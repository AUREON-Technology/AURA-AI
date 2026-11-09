"""Index lifecycle: build, verify, switch, roll back (ADR-007).

The defect this replaces is specific and severe. ``services/rag.py:69-72`` deletes the entire
Qdrant collection whenever the embedding dimension it computes at boot differs from the
collection's -- and since the embedding backend is chosen by a runtime fallback chain, an expired
API key is sufficient to destroy every user's knowledge base on the next restart (SEC-03).

Here:

* an index's identity includes the embedding model and dimension, so a change **provisions a new
  index** rather than overwriting one;
* readers and writers address an **alias**, and promotion is an alias repoint -- atomic, O(1),
  and reversible with no data movement;
* promotion is **gated** on a retrieval evaluation against the current index, so a rebuild that
  loses recall never goes live;
* the previous index is retained for a configurable window, which is what makes rollback a
  thirty-second operation instead of a rebuild.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.errors import ConflictError, IndexNotReadyError, NotFoundError
from ..domain.events import IndexBuilt, IndexSwitched
from ..domain.identifiers import GenerationId
from ..domain.knowledge import Chunk, IndexIdentity
from ..observability.logging import get_logger
from ..ports.llm import EmbeddingProvider
from ..ports.storage import GLOBAL_OWNER, DocumentStore, VectorPoint, VectorStore

__all__ = ["IndexManager", "BuildReport", "PromotionGate", "GateResult"]

logger = get_logger("knowledge.index")


@dataclass(frozen=True, slots=True)
class BuildReport:
    identity: IndexIdentity
    documents: int
    chunks: int
    failures: int
    duration_ms: float
    generation: GenerationId

    @property
    def error_rate(self) -> float:
        total = self.chunks + self.failures
        return self.failures / total if total else 0.0


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    failures: tuple[str, ...] = ()
    measurements: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PromotionGate:
    """Thresholds a candidate index must clear before an alias switch.

    Every one of these corresponds to a real failure mode:

    ``max_recall_drop``    a rebuild that retrieves worse than what it replaces.
    ``max_error_rate``     an embedding backend degrading mid-build.
    ``max_count_delta``    a source that started returning an error page -- the classic
                           "the nightly job succeeded and the corpus is now 4 documents".
    ``min_chunks``         an empty build, which would otherwise promote cleanly.
    """

    max_recall_drop: float = 0.02
    max_error_rate: float = 0.05
    max_count_delta: float = 0.40
    min_chunks: int = 1

    def evaluate(
        self,
        *,
        candidate_recall: float | None,
        baseline_recall: float | None,
        error_rate: float,
        candidate_chunks: int,
        baseline_chunks: int,
    ) -> GateResult:
        failures: list[str] = []
        measurements: dict[str, float] = {
            "error_rate": round(error_rate, 4),
            "candidate_chunks": float(candidate_chunks),
            "baseline_chunks": float(baseline_chunks),
        }

        if candidate_chunks < self.min_chunks:
            failures.append(f"candidate index has {candidate_chunks} chunks, minimum {self.min_chunks}")

        if error_rate > self.max_error_rate:
            failures.append(f"error rate {error_rate:.1%} exceeds {self.max_error_rate:.1%}")

        if baseline_chunks > 0:
            delta = abs(candidate_chunks - baseline_chunks) / baseline_chunks
            measurements["count_delta"] = round(delta, 4)
            if delta > self.max_count_delta:
                failures.append(
                    f"chunk count changed by {delta:.1%}, exceeding {self.max_count_delta:.1%}"
                )

        if candidate_recall is not None and baseline_recall is not None:
            measurements["candidate_recall"] = round(candidate_recall, 4)
            measurements["baseline_recall"] = round(baseline_recall, 4)
            drop = baseline_recall - candidate_recall
            measurements["recall_drop"] = round(drop, 4)
            if drop > self.max_recall_drop:
                failures.append(
                    f"recall dropped {drop:.3f}, exceeding {self.max_recall_drop:.3f}"
                )

        return GateResult(passed=not failures, failures=tuple(failures), measurements=measurements)


class IndexManager:
    def __init__(
        self,
        *,
        vectors: VectorStore,
        documents: DocumentStore,
        embedder: EmbeddingProvider,
        alias: str,
        schema_version: int = 1,
        gate: PromotionGate | None = None,
        metrics: Any = None,
        event_bus: Any = None,
        batch_size: int = 64,
        payload_content: bool = True,
    ) -> None:
        self._vectors = vectors
        self._documents = documents
        self._embedder = embedder
        self._alias = alias
        #: Whether chunk text is copied into the vector payload (audit FSA-018). Off for an
        #: off-host vector store: the engine resolves text from the document store instead.
        self.payload_content = payload_content
        self._schema_version = schema_version
        self._gate = gate or PromotionGate()
        self._metrics = metrics
        self._events = event_bus
        self._batch_size = max(1, min(batch_size, embedder.max_batch_size))

    # -- identity ----------------------------------------------------------------------

    def identity_for_current_embedder(self) -> IndexIdentity:
        return IndexIdentity(
            logical_name=self._alias,
            schema_version=self._schema_version,
            embedding_model_id=self._embedder.model_id,
            dimension=self._embedder.dimension,
        )

    async def active_identity(self) -> IndexIdentity | None:
        return await self._vectors.resolve_alias(self._alias)

    async def resolve_for_read(self) -> IndexIdentity:
        """The index reads must go through.

        Raises when the alias points at an index built by a *different* embedding model: a query
        vector from model B is meaningless against vectors from model A, and returning confident
        nonsense is worse than an error the operator can act on.
        """
        active = await self.active_identity()
        required = self.identity_for_current_embedder()
        if active is None:
            raise IndexNotReadyError(
                f"no index is published under alias {self._alias!r}; build one first",
                details={"required": required.physical_name},
            )
        if not active.matches_embedding(self._embedder.model_id, self._embedder.dimension):
            raise IndexNotReadyError(
                "the published index was built with a different embedding model; rebuild and "
                "promote before querying",
                details={"published": active.physical_name, "required": required.physical_name},
            )
        return active

    async def ensure_ready(self) -> IndexIdentity:
        """Create and publish an empty index if none exists.

        Used on first boot so a fresh install is queryable (returning no results) rather than
        erroring. Never touches an existing index.
        """
        required = self.identity_for_current_embedder()
        await self._vectors.ensure_index(required)
        active = await self.active_identity()
        if active is None:
            await self._vectors.set_alias(self._alias, required)
            logger.info("published initial index", extra={"index": required.physical_name})
        return required

    # -- build -------------------------------------------------------------------------

    async def build(
        self, *, identity: IndexIdentity | None = None, generation: GenerationId | None = None
    ) -> BuildReport:
        """Populate a target index from the document store.

        The document store is the source of truth (ADR-007), so a rebuild never depends on the
        index it is replacing -- which is what makes an embedding-model change a routine
        operation rather than a migration.

        Chunks stream in batches so the build is bounded by batch size, not corpus size.
        """
        target = identity or self.identity_for_current_embedder()
        generation_id = generation or GenerationId.new()
        started = time.perf_counter()

        await self._vectors.ensure_index(target)

        chunks_written = 0
        failures = 0
        documents_seen: set[str] = set()

        async for batch in self._documents.iter_all_chunks(batch_size=self._batch_size):
            if not batch:
                continue
            texts = [chunk.content for chunk in batch]
            try:
                embeddings = await self._embedder.embed(texts, for_query=False)
            except Exception as exc:
                # A failed batch is counted, not fatal. The gate's error-rate threshold decides
                # whether the resulting index is fit to promote -- which is the correct place
                # for that judgement.
                failures += len(batch)
                logger.warning(
                    "embedding batch failed during index build",
                    extra={"size": len(batch), "error": type(exc).__name__},
                )
                continue

            points = [
                VectorPoint(
                    id=chunk.id.value,
                    vector=vector,
                    payload=self.payload_for(chunk),
                )
                for chunk, vector in zip(batch, embeddings.vectors)
            ]
            await self._vectors.upsert(target, points)
            chunks_written += len(points)
            documents_seen.update(c.document_id.value for c in batch)
            if self._metrics is not None:
                self._metrics.chunks_indexed_total.inc(len(points))

        duration = (time.perf_counter() - started) * 1000.0
        report = BuildReport(
            identity=target,
            documents=len(documents_seen),
            chunks=chunks_written,
            failures=failures,
            duration_ms=duration,
            generation=generation_id,
        )
        logger.info(
            "index build complete",
            extra={"index": target.physical_name, "chunks": chunks_written,
                   "documents": len(documents_seen), "failures": failures,
                   "ms": round(duration, 1)},
        )
        await self._publish(
            IndexBuilt(
                index_name=target.physical_name,
                documents=len(documents_seen),
                chunks=chunks_written,
                embedding_model=target.embedding_model_id,
                dimension=target.dimension,
                duration_ms=duration,
            )
        )
        return report

    #: The only string-valued metadata an off-host payload may carry: filterable provenance
    #: (what kind of source, which modality), never document text. A title or a heading reads
    #: as well as the body does, so with the content off they stay in the document store too.
    OFF_HOST_PAYLOAD_KEYS = frozenset({"source_type", "modality", "page", "needs_ocr"})

    def payload_for(self, chunk: Chunk) -> dict[str, Any]:
        """The vector payload for a chunk: identity and scope always; headings, title and the
        text only when :attr:`payload_content` allows it (audit FSA-018). Off-host the engine
        rebuilds the chunk from the owner-scoped document store on retrieval."""
        payload: dict[str, Any] = {
            "chunk_id": chunk.id.value,
            "document_id": chunk.document_id.value,
            "document_version": chunk.document_version,
            "chunk_index": chunk.index,
            "owner_id": chunk.owner_id.value if chunk.owner_id else GLOBAL_OWNER,
            "trust_tier": int(chunk.trust_tier),
        }
        if self.payload_content:
            payload["heading_path"] = list(chunk.heading_path)
            payload.update(
                {k: v for k, v in chunk.metadata.items() if isinstance(v, (str, int, float, bool))}
            )
            payload["content"] = chunk.content
        else:
            payload.update({
                k: v for k, v in chunk.metadata.items()
                if isinstance(v, (int, float, bool)) and not isinstance(v, str)
                or (isinstance(v, str) and k in self.OFF_HOST_PAYLOAD_KEYS)
            })
        return payload

    # -- promotion ---------------------------------------------------------------------

    async def evaluate_promotion(
        self,
        candidate: IndexIdentity,
        *,
        report: BuildReport,
        candidate_recall: float | None = None,
        baseline_recall: float | None = None,
    ) -> GateResult:
        baseline = await self.active_identity()
        baseline_chunks = await self._vectors.count(baseline) if baseline else 0
        candidate_chunks = await self._vectors.count(candidate)
        result = self._gate.evaluate(
            candidate_recall=candidate_recall,
            baseline_recall=baseline_recall,
            error_rate=report.error_rate,
            candidate_chunks=candidate_chunks,
            baseline_chunks=baseline_chunks,
        )
        if not result.passed and self._metrics is not None:
            for failure in result.failures:
                self._metrics.refresh_gate_failures_total.labels(gate=failure.split()[0]).inc()
        return result

    async def promote(
        self, candidate: IndexIdentity, *, generation: GenerationId | None = None,
        reason: str = "promotion", force: bool = False, gate: GateResult | None = None,
    ) -> IndexIdentity:
        """Repoint the alias at ``candidate``.

        The whole promotion is one atomic operation from a reader's perspective: queries resolve
        the alias, so they see either the old index or the new one, never a half-built state.
        """
        if gate is not None and not gate.passed and not force:
            raise ConflictError(
                "promotion gates failed; index not promoted",
                details={"failures": list(gate.failures), "measurements": gate.measurements},
            )
        if not await self._vectors.index_exists(candidate):
            raise NotFoundError(
                f"cannot promote {candidate.physical_name}: it does not exist",
            )

        previous = await self.active_identity()
        await self._vectors.set_alias(self._alias, candidate)

        if self._metrics is not None:
            self._metrics.index_switches_total.labels(alias=self._alias, reason=reason).inc()
            self._metrics.knowledge_age_seconds.set(0)
        logger.info(
            "index alias switched",
            extra={"alias": self._alias, "from": previous.physical_name if previous else None,
                   "to": candidate.physical_name, "reason": reason},
        )
        await self._publish(
            IndexSwitched(
                alias=self._alias,
                from_index=previous.physical_name if previous else None,
                to_index=candidate.physical_name,
                generation=generation,
                reason=reason,
            )
        )
        return candidate

    async def rollback(self, target: IndexIdentity) -> IndexIdentity:
        """Repoint the alias back to a previously-good index.

        Always permitted without a gate: reverting to a known-good state is safe by definition,
        while advancing is not (ADR-008 draws the same line for model promotion).
        """
        return await self.promote(target, reason="rollback", force=True)

    async def prune(self, *, keep: Sequence[IndexIdentity] = ()) -> list[str]:
        """Drop indexes that are neither published nor explicitly retained.

        Retention is what makes rollback possible, so this is called on a schedule with a
        retention window, never immediately after a promotion.
        """
        active = await self.active_identity()
        protected = {i.physical_name for i in keep}
        if active:
            protected.add(active.physical_name)

        dropped: list[str] = []
        names = getattr(self._vectors, "index_names", None)
        if names is None:  # pragma: no cover - adapter without enumeration support
            return dropped
        for name in names():
            if name in protected or not name.startswith(self._alias.replace("_", "-")):
                continue
            try:
                identity = await self._vectors.resolve_alias(name)
                if identity is None:
                    continue
                await self._vectors.drop_index(identity)
                dropped.append(name)
            except ConflictError:
                continue
        return dropped

    async def _publish(self, event: Any) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(event)
        except Exception:  # pragma: no cover - defensive
            logger.debug("index event publication failed", exc_info=True)
