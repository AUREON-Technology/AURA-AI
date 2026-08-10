"""The strategies that fetch evidence themselves rather than wrapping another.

* :class:`SimpleRAG` -- hybrid dense + lexical retrieval over the corpus. Every other strategy
  eventually bottoms out here.
* :class:`MemoryRAG` -- what the user told us, as opposed to what the corpus says.
* :class:`GraphRAG` -- multi-hop traversal, for questions about relationships rather than content.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..domain.knowledge import RetrievalResult
from ..domain.memory import MemoryKind, MemoryQuery
from .base import BaseStrategy, RetrievalOutcome, RetrievalRequest, StrategyTrace

__all__ = ["SimpleRAG", "MemoryRAG", "GraphRAG"]


class SimpleRAG(BaseStrategy):
    """Hybrid retrieval over the document corpus.

    A thin adapter over :meth:`KnowledgeEngine.retrieve`, which already does dense + BM25 with RRF
    fusion, heuristic reranking, a relevance floor and MMR diversification. This class exists to
    put that behind the strategy interface so the composing strategies have something uniform to
    wrap -- not to reimplement any of it.

    The floor is what makes an empty result meaningful, and it is inherited rather than restated:
    if the engine returns nothing above threshold, so does this.
    """

    name = "simple"

    def __init__(self, knowledge: Any, *, metrics: Any = None) -> None:
        super().__init__(metrics=metrics)
        self._knowledge = knowledge

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        result = await self._knowledge.retrieve(
            request.query,
            owner_id=request.owner_id,
            limit=request.limit,
            include_global=request.include_global,
            score_threshold=request.score_threshold,
            filters=request.filters,
        )
        return RetrievalOutcome(
            result=result,
            trace=StrategyTrace(
                name=self.name,
                candidates=result.candidates_considered,
                returned=len(result.chunks),
            ),
        )


class MemoryRAG(BaseStrategy):
    """Recall from the user's own memories, optionally alongside corpus retrieval.

    Memories are returned in ``outcome.memories``, never merged into ``result.chunks``. That
    separation matters for attribution: a memory is something the user said, and rendering it as a
    retrieved source would cite the corpus for the user's own statement -- which reads to the user
    as the system inventing a document.

    ``also_search`` defaults true because most memory-flavoured questions still benefit from the
    corpus ("what did I say about the refund policy" needs both). Set false for questions that are
    purely autobiographical, where corpus hits are noise.
    """

    name = "memory"

    def __init__(
        self,
        memory: Any,
        inner: Any = None,
        *,
        kinds: tuple[MemoryKind, ...] = (
            MemoryKind.SEMANTIC,
            MemoryKind.EPISODIC,
            MemoryKind.ENTITY,
            MemoryKind.PROCEDURAL,
        ),
        limit: int = 8,
        also_search: bool = True,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._memory = memory
        self._inner = inner
        self._kinds = kinds
        self._limit = limit
        self._also_search = also_search

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        notes: list[str] = []
        memories: tuple[Any, ...] = ()

        if request.owner_id is None:
            # Memory is owner-scoped by construction; there is no such thing as a global memory.
            # Recording the skip rather than silently returning nothing keeps a caller from
            # concluding the user has no memories when in fact none were requested.
            notes.append("skipped: no owner")
        else:
            try:
                hits = await self._memory.recall(
                    MemoryQuery(
                        owner_id=request.owner_id,
                        text=request.query,
                        kinds=self._kinds,
                        session_id=request.session_id,
                        limit=self._limit,
                    )
                )
                memories = tuple(hits)
            except Exception as exc:
                # Memory being unavailable must not fail the turn: the corpus half is still
                # useful, and a user asking a factual question does not care that recall broke.
                notes.append(f"memory unavailable: {type(exc).__name__}")

        result = RetrievalResult(query=request.query, strategy=self.name)
        children: tuple[StrategyTrace, ...] = ()
        if self._also_search and self._inner is not None:
            inner_outcome = await self._inner.retrieve(request)
            result = inner_outcome.result
            children = (inner_outcome.trace,)

        return RetrievalOutcome(
            result=result,
            memories=memories,
            trace=StrategyTrace(
                name=self.name,
                candidates=result.candidates_considered + len(memories),
                returned=len(result.chunks) + len(memories),
                notes=tuple(notes),
                children=children,
            ),
        )


class GraphRAG(BaseStrategy):
    """Multi-hop retrieval over the knowledge graph.

    For questions about *relationships* -- "which services depend on the auth module", "what does
    this project contain" -- where the answer is a path rather than a passage. Vector search is
    poor at these: the sentence stating a dependency may not lexically or semantically resemble the
    question, and the answer often requires composing two facts that appear in different documents.

    Entities are seeded from the query, then expanded breadth-first to ``max_hops``. Paths are
    returned in ``outcome.graph_paths`` as triples, so the answer can show its derivation instead
    of asserting a conclusion the user cannot check.

    ``max_hops`` defaults to 2. Three hops on a well-connected graph reaches most of it, which
    produces a context full of loosely-related edges -- worse than not using the graph.
    """

    name = "graph"

    def __init__(
        self,
        graph: Any,
        inner: Any = None,
        *,
        max_hops: int = 2,
        max_entities: int = 6,
        max_paths: int = 24,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._graph = graph
        self._inner = inner
        self._max_hops = max_hops
        self._max_entities = max_entities
        self._max_paths = max_paths

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        notes: list[str] = []
        paths: list[tuple[str, ...]] = []

        try:
            seeds = await self._graph.find_entities(
                request.query, owner_id=request.owner_id, limit=self._max_entities
            )
        except Exception as exc:
            seeds = []
            notes.append(f"graph unavailable: {type(exc).__name__}")

        if not seeds:
            notes.append("no matching entities")

        # Breadth-first, tracking visited keys so a cycle terminates. Graphs extracted from prose
        # routinely contain cycles (A depends on B, B documented in A), and without this the
        # traversal would not stop.
        frontier = [(entity.key if hasattr(entity, "key") else str(entity), ()) for entity in seeds]
        visited: set[str] = set()

        for _hop in range(self._max_hops):
            if not frontier or len(paths) >= self._max_paths:
                break
            next_frontier: list[tuple[str, tuple[str, ...]]] = []
            for key, prefix in frontier:
                if key in visited:
                    continue
                visited.add(key)
                try:
                    edges = await self._graph.neighbours(key, owner_id=request.owner_id)
                except Exception as exc:
                    notes.append(f"traversal failed at {key!r}: {type(exc).__name__}")
                    continue
                for edge in edges:
                    subject = getattr(edge, "subject", None) or key
                    predicate = getattr(edge, "predicate", "related_to")
                    obj = getattr(edge, "object", None) or getattr(edge, "target", "")
                    triple = (str(subject), str(predicate), str(obj))
                    paths.append(prefix + triple if prefix else triple)
                    if len(paths) >= self._max_paths:
                        break
                    if str(obj) not in visited:
                        next_frontier.append((str(obj), triple))
                if len(paths) >= self._max_paths:
                    break
            frontier = next_frontier

        if len(paths) >= self._max_paths:
            notes.append("path limit reached")

        result = RetrievalResult(query=request.query, strategy=self.name)
        children: tuple[StrategyTrace, ...] = ()
        if self._inner is not None:
            # Graph edges answer "how are these connected"; the passages answer "what does it say".
            # A multi-hop question usually needs both, so the corpus pass is not optional here.
            inner_outcome = await self._inner.retrieve(request)
            result = inner_outcome.result
            children = (inner_outcome.trace,)

        return RetrievalOutcome(
            result=result,
            graph_paths=tuple(paths),
            trace=StrategyTrace(
                name=self.name,
                candidates=result.candidates_considered + len(paths),
                returned=len(result.chunks) + len(paths),
                notes=tuple(notes),
                children=children,
            ),
        )
