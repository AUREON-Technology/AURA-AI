"""Knowledge engine: ingestion and hybrid retrieval.

The two operations the platform's RAG capability reduces to.

**Ingest** -- version the document, chunk it structurally, guard every chunk, embed in batches,
write to the versioned index and the lexical index, and extract graph entities. Idempotent on
content hash, so re-ingesting unchanged content is a no-op and the nightly refresh does not churn
the index.

**Retrieve** -- run dense and lexical search concurrently, fuse by RRF, rerank, apply a score
floor, diversify with MMR, and return chunks that carry their citations. The score floor is the
single most important correctness property: when nothing is relevant, this returns nothing, and
the kernel says so. The legacy pipeline always returns top-k and labels it "Relevant Context",
which is how a model ends up grounding a confident answer in noise.
"""

from __future__ import annotations

from dataclasses import replace

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Hashable, Mapping, Sequence

from ..domain.errors import ConflictError, EmbeddingError, KnowledgeError, ValidationError
from ..domain.events import DocumentIngested, DocumentSuperseded
from ..domain.identifiers import DocumentId, UserId
from ..domain.knowledge import (
    Chunk,
    Document,
    IndexIdentity,
    RetrievalResult,
    RetrievedChunk,
    SourceType,
    TrustTier,
    build_chunks,
)
from ..observability.logging import get_logger
from ..ports.llm import EmbeddingProvider, Reranker
from ..ports.storage import GLOBAL_OWNER, DocumentStore, VectorPoint, VectorStore
from .chunking import ChunkingConfig, chunk_with_headings
from .documents import ExtractedBlock
from .graph_pipeline import KnowledgeGraphPipeline
from .index_manager import IndexManager
from .retrieval import BM25Index, HeuristicReranker, reciprocal_rank_fusion

__all__ = ["KnowledgeEngine", "IngestionResult", "derive_document_id"]

logger = get_logger("knowledge")

#: Chunk-metadata keys that must survive the vector-payload round trip. Deliberately a fixed list
#: rather than "copy everything not already a field": the payload also carries the content and the
#: owner, and echoing those back into metadata would duplicate them on every retrieved chunk.
_PROVENANCE_KEYS = ("modality", "page", "needs_ocr")

# Scores relevance when the configured reranker is disabled or fails (see `_rerank`). Stateless.
_FALLBACK_SCORER = HeuristicReranker()


def derive_document_id(title: str, owner_id: UserId | None) -> DocumentId:
    """The id a document gets when the caller does not name one.

    A pure function of ``(owner, title)``. It used to be ``hash(title)``, which is salted per
    interpreter (``PYTHONHASHSEED``): the same upload retried on another gateway worker, a Celery
    worker, or after a restart got a different id, so the retry could neither find nor overwrite
    the first attempt's chunks -- and, being unscoped, two users uploading "Notes" collided on one
    id and the second upload superseded the first user's document.
    """
    scope = owner_id.value if owner_id is not None else ""
    digest = hashlib.sha256(f"{scope}\x00{title}".encode("utf-8")).hexdigest()[:20]
    return DocumentId(f"doc-{digest}")


class _KeyedLocks:
    """One ``asyncio.Lock`` per key, dropped once nobody holds or awaits it."""

    def __init__(self) -> None:
        self._locks: dict[Hashable, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: Hashable) -> AsyncIterator[None]:
        lock, users = self._locks.get(key, (None, 0))
        if lock is None:
            lock = asyncio.Lock()
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self._locks[key]
            if users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)


def _owner_value(owner_id: UserId | None) -> str | None:
    return owner_id.value if owner_id is not None else None


def _owner_key(owner_id: UserId | None) -> str:
    """The lexical index group a chunk lives in: its owner, or the shared global group."""
    return owner_id.value if owner_id is not None else GLOBAL_OWNER


class IngestionResult:
    __slots__ = ("document", "chunks", "created", "quarantined", "duration_ms", "skipped_reason")

    def __init__(
        self,
        *,
        document: Document | None,
        chunks: int = 0,
        created: bool = False,
        quarantined: int = 0,
        duration_ms: float = 0.0,
        skipped_reason: str | None = None,
    ) -> None:
        self.document = document
        self.chunks = chunks
        self.created = created
        self.quarantined = quarantined
        self.duration_ms = duration_ms
        self.skipped_reason = skipped_reason

    def __bool__(self) -> bool:
        return self.created


class KnowledgeEngine:
    def __init__(
        self,
        *,
        documents: DocumentStore,
        vectors: VectorStore,
        embedder: EmbeddingProvider,
        index_manager: IndexManager,
        reranker: Reranker | None = None,
        safety: Any = None,
        graph: Any = None,
        extractor: Any = None,
        chunking: ChunkingConfig | None = None,
        top_k: int = 2,
        candidate_k: int = 24,
        score_threshold: float = 0.35,
        hybrid_enabled: bool = True,
        rerank_enabled: bool = True,
        rrf_k: int = 60,
        mmr_lambda: float = 0.7,
        max_document_bytes: int = 8 * 1024 * 1024,
        metrics: Any = None,
        event_bus: Any = None,
        graph_pipeline: KnowledgeGraphPipeline | None = None,
    ) -> None:
        self._documents = documents
        self._vectors = vectors
        self._embedder = embedder
        self._indexes = index_manager
        self._reranker = reranker or HeuristicReranker()
        self._safety = safety
        self._graph = graph
        self._extractor = extractor
        # Every fact reaches the graph through the pipeline (normalise, validate, provenance,
        # calibrate, dedupe) -- never as the extractor's raw output (ADR-005, "Knowledge graph").
        self._graph_pipeline = graph_pipeline or (
            KnowledgeGraphPipeline(graph=graph, extractor=extractor)
            if graph is not None and extractor is not None
            else None
        )
        self._chunking = chunking or ChunkingConfig()
        self._top_k = top_k
        self._candidate_k = candidate_k
        self._threshold = score_threshold
        self._hybrid = hybrid_enabled
        self._rerank_enabled = rerank_enabled
        self._rrf_k = rrf_k
        self._mmr_lambda = mmr_lambda
        self._max_bytes = max_document_bytes
        self._metrics = metrics
        self._events = event_bus

        # In-process lexical index. Rebuilt from the document store on startup; the Postgres FTS
        # adapter replaces this when a deployment outgrows it (ADR-005). Chunks are grouped by
        # owner key (`_owner_key`), so a query reads only its tenant's and the global postings.
        self._lexical = BM25Index()
        # Owner key -> the store's corpus revision this worker's shard for it last matched. Each
        # gateway worker holds its own index over one shared store, so a write served by another
        # worker reaches this one only through these revisions (F-14): `_converge_lexical`
        # compares them before every lexical search and refreshes the shards that moved. A key
        # that is absent has never been checked, which always triggers a refresh.
        self._lexical_revisions: dict[str, int] = {}
        self._lexical_refresh_locks = _KeyedLocks()

        # Serialises the check-then-write section of `ingest` per content hash and per document
        # id. Without it, concurrent duplicate submissions (a double tap, a client retry racing its
        # original) all pass the content-hash check before any of them writes, and concurrent new
        # versions of one document all compute the same next version number and overwrite each
        # other. In-process only: across gateway workers the `Idempotency-Key` header is what
        # absorbs a retried request (roadmap P1.4).
        self._ingest_locks = _KeyedLocks()

    # -- ingestion ---------------------------------------------------------------------

    async def ingest(
        self,
        *,
        content: str = "",
        title: str,
        document_id: DocumentId | None = None,
        owner_id: UserId | None = None,
        source_type: SourceType = SourceType.UPLOAD,
        source_uri: str | None = None,
        trust_tier: TrustTier = TrustTier.COMMUNITY,
        metadata: Mapping[str, Any] | None = None,
        language: str | None = None,
        segments: Sequence[ExtractedBlock] | None = None,
    ) -> IngestionResult:
        """Chunk, guard, embed and index a document.

        ``segments`` is the structured form, produced by
        :func:`aura_core.knowledge.documents.extract_pdf`. Passing it does two things a single
        ``content`` string cannot: each block is chunked on its own, so no chunk straddles a page
        boundary, and each block's ``modality`` and ``page`` follow its chunks into the vector
        payload -- which is what makes ``MultimodalRAG``'s filter select real content.

        ``content`` remains the plain-text path and stays the default. When only ``segments`` is
        given, the document text is derived from it.
        """
        started = time.perf_counter()
        if not content and segments:
            content = "\n\n".join(b.text for b in segments if b.text)
        text = content.strip()
        if not text:
            raise ValidationError("cannot ingest empty content")
        if len(content.encode("utf-8")) > self._max_bytes:
            raise ValidationError(
                "document exceeds the configured size limit",
                details={"limit_bytes": self._max_bytes},
            )

        doc_id = document_id or derive_document_id(title, owner_id)

        candidate = Document(
            id=doc_id, version=1, title=title, content=text, source_type=source_type,
            source_uri=source_uri, owner_id=owner_id, trust_tier=trust_tier,
            language=language, metadata=dict(metadata or {}),
        )
        owner_key = owner_id.value if owner_id is not None else None
        # Always hash-lock first, then id-lock: one global acquisition order, so two ingests can
        # never each hold the lock the other is waiting for.
        async with self._ingest_locks.hold(("hash", owner_key, candidate.content_hash)):
            async with self._ingest_locks.hold(("document", doc_id.value)):
                return await self._ingest_serialised(
                    candidate, text=text, segments=segments, metadata=metadata,
                    started=started,
                )

    async def _ingest_serialised(
        self,
        candidate: Document,
        *,
        text: str,
        segments: Sequence[ExtractedBlock] | None,
        metadata: Mapping[str, Any] | None,
        started: float,
    ) -> IngestionResult:
        doc_id, owner_id, title = candidate.id, candidate.owner_id, candidate.title
        source_type = candidate.source_type

        # Idempotency on content hash. Without this the nightly refresh creates a new version of
        # every unchanged document each night, which multiplies storage and forces a needless
        # reindex (ADR-008 step 1).
        existing_by_hash = await self._documents.get_by_hash(
            candidate.content_hash, owner_id=owner_id
        )
        if existing_by_hash is not None:
            return IngestionResult(
                document=existing_by_hash,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                skipped_reason="unchanged_content",
            )

        previous = await self._documents.get(doc_id)
        if previous is not None and _owner_value(previous.owner_id) != _owner_value(owner_id):
            # `get` is not owner-scoped, and versioning an existing id keeps the *existing* owner, so
            # without this an explicit `document_id` naming someone else's (or a global) document
            # would write the caller's content straight into their knowledge base -- an integrity
            # bug and an injection delivery channel at once (tests/security/test_idor.py,
            # tests/unit/test_ingestion_idempotency.py).
            raise ConflictError(
                "document_id is already in use", details={"document_id": doc_id.value}
            )
        document = (
            previous.next_version(
                text,
                title=title,
                metadata=dict(metadata or {}),
                cag_pinned=previous.cag_pinned,
            )
            if previous is not None
            else candidate
        )

        pieces, piece_metadata = self._chunk_pieces(text, segments)
        if not pieces:
            raise KnowledgeError("chunking produced no content", details={"document": doc_id.value})

        quarantined = 0
        if self._safety is not None:
            accepted_pieces: list[tuple[str, tuple[str, ...]]] = []
            accepted_metadata: list[Mapping[str, Any]] = []
            scanned = await self._safety.guard_context_items(
                [(f"{doc_id.value}#{i}", piece) for i, (piece, _path) in enumerate(pieces)]
            )
            accepted, rejected, _tainted = scanned
            accepted_text = {provenance: cleaned for provenance, cleaned in accepted}
            for index, (piece, path) in enumerate(pieces):
                key = f"{doc_id.value}#{index}"
                if key in accepted_text:
                    accepted_pieces.append((accepted_text[key], path))
                    # Filtered in lockstep. Keeping the two lists index-aligned is the whole
                    # correctness requirement here: drop one without the other and every surviving
                    # chunk after the first quarantined one reports the wrong page.
                    if piece_metadata:
                        accepted_metadata.append(piece_metadata[index])
                else:
                    quarantined += 1
            pieces = accepted_pieces
            piece_metadata = accepted_metadata
            if not pieces:
                # Every chunk was instruction-shaped. Ingesting nothing is correct; recording
                # why is what makes it diagnosable.
                logger.warning(
                    "document fully quarantined during ingestion",
                    extra={"document": doc_id.value, "chunks": quarantined},
                )
                await self._publish(
                    DocumentIngested(
                        document_id=doc_id, version=document.version, chunks=0,
                        source_type=str(source_type), owner_id=owner_id, quarantined=True,
                    )
                )
                return IngestionResult(
                    document=document, quarantined=quarantined,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    skipped_reason="fully_quarantined",
                )

        chunks = build_chunks(document, pieces, chunk_metadata=piece_metadata or None)

        identity = await self._indexes.ensure_ready()
        await self._embed_and_write(chunks, identity)

        await self._documents.put(document, chunks)
        if previous is not None:
            await self._documents.supersede(
                doc_id, old_version=previous.version, new_version=document.version
            )
            await self._retire_chunks(previous, identity)
            await self._publish(
                DocumentSuperseded(
                    document_id=doc_id, old_version=previous.version, new_version=document.version
                )
            )

        for chunk in chunks:
            self._lexical.add(chunk.id.value, chunk.content, group=_owner_key(chunk.owner_id))

        if self._graph_pipeline is not None:
            # Extract from what survived the context guard, not the raw upload: a quarantined
            # (instruction-shaped) chunk must not reach long-term knowledge through the graph
            # after being kept out of the index. With nothing quarantined this is the full text.
            graph_text = text if quarantined == 0 else "\n\n".join(p for p, _ in pieces)
            await self._extract_graph(
                document, graph_text, owner_id, superseded=previous is not None
            )

        duration = (time.perf_counter() - started) * 1000.0
        if self._metrics is not None:
            self._metrics.documents_ingested_total.labels(
                source_type=str(source_type), outcome="ok"
            ).inc()
        await self._publish(
            DocumentIngested(
                document_id=doc_id, version=document.version, chunks=len(chunks),
                source_type=str(source_type), owner_id=owner_id, quarantined=quarantined > 0,
            )
        )
        logger.info(
            "document ingested",
            extra={"document": doc_id.value, "version": document.version,
                   "chunks": len(chunks), "quarantined": quarantined,
                   "ms": round(duration, 1)},
        )
        return IngestionResult(
            document=document, chunks=len(chunks), created=True,
            quarantined=quarantined, duration_ms=duration,
        )

    def _chunk_pieces(
        self, text: str, segments: Sequence[ExtractedBlock] | None
    ) -> tuple[list[tuple[str, tuple[str, ...]]], list[Mapping[str, Any]]]:
        """Chunk either the whole text or each extracted block, and keep per-piece provenance.

        Returns ``(pieces, metadata)`` index-aligned, with ``metadata`` empty for the plain-text
        path so ``build_chunks`` behaves exactly as it did before segments existed.
        """
        if not segments:
            return chunk_with_headings(text, self._chunking), []

        pieces: list[tuple[str, tuple[str, ...]]] = []
        metadata: list[Mapping[str, Any]] = []
        for block in segments:
            if not block.text.strip():
                continue
            block_meta = block.chunk_metadata
            for piece in chunk_with_headings(block.text, self._chunking):
                pieces.append(piece)
                metadata.append(block_meta)
        return pieces, metadata

    async def _embed_and_write(self, chunks: Sequence[Chunk], identity: IndexIdentity) -> None:
        """Embed in batches and upsert.

        Batching is the fix for the ~17-seconds-per-100 KB serial ingestion in the legacy
        pipeline: one call per chunk becomes one call per ``max_batch_size`` chunks.
        """
        batch_size = max(1, self._embedder.max_batch_size)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            try:
                embeddings = await self._embedder.embed([c.content for c in batch], for_query=False)
            except EmbeddingError:
                if self._metrics is not None:
                    self._metrics.embedding_failures_total.labels(
                        provider=self._embedder.name, reason="ingest"
                    ).inc()
                # Propagates. A partially-embedded document must not be recorded as ingested --
                # that is how a corpus silently becomes unsearchable.
                raise
            await self._vectors.upsert(
                identity,
                [
                    VectorPoint(id=c.id.value, vector=v, payload=self._indexes.payload_for(c))
                    for c, v in zip(batch, embeddings.vectors)
                ],
            )
            if self._metrics is not None:
                self._metrics.chunks_indexed_total.inc(len(batch))

    async def _retire_chunks(self, previous: Document, identity: IndexIdentity) -> None:
        """Remove the superseded version's chunks from both indexes.

        Skipping this is how stale and fresh content end up mixed in one result set, with no way
        for the model or the user to tell which is which.
        """
        old_chunks = await self._documents.chunks_for(previous.id, previous.version)
        if not old_chunks:
            return
        ids = [c.id.value for c in old_chunks]
        await self._vectors.delete(identity, point_ids=ids)
        for chunk_id in ids:
            self._lexical.remove(chunk_id)

    async def _extract_graph(
        self, document: Document, text: str, owner_id: UserId | None, *, superseded: bool
    ) -> None:
        pipeline = self._graph_pipeline
        assert pipeline is not None
        try:
            report = await pipeline.ingest(
                document_id=document.id.value, version=document.version, text=text,
                owner_id=owner_id,
            )
            logger.info("graph updated", extra=report.as_dict() | {"rejected": len(report.rejected)})
        except Exception:
            # Graph extraction is an enhancement. Failing it must not fail the ingestion that
            # the user actually asked for.
            logger.warning("graph extraction failed", extra={"document": document.id.value})
        if superseded:
            # Separate from extraction on purpose: even if the new version's extraction failed,
            # what only the old version said must stop being a current fact.
            try:
                await pipeline.retire_superseded(
                    document_id=document.id.value, current_version=document.version,
                    owner_id=owner_id,
                )
            except Exception:
                logger.warning(
                    "graph retirement of superseded version failed",
                    extra={"document": document.id.value},
                )

    async def delete_document(self, document_id: DocumentId, *, owner_id: UserId | None) -> int:
        """Delete a document: its derived graph facts, then the tombstone, then its index entries.

        Returns how many chunks left the corpus; ``0`` when there was no such document of
        ``owner_id``'s. The tombstone comes before the index work and is what makes the delete
        hold: every retrieval path checks hits against the document store (lexical and dense
        alike), so a vector point this delete fails to reach -- an index that is down, a replica
        that has not caught up -- can no longer bring the document back. The index deletes are
        then cleanup, and never depend on the configured embedder matching the published index
        (F14): deleting reaches the points by id in every index that may hold them.
        """
        document = await self._documents.get(document_id)
        if document is None or _owner_value(document.owner_id) != _owner_value(owner_id):
            # Someone else's document is reported exactly like an absent one. Checked here, not
            # left to the store's owner-scoped tombstone: that ran *after* the chunks had already
            # left the vector and lexical indexes, so another user's delete reported 0 chunks
            # removed while silently making the owner's document unretrievable.
            return 0
        if self._graph_pipeline is not None:
            # Before the tombstone and allowed to raise: "delete my data" must include the facts
            # derived from it, and a failure here leaves the document un-tombstoned so a retried
            # delete does the whole job rather than reporting success over orphaned facts.
            await self._graph_pipeline.purge(document_id=document_id.value, owner_id=owner_id)
        chunks = await self._documents.chunks_for(document_id, document.version)
        removed = await self._documents.tombstone(document_id, owner_id=owner_id)
        for chunk in chunks:
            self._lexical.remove(chunk.id.value)
        await self._remove_points([c.id.value for c in chunks])
        return removed

    async def _remove_points(self, point_ids: Sequence[str]) -> None:
        """Best-effort index cleanup after a tombstone. A failure is logged, not raised: the
        tombstone already hides the document from retrieval, and erasure sweeps the owner's
        points by filter regardless (:meth:`erase_owner`)."""
        if not point_ids:
            return
        try:
            identities = await self._indexes.identities_for_removal()
        except Exception as exc:
            logger.warning("index cleanup skipped", extra={"error": type(exc).__name__})
            return
        for identity in identities:
            try:
                await self._vectors.delete(identity, point_ids=list(point_ids))
            except Exception as exc:
                logger.warning(
                    "vector cleanup after delete failed",
                    extra={"index": identity.physical_name, "error": type(exc).__name__},
                )

    async def erase_owner(self, owner_id: UserId) -> int:
        """Erasure: everything ``owner_id`` ever ingested, gone from every index and the store.

        Each active document goes through :meth:`delete_document` first -- that is what removes
        its vectors, lexical entries and derived graph facts -- and then every row and chunk the
        owner has is deleted outright, including versions and documents tombstoned earlier, whose
        chunk text a tombstone keeps. Returns how many document rows were removed.
        """
        async def owned() -> list:
            # Filtered again rather than trusted: global documents are never the owner's to erase,
            # whatever a store's listing includes.
            found = await self._documents.list_active(owner_id=owner_id, limit=100)
            return [d for d in found if _owner_value(d.owner_id) == _owner_value(owner_id)]

        while True:
            active = await owned()
            if not active:
                break
            for document in active:
                await self.delete_document(document.id, owner_id=owner_id)
            if {d.id.value for d in active} & {d.id.value for d in await owned()}:
                # A document that survives its own delete would be returned again forever.
                raise KnowledgeError(f"could not delete the documents of {owner_id.value!r}")
        # Every point the owner has, in every index that may hold one, by owner filter -- not
        # only the active versions' chunks the loop above reached, and allowed to raise: unlike a
        # single delete, an erasure that leaves vector payloads behind has not erased.
        for identity in await self._indexes.identities_for_removal():
            await self._vectors.delete_by_filter(identity, filters={}, owner_id=owner_id)
        return await self._documents.purge_owner(owner_id)

    # -- retrieval ---------------------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        *,
        owner_id: UserId | None,
        limit: int | None = None,
        include_global: bool = True,
        score_threshold: float | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> RetrievalResult:
        """Hybrid retrieve, rerank, threshold, diversify."""
        started = time.perf_counter()
        text = query.strip()
        if not text:
            return RetrievalResult(query=query, strategy="none")

        top_k = limit or self._top_k
        threshold = self._threshold if score_threshold is None else score_threshold
        strategy = "hybrid" if self._hybrid else "dense"

        try:
            identity = await self._indexes.resolve_for_read()
        except Exception as exc:
            logger.warning("retrieval skipped: index not ready", extra={"error": type(exc).__name__})
            return RetrievalResult(query=query, strategy="unavailable")

        # Dense and lexical run concurrently: they are independent, and serialising them adds
        # the slower one's latency to every turn for no benefit.
        dense_task = asyncio.create_task(
            self._dense_search(text, identity, owner_id, include_global, filters)
        )
        lexical_task = asyncio.create_task(
            self._lexical_search(text, owner_id, include_global)
            if self._hybrid
            else self._empty()
        )
        dense_hits, lexical_hits = await asyncio.gather(dense_task, lexical_task)
        if dense_hits:
            dense_hits = await self._drop_inactive_dense(dense_hits)
        if lexical_hits:
            lexical_hits = await self._drop_inactive_lexical(
                lexical_hits, {chunk_id for chunk_id, _score, _payload in dense_hits}
            )

        if not dense_hits and not lexical_hits:
            if self._metrics is not None:
                self._metrics.retrieval_total.labels(strategy=strategy, result="empty").inc()
            return RetrievalResult(query=query, strategy=strategy, candidates_considered=0)

        payloads = {chunk_id: payload for chunk_id, _score, payload in dense_hits}
        dense_scores = {chunk_id: score for chunk_id, score, _payload in dense_hits}
        lexical_scores = dict(lexical_hits)
        dense_ranking = [c for c, _ in sorted(dense_scores.items(), key=lambda i: i[1], reverse=True)]
        lexical_ranking = [c for c, _ in sorted(lexical_scores.items(), key=lambda i: i[1], reverse=True)]

        fused = (
            reciprocal_rank_fusion([dense_ranking, lexical_ranking], k=self._rrf_k)
            if self._hybrid and lexical_ranking
            else [(c, dense_scores[c]) for c in dense_ranking]
        )
        candidates = fused[: self._candidate_k]
        candidate_ids = [chunk_id for chunk_id, _ in candidates]

        # A chunk that only the lexical retriever found has no payload from the vector search;
        # fetch its content from the document store so it can be reranked and returned.
        contents, stored = await self._resolve_contents(candidate_ids, payloads)

        scored = await self._rerank(text, candidate_ids, contents)

        results: list[RetrievedChunk] = []
        below: list[RetrievedChunk] = []
        for chunk_id, score in scored:
            chunk = await self._chunk_from(
                chunk_id, payloads.get(chunk_id), contents.get(chunk_id), stored.get(chunk_id)
            )
            if chunk is None:
                continue
            retrieved = RetrievedChunk(
                chunk=chunk,
                score=score,
                dense_score=dense_scores.get(chunk_id),
                lexical_score=lexical_scores.get(chunk_id),
                rerank_score=score if self._rerank_enabled else None,
                dense_rank=dense_ranking.index(chunk_id) + 1 if chunk_id in dense_scores else None,
                lexical_rank=lexical_ranking.index(chunk_id) + 1 if chunk_id in lexical_scores else None,
            )
            (results if score >= threshold else below).append(retrieved)

        results = self._diversify(results, top_k)
        duration = (time.perf_counter() - started) * 1000.0

        if self._metrics is not None:
            self._metrics.retrieval_total.labels(
                strategy=strategy, result="hit" if results else "empty"
            ).inc()
            self._metrics.retrieval_duration.labels(strategy=strategy).observe(duration / 1000.0)
            self._metrics.retrieval_returned.observe(len(results))
            if results:
                self._metrics.retrieval_top_score.labels(strategy=strategy).observe(results[0].score)
            elif candidates:
                self._metrics.retrieval_empty_total.inc()

        return RetrievalResult(
            chunks=tuple(results),
            query=query,
            candidates_considered=len(candidates),
            below_threshold=tuple(below[:10]),
            strategy=strategy,
            duration_ms=duration,
        )

    @staticmethod
    async def _empty() -> list[tuple[str, float]]:
        return []

    async def _dense_search(
        self,
        text: str,
        identity: IndexIdentity,
        owner_id: UserId | None,
        include_global: bool,
        filters: Mapping[str, Any] | None,
    ) -> list[tuple[str, float, Mapping[str, Any]]]:
        try:
            embedding = await self._embedder.embed([text], for_query=True)
        except EmbeddingError as exc:
            # Degrade to lexical-only rather than failing the turn. This is a legitimate
            # degradation because the fallback still searches the real corpus -- unlike the
            # legacy zero-vector fallback, which searches nothing while appearing to work.
            logger.warning("dense retrieval unavailable", extra={"error": exc.kind})
            if self._metrics is not None:
                self._metrics.embedding_failures_total.labels(
                    provider=self._embedder.name, reason="query"
                ).inc()
            return []
        return list(
            await self._vectors.search(
                identity,
                embedding.first,
                limit=self._candidate_k,
                owner_id=owner_id,
                include_global=include_global,
                filters=filters,
            )
        )

    async def _lexical_search(
        self, text: str, owner_id: UserId | None, include_global: bool
    ) -> list[tuple[str, float]]:
        # Tenant isolation is the choice of groups: the caller's own chunks and, if asked, the
        # global ones. No identity reads only the global group. Selecting shards replaces the
        # per-query pass over every chunk's owner that used to build an allow-list (F-30).
        groups = [owner_id.value] if owner_id else []
        if include_global:
            groups.append(GLOBAL_OWNER)
        if not groups:
            return []
        await self._converge_lexical(groups)
        if self._lexical.size == 0:
            return []
        return self._lexical.search(text, limit=self._candidate_k, groups=groups)

    async def _converge_lexical(self, groups: Sequence[str]) -> None:
        """Bring the shards this search reads up to date with the shared document store.

        One keyed read of the store's corpus revisions per search. A shard whose revision has
        not moved since this worker last matched it costs nothing more; one that has moved --
        an upload, a new version, a delete or an erasure served by any worker -- is refreshed
        on its own (`_refresh_lexical_group`), so a busy tenant never makes another tenant's
        search rebuild anything.

        Never raises. If the store cannot answer, the search runs on the index this worker
        already has: the read-time active-version check in `retrieve` still keeps retired chunks
        out of the results, and the next search tries again.
        """
        try:
            revisions = await self._documents.corpus_revisions(groups)
        except Exception as exc:
            logger.warning(
                "lexical index freshness check failed; searching the local index",
                extra={"error": type(exc).__name__},
            )
            return
        for group in groups:
            current = revisions.get(group, 0)
            if self._lexical_revisions.get(group, -1) >= current:
                continue
            async with self._lexical_refresh_locks.hold(group):
                # Re-checked under the lock: a concurrent search may have refreshed the shard to
                # this revision or a later one while this one waited.
                if self._lexical_revisions.get(group, -1) >= current:
                    continue
                try:
                    await self._refresh_lexical_group(group)
                except Exception as exc:
                    logger.warning(
                        "lexical shard refresh failed; searching the local index",
                        extra={"error": type(exc).__name__},
                    )
                    continue
                # `current` was read before the refresh listed the shard, so anything committed
                # after that listing carries a later revision and is picked up next time.
                self._lexical_revisions[group] = current

    async def _refresh_lexical_group(self, group: str) -> None:
        """Make one shard hold exactly the active chunks the store has for this owner key.

        A diff, not a rebuild: the store lists the shard's active chunk ids, ids this worker
        holds that are no longer listed are removed, and only the listed ids it lacks are
        fetched and tokenised. The cost is one id listing plus the content of what changed.
        """
        active = set(await self._documents.active_chunk_ids(group))
        # Computed straight after the listing returns, with no await in between, so no local
        # write can interleave between the listing and the diff taken against it.
        held = self._lexical.ids_in(group)
        stale = held - active
        missing = active - held
        for chunk_id in stale:
            self._lexical.remove(chunk_id)
        if missing:
            for chunk in await self._documents.active_chunks(sorted(missing)):
                self._lexical.add(chunk.id.value, chunk.content, group=group)
        if stale or missing:
            logger.info(
                "lexical shard refreshed from the document store",
                extra={"added": len(missing), "removed": len(stale)},
            )

    async def _drop_inactive_dense(
        self, hits: Sequence[tuple[str, float, Mapping[str, Any]]]
    ) -> list[tuple[str, float, Mapping[str, Any]]]:
        """Keep only dense hits whose chunk belongs to its document's active version.

        A vector index is not the source of truth (ADR-007): a delete whose vector cleanup
        failed, an index another embedder built, or a replica that has not caught up can all
        still hold points for a tombstoned or superseded document. One store read for the whole
        candidate set decides. If the store cannot answer, the dense hits are dropped rather
        than trusted -- returning a document its owner deleted is worse than a lexical-only
        answer (F14).
        """
        try:
            live = {
                chunk.id.value
                for chunk in await self._documents.active_chunks([cid for cid, _s, _p in hits])
            }
        except Exception as exc:
            logger.warning(
                "dense hits dropped: liveness check failed", extra={"error": type(exc).__name__}
            )
            return []
        return [hit for hit in hits if hit[0] in live]

    async def _drop_inactive_lexical(
        self, hits: Sequence[tuple[str, float]], live_ids: set[str]
    ) -> list[tuple[str, float]]:
        """Drop lexical hits whose chunk is not part of its document's active version.

        The BM25 index is per process, and a delete or a new version updates directly only the
        index of the gateway worker that served it. `_converge_lexical` brings the other workers
        along before each search, but a write can still land between that check and this
        search, or the check can fail, and ``chunks_for`` resolves chunk ids regardless of
        status -- which is how a deleted document stayed searchable (F-14). So this stays as the
        guard: the shared document store decides, and a hit whose document is tombstoned, or
        whose version has been superseded, is dropped and evicted from this worker's index.
        Versions never become active again, so the eviction is final. Hits the vector store also
        returned are live already, and skip the lookup.
        """
        active_versions: dict[str, int | None] = {}
        kept: list[tuple[str, float]] = []
        for chunk_id, score in hits:
            if chunk_id in live_ids:
                kept.append((chunk_id, score))
                continue
            document_id, version, _index = self._parse_chunk_id(chunk_id)
            if document_id is not None:
                if document_id not in active_versions:
                    document = await self._documents.get(DocumentId(document_id))
                    active_versions[document_id] = document.version if document else None
                if active_versions[document_id] == version:
                    kept.append((chunk_id, score))
                    continue
            self._lexical.remove(chunk_id)
        return kept

    async def _resolve_contents(
        self, chunk_ids: Sequence[str], payloads: Mapping[str, Mapping[str, Any]]
    ) -> tuple[dict[str, str], dict[str, Chunk]]:
        """Each candidate's text, and the stored chunk for every candidate whose payload did not
        carry it (lexical-only hits, and every hit when the payload content is off -- FSA-018).

        One store read per document version, not per candidate: ``chunks_for`` is a full SELECT
        of the document's chunks, decrypted when field encryption is on, and twenty candidates
        from five documents must cost five of those, not twenty.
        """
        contents: dict[str, str] = {}
        stored: dict[str, Chunk] = {}
        fetched: dict[tuple[str, int], dict[int, Chunk]] = {}
        for chunk_id in chunk_ids:
            payload = payloads.get(chunk_id)
            if payload and payload.get("content"):
                contents[chunk_id] = str(payload["content"])
                continue
            document_id, version, index = self._parse_chunk_id(chunk_id)
            if document_id is None:
                continue
            key = (document_id, version)
            if key not in fetched:
                chunks = await self._documents.chunks_for(DocumentId(document_id), version)
                fetched[key] = {chunk.index: chunk for chunk in chunks}
            chunk = fetched[key].get(index)
            if chunk is not None:
                contents[chunk_id] = chunk.content
                stored[chunk_id] = chunk
        return contents, stored

    @staticmethod
    def _parse_chunk_id(chunk_id: str) -> tuple[str | None, int, int]:
        """Chunk ids are ``{document}:v{version}:{index}`` (see ``Chunk.build_id``)."""
        try:
            document_id, version_part, index_part = chunk_id.rsplit(":", 2)
            return document_id, int(version_part.lstrip("v")), int(index_part)
        except (ValueError, AttributeError):
            return None, 0, 0

    async def _rerank(
        self, query: str, candidate_ids: Sequence[str], contents: Mapping[str, str]
    ) -> list[tuple[str, float]]:
        """Order the candidates and give each the score the relevance threshold is applied to.

        That score has to measure how well *this* chunk matches *this* query, on the scale the
        threshold is calibrated for. It used to be derived from rank (``1 - i/n``) whenever the
        reranker was disabled or raised, which made the threshold meaningless on exactly those
        paths: the top candidate scored 1.0 and always passed however irrelevant it was, the
        bottom third was always cut however relevant, and ``searched_but_empty`` could never be
        true (audit F-30 / AR-14).

        The threshold is calibrated against :class:`HeuristicReranker` scores -- the default
        provider, and what the cross-encoder itself degrades to -- so without a working
        configured reranker that is what supplies the score. Neither fallback is a rank:

        - reranking disabled: the fusion order is kept, as configured, but each candidate is
          *gated* on its heuristic relevance;
        - the reranker raised: the candidates are reranked by the heuristic instead, the same
          degradation ``CrossEncoderReranker`` applies to its own failures.

        Dense cosine is deliberately not used as the gate: its distribution depends on the
        embedding model (``bge-small`` puts unrelated passages well above 0.35), so the same
        floor would mean something different per deployment. If even the heuristic fails, every
        candidate scores 0.0 -- an honest "nothing passed" rather than a fabricated pass.
        """
        usable = [(cid, contents[cid]) for cid in candidate_ids if cid in contents]
        if not usable:
            return []
        texts = [text for _cid, text in usable]

        if not self._rerank_enabled:
            relevance = await self._fallback_relevance(query, texts)
            return [(cid, relevance.get(i, 0.0)) for i, (cid, _) in enumerate(usable)]

        try:
            ranked = await self._reranker.rerank(query, texts, top_k=self._candidate_k)
        except Exception:
            logger.warning("reranker failed; scoring with the heuristic reranker instead")
            relevance = await self._fallback_relevance(query, texts)
            ranked = sorted(relevance.items(), key=lambda item: item[1], reverse=True)
            if len(ranked) < len(usable):
                ranked.extend((i, 0.0) for i in range(len(usable)) if i not in relevance)
        return [(usable[index][0], score) for index, score in ranked if index < len(usable)]

    async def _fallback_relevance(self, query: str, texts: Sequence[str]) -> dict[int, float]:
        """Heuristic relevance per candidate index; empty (so everything fails) if it raises."""
        try:
            return dict(await _FALLBACK_SCORER.rerank(query, texts))
        except Exception:  # pragma: no cover - the heuristic is pure Python over strings
            logger.warning("heuristic relevance scoring failed; no candidate can pass the threshold")
            return {}

    def _diversify(self, results: Sequence[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
        """Drop near-duplicate chunks.

        Uses content-token overlap rather than vectors: at this point the vectors are no longer
        in hand, and Jaccard over tokens is a good enough redundancy signal for chunks that
        overlap by construction (the chunker's sentence overlap guarantees adjacent chunks share
        text).
        """
        if len(results) <= 1:
            return list(results[:top_k])

        from .retrieval import tokenize

        selected: list[RetrievedChunk] = []
        token_sets: list[set[str]] = []
        for candidate in results:
            tokens = set(tokenize(candidate.chunk.content))
            redundancy = max(
                (
                    len(tokens & existing) / max(1, len(tokens | existing))
                    for existing in token_sets
                ),
                default=0.0,
            )
            # λ-weighted MMR objective, same shape as the vector version in retrieval.py.
            if self._mmr_lambda * candidate.score - (1 - self._mmr_lambda) * redundancy <= 0 and selected:
                continue
            selected.append(candidate)
            token_sets.append(tokens)
            if len(selected) >= top_k:
                break
        return selected

    async def _chunk_from(
        self,
        chunk_id: str,
        payload: Mapping[str, Any] | None,
        content: str | None,
        stored: Chunk | None = None,
    ) -> Chunk | None:
        if stored is not None:
            # The chunk came back from the document store (the payload carried no text, or the
            # hit was lexical-only): it already has its heading path, title and content. Keep any
            # filterable provenance the payload has that the store did not persist.
            if payload:
                extra = {k: payload[k] for k in _PROVENANCE_KEYS if k in payload and k not in stored.metadata}
                if extra:
                    return replace(stored, metadata={**stored.metadata, **extra})
            return stored
        if payload:
            from ..domain.identifiers import ChunkId

            owner = payload.get("owner_id")
            return Chunk(
                id=ChunkId(chunk_id),
                document_id=DocumentId(str(payload.get("document_id", "unknown"))),
                document_version=int(payload.get("document_version", 1)),
                index=int(payload.get("chunk_index", 0)),
                content=content or str(payload.get("content", "")),
                heading_path=tuple(payload.get("heading_path") or ()),
                owner_id=UserId(owner) if owner and owner != GLOBAL_OWNER else None,
                # Provenance keys have to survive the round trip, not just the filter. Rebuilding
                # with only the title made `modality` and `page` write-only: the vector store could
                # filter on them, but a retrieved chunk could not say which page it came from, so
                # no citation could name one and nothing downstream could tell an unreadable-page
                # placeholder apart from real content.
                metadata={
                    "title": payload.get("title", ""),
                    **{k: payload[k] for k in _PROVENANCE_KEYS if k in payload},
                },
            )

        document_id, version, index = self._parse_chunk_id(chunk_id)
        if document_id is None:
            return None
        for chunk in await self._documents.chunks_for(DocumentId(document_id), version):
            if chunk.index == index:
                return chunk
        return None

    # -- maintenance -------------------------------------------------------------------

    async def rebuild_lexical_index(self) -> int:
        """Repopulate the in-process BM25 index from the document store.

        Called at startup: the lexical index is process-local, so a restart or a new replica
        starts with an empty one, and hybrid retrieval would silently degrade to dense-only.

        The recorded corpus revisions are forgotten with it, so each shard is checked against the
        store on its first search: whatever another worker wrote while this stream was running
        is reconciled then rather than missed.
        """
        self._lexical.clear()
        self._lexical_revisions.clear()
        count = 0
        async for batch in self._documents.iter_all_chunks(batch_size=500):
            for chunk in batch:
                self._lexical.add(chunk.id.value, chunk.content, group=_owner_key(chunk.owner_id))
                count += 1
        logger.info("lexical index rebuilt", extra={"chunks": count})
        return count

    async def _publish(self, event: Any) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(event)
        except Exception:  # pragma: no cover - defensive
            logger.debug("knowledge event publication failed", exc_info=True)

    @property
    def lexical_size(self) -> int:
        return self._lexical.size
