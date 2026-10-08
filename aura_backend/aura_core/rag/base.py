"""The retrieval-strategy contract.

Ten strategies exist because different questions need different retrieval, not because more is
better. "What is our refund policy?" wants one lexical-plus-dense pass. "What did I tell you about
this project last week?" wants memory. "How do these three services depend on each other?" wants
graph traversal. Running all ten on every turn would multiply latency and local-model load for no
gain -- so each is a separate object, and something decides which to run
(:class:`~aura_core.rag.adaptive.AdaptiveRouter`).

**Every strategy is a decorator or a leaf, and they compose.** ``CorrectiveRAG(MultiQueryRAG(
SimpleRAG(...)))`` is a legitimate configuration and needs no new code, which is the property that
keeps this from becoming a pile of branches inside one ``retrieve`` method.

Three rules every implementation follows:

1. **An empty result is a real answer.** A strategy that finds nothing relevant returns nothing,
   with ``candidates_considered`` recording that it looked. The legacy pipeline always returned
   top-k labelled "Relevant Context", which is how a model ends up grounding a confident answer in
   noise.
2. **Failure degrades to the wrapped strategy, never to silence.** A strategy that needs a
   generation (Multi-Query, HyDE) and cannot get one falls back to its inner strategy and records
   that it did. Losing the enhancement is acceptable; losing retrieval is not.
3. **Cost is reported, not hidden.** ``generations`` and ``duration_ms`` come back on the trace, so
   the adaptive router and the operator can both see what a strategy actually cost. On a 7B model
   on CPU, one extra generation is not a rounding error.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.identifiers import SessionId, UserId
from ..domain.knowledge import RetrievalResult, RetrievedChunk

__all__ = [
    "RetrievalRequest",
    "StrategyTrace",
    "RetrievalOutcome",
    "RetrievalStrategy",
    "fuse_results",
]


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """Everything a strategy may need, so no strategy reaches for ambient state.

    Passing the request rather than letting strategies read from a session object is what makes
    them independently testable: a test constructs a request, not a conversation.
    """

    query: str
    owner_id: UserId | None = None
    limit: int | None = None
    include_global: bool = True
    score_threshold: float | None = None
    filters: Mapping[str, Any] | None = None
    session_id: SessionId | None = None

    #: Conversation history, most recent last, as ``(role, content)``. Used by strategies that
    #: rewrite the query -- a follow-up like "and the second one?" is unretrievable on its own.
    history: tuple[tuple[str, str], ...] = ()

    #: Budget for strategies that would otherwise generate without limit. Zero means "no
    #: generations permitted", which downgrades Multi-Query and HyDE to their inner strategy
    #: rather than failing -- the honest behaviour when a caller cannot afford them.
    max_generations: int = 2

    def with_query(self, query: str) -> "RetrievalRequest":
        return replace(self, query=query)


@dataclass(frozen=True, slots=True)
class StrategyTrace:
    """What ran, what it cost, and why.

    Recorded per strategy and nested, so ``Corrective(MultiQuery(Simple))`` produces a tree an
    operator can read. Without this, a slow turn is a mystery: the only visible fact would be that
    retrieval took 900ms.
    """

    name: str
    duration_ms: float = 0.0
    generations: int = 0
    candidates: int = 0
    returned: int = 0
    #: Free-form, deliberately short. Records decisions, not narration: "reformulated",
    #: "degraded: no generation budget", "insufficient evidence".
    notes: tuple[str, ...] = ()
    children: tuple["StrategyTrace", ...] = ()

    def with_note(self, note: str) -> "StrategyTrace":
        return replace(self, notes=self.notes + (note,))

    @property
    def total_generations(self) -> int:
        return self.generations + sum(c.total_generations for c in self.children)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2),
            "generations": self.generations,
            "candidates": self.candidates,
            "returned": self.returned,
            "notes": list(self.notes),
            "children": [c.as_dict() for c in self.children],
        }


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    """A :class:`RetrievalResult` plus how it was obtained.

    Separate from RetrievalResult because that type is the domain's answer to "what did we find",
    and this adds "and what did it take". Keeping them apart means the context assembler and the
    citation builder do not have to know strategies exist.
    """

    result: RetrievalResult
    trace: StrategyTrace

    #: Memory hits, when a strategy consulted memory. Kept separate from ``result.chunks``
    #: because a memory is not a document chunk: it has no citation, and presenting one as a
    #: source would attribute the user's own statement to the corpus.
    memories: tuple[Any, ...] = ()

    #: Graph paths, when a strategy traversed the graph. ``(subject, predicate, object)`` triples
    #: in traversal order, so a multi-hop answer can show its reasoning.
    graph_paths: tuple[tuple[str, ...], ...] = ()

    @property
    def chunks(self) -> tuple[RetrievedChunk, ...]:
        return self.result.chunks

    def __bool__(self) -> bool:
        return bool(self.result.chunks or self.memories or self.graph_paths)

    @property
    def is_empty_but_searched(self) -> bool:
        """Distinguishes "nothing relevant exists" from "retrieval never ran".

        The caller must be able to tell these apart: the first is an answer the model should
        convey, the second is a fault it should not paper over.
        """
        return not bool(self) and self.trace.candidates > 0


@runtime_checkable
class RetrievalStrategy(Protocol):
    """One way of finding evidence for a query."""

    name: str

    async def retrieve(self, request: RetrievalRequest) -> RetrievalOutcome:
        ...


class BaseStrategy:
    """Timing and trace plumbing, so implementations contain only their own logic."""

    name = "base"

    def __init__(self, *, metrics: Any = None) -> None:
        self._metrics = metrics

    async def retrieve(self, request: RetrievalRequest) -> RetrievalOutcome:
        started = time.perf_counter()
        outcome = await self._run(request)
        duration = (time.perf_counter() - started) * 1000.0
        trace = replace(outcome.trace, duration_ms=duration)
        if self._metrics is not None:
            counter = getattr(self._metrics, "rag_strategy_total", None)
            if counter is not None:
                counter.labels(
                    strategy=self.name, result="hit" if outcome else "empty"
                ).inc()
        return replace(outcome, trace=trace)

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:  # pragma: no cover
        raise NotImplementedError


def fuse_results(
    results: Sequence[RetrievalResult], *, k: int = 60, limit: int | None = None
) -> RetrievalResult:
    """Reciprocal-rank fusion over several retrievals of the same query.

    Used by Multi-Query and by the adaptive router when it runs strategies in parallel. RRF rather
    than score averaging because the inputs are not on a comparable scale: a dense cosine score, a
    BM25 score and a rerank score mean different things, and averaging them lets whichever has the
    widest range dominate. Rank position is the one signal they share.

    ``k=60`` matches the retrieval engine's own fusion constant, so a chunk ranked third by two
    strategies is weighted here exactly as it would be there.
    """
    if not results:
        return RetrievalResult(strategy="fused")
    if len(results) == 1:
        return results[0]

    scores: dict[str, float] = {}
    best: dict[str, RetrievedChunk] = {}
    considered = 0
    for result in results:
        considered += result.candidates_considered
        for rank, retrieved in enumerate(result.chunks, start=1):
            key = retrieved.chunk.id.value
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            # Keep the instance with the highest original score: its dense/lexical/rerank
            # components are the most informative for a citation and for debugging.
            if key not in best or retrieved.score > best[key].score:
                best[key] = retrieved

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if limit is not None:
        ordered = ordered[:limit]

    # The fused score is an RRF score, not a similarity. Overwriting `score` would make it
    # incomparable with the threshold the engine applied, so the chunk keeps its own score and the
    # fusion only decides order.
    chunks = tuple(best[key] for key, _ in ordered)

    below: list[RetrievedChunk] = []
    seen = {c.chunk.id.value for c in chunks}
    for result in results:
        for retrieved in result.below_threshold:
            if retrieved.chunk.id.value not in seen:
                below.append(retrieved)
                seen.add(retrieved.chunk.id.value)

    return RetrievalResult(
        chunks=chunks,
        query=results[0].query,
        candidates_considered=considered,
        below_threshold=tuple(below[:10]),
        strategy="fused",
        duration_ms=max(r.duration_ms for r in results),
    )
