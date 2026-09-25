"""The strategies that fetch evidence themselves rather than wrapping another.

* :class:`SimpleRAG` -- hybrid dense + lexical retrieval over the corpus. Every other strategy
  eventually bottoms out here.
* :class:`MemoryRAG` -- what the user told us, as opposed to what the corpus says.
* :class:`GraphRAG` -- multi-hop traversal, for questions about relationships rather than content.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from ..domain.graph import parse_source
from ..domain.identifiers import ChunkId, DocumentId
from ..domain.knowledge import Chunk, RetrievalResult, RetrievedChunk, TrustTier
from ..domain.memory import SHARED_MEMORY_SCOPE, MemoryKind, MemoryQuery
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
                        # A retrieval request carries no persona, so it cannot know whose
                        # private scope it may read: shared only (GAP-024 memory scopes).
                        scopes=(SHARED_MEMORY_SCOPE,),
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
    this project contain" -- where the answer is a path rather than a passage. Vector search is poor
    at these: the sentence stating a dependency may not lexically or semantically resemble the
    question, and the answer often requires composing two facts from different documents.

    Traversal is delegated to :meth:`GraphStore.neighbours`, which already does bounded
    breadth-first expansion and clamps depth internally -- an unbounded walk driven by user input is
    a denial-of-service primitive, and re-implementing the loop here would duplicate that cap while
    being able to disagree with it.

    Paths come back in ``outcome.graph_paths`` as ``(subject, predicate, object)`` triples of entity
    *names* rather than canonical keys, because the point of returning them is that a person can
    read the derivation instead of taking a conclusion on trust.

    ``max_hops`` defaults to 2. Three hops on a well-connected graph reaches most of it, producing a
    context full of loosely-related edges -- worse than not using the graph at all.

    **The facts also travel as chunks.** The kernel reads only ``RetrievalResult.chunks`` through
    the ``StrategyFacade``, so ``graph_paths`` alone never reached a prompt. With ``emit_chunks``
    (the default) the traversed edges are rendered as one synthetic chunk *per source document*,
    placed ahead of the passages. Grouping by source keeps provenance honest -- the chunk's citation
    names the document version the facts were extracted from -- and, being ordinary chunks, they go
    through the kernel's context guard and are fenced as DATA like any retrieved passage (ADR-006).
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
        min_confidence: float = 0.0,
        emit_chunks: bool = True,
        max_fact_chunks: int = 2,
        metrics: Any = None,
    ) -> None:
        super().__init__(metrics=metrics)
        self._emit_chunks = emit_chunks
        self._max_fact_chunks = max_fact_chunks
        self._graph = graph
        self._inner = inner
        self._max_hops = max_hops
        self._max_entities = max_entities
        self._max_paths = max_paths
        # Relations carry confidence because a surface-pattern match is evidence, not proof. A
        # caller answering something consequential can require a higher bar.
        self._min_confidence = min_confidence

    #: Words that are never entity mentions. Short and question-shaped: a long stop list would
    #: start removing words that are part of real entity names ("Bank of England").
    _QUERY_NOISE = frozenset({
        "which", "what", "who", "where", "when", "why", "how", "does", "do", "did", "is",
        "are", "was", "were", "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
        "depend", "depends", "related", "connected", "between", "tell", "me", "about", "show",
        "list", "all", "any", "service", "services",
    })

    async def _seed_entities(self, request: RetrievalRequest) -> list[Any]:
        """Find the entities a query mentions.

        ``find_entities`` matches its ``name`` argument as a substring *of* an entity name, so
        passing the whole query almost never matches: "which services depend on Auth Service" is
        not a substring of "Auth Service". Passing the query verbatim was a real bug -- it only
        appeared to work in tests that queried with a bare entity name.

        Three passes, cheapest first, stopping as soon as one finds anything:

        1. **The query as-is.** Correct and one round trip for the common short form -- a user
           typing just "Auth Service".
        2. **Capitalised phrases.** Entity mentions in English prose are overwhelmingly proper
           nouns, and this recovers the multi-word ones intact, which matters because "Auth" alone
           would also match "Auth Audit Log".
        3. **Individual significant words.** The fallback for lowercase corpora and for queries
           whose capitalisation the user did not preserve.
        """
        seeds: list[Any] = []
        seen: set[str] = set()

        async def lookup(term: str) -> None:
            if len(seeds) >= self._max_entities or len(term) < 3:
                return
            found = await self._graph.find_entities(
                owner_id=request.owner_id, name=term, limit=self._max_entities
            )
            for entity in found:
                if entity.key not in seen:
                    seen.add(entity.key)
                    seeds.append(entity)

        await lookup(request.query.strip())
        if seeds:
            return seeds[: self._max_entities]

        for phrase in re.findall(r"\b[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*", request.query):
            await lookup(phrase.strip())
        if seeds:
            return seeds[: self._max_entities]

        for word in re.findall(r"\b\w{3,}\b", request.query):
            if word.lower() not in self._QUERY_NOISE:
                await lookup(word)
        return seeds[: self._max_entities]

    async def _run(self, request: RetrievalRequest) -> RetrievalOutcome:
        notes: list[str] = []
        paths: list[tuple[str, ...]] = []

        try:
            seeds = await self._seed_entities(request)
        except Exception as exc:
            seeds = []
            notes.append(f"graph unavailable: {type(exc).__name__}")

        if not seeds:
            notes.append("no matching entities")

        # Resolve keys to names once, so triples read as prose. Seeds are already Entity objects;
        # neighbours() returns the entities it touched alongside the relations, which is exactly
        # what makes this possible without a lookup per edge.
        names: dict[str, str] = {entity.key: entity.name for entity in seeds}
        seen_edges: set[tuple[str, str, str]] = set()
        traversed: list[Any] = []

        for entity in seeds:
            if len(paths) >= self._max_paths:
                notes.append("path limit reached")
                break
            try:
                neighbour_entities, relations = await self._graph.neighbours(
                    entity.key,
                    owner_id=request.owner_id,
                    depth=self._max_hops,
                    limit=self._max_paths,
                )
            except Exception as exc:
                notes.append(f"traversal failed at {entity.key!r}: {type(exc).__name__}")
                continue

            names.update({e.key: e.name for e in neighbour_entities})

            for relation in relations:
                if relation.confidence < self._min_confidence:
                    continue
                triple_key = (relation.subject_key, relation.predicate, relation.object_key)
                # The same edge is reachable from both of its endpoints, and seeds often overlap.
                if triple_key in seen_edges:
                    continue
                seen_edges.add(triple_key)
                traversed.append(relation)
                paths.append(
                    (
                        names.get(relation.subject_key, relation.subject_key),
                        relation.predicate,
                        names.get(relation.object_key, relation.object_key),
                    )
                )
                if len(paths) >= self._max_paths:
                    notes.append("path limit reached")
                    break

        result = RetrievalResult(query=request.query, strategy=self.name)
        children: tuple[StrategyTrace, ...] = ()
        if self._inner is not None:
            # Graph edges answer "how are these connected"; passages answer "what does it say". A
            # multi-hop question usually needs both, so the corpus pass is not optional here.
            inner_outcome = await self._inner.retrieve(request)
            result = inner_outcome.result
            children = (inner_outcome.trace,)

        fact_chunks = self._fact_chunks(traversed, names, request) if self._emit_chunks else ()
        if fact_chunks:
            passages = tuple(
                c for c in result.chunks
                if c.chunk.id.value not in {f.chunk.id.value for f in fact_chunks}
            )
            result = replace(result, chunks=fact_chunks + passages, strategy=result.strategy)
            notes.append(f"fact chunks={len(fact_chunks)}")

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

    def _fact_chunks(
        self, relations: list[Any], names: dict[str, str], request: RetrievalRequest
    ) -> tuple[RetrievedChunk, ...]:
        """Render traversed edges as one chunk per source document, strongest group first."""
        groups: dict[str, list[Any]] = {}
        for relation in relations:
            source = relation.sources[0] if relation.sources else ""
            groups.setdefault(source, []).append(relation)

        rendered: list[RetrievedChunk] = []
        for source, members in groups.items():
            members.sort(key=lambda r: r.confidence, reverse=True)
            document_id, version = parse_source(source)
            lines = [
                f"{names.get(r.subject_key, r.subject_key)} {r.predicate.replace('_', ' ')} "
                f"{names.get(r.object_key, r.object_key)} (confidence {r.confidence:.2f})."
                for r in members
            ]
            doc = DocumentId(document_id or source or "knowledge-graph")
            chunk = Chunk(
                id=ChunkId(f"{doc.value}:v{version or 0}:graph"),
                document_id=doc,
                document_version=version or 0,
                index=0,
                content="Knowledge-graph facts: " + " ".join(lines),
                heading_path=("knowledge graph",),
                owner_id=request.owner_id,
                trust_tier=TrustTier.COMMUNITY,
                metadata={"modality": "graph", "source": source, "facts": len(members)},
            )
            rendered.append(RetrievedChunk(chunk=chunk, score=members[0].confidence))
        rendered.sort(key=lambda c: c.score, reverse=True)
        return tuple(rendered[: self._max_fact_chunks])
