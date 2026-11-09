"""Memory engine (ADR-004).

One facade over six memory kinds. Callers say ``recall(query)`` and ``remember(fact)``; the
engine handles embedding, per-kind ranking, consolidation, and provenance.

What this replaces: ``services/memory.py``, which has one kind ("user fact"), stores it in the
same Qdrant collection as document chunks, deduplicates only by exact-text hash, never decays or
consolidates, keeps contradictory facts side by side, and records no provenance -- so there is
no way to answer "forget what you learned from that document".

Retrieval blends three signals with weights that differ per kind
(:class:`~aura_core.domain.memory.MemoryPolicy`): similarity, recency, and reinforcement. A fact
stated a year ago is still a fact; a conversational detail from last month usually is not. That
difference is data here, not scattered conditionals.

**Scopes (GAP-024).** Every record belongs to one persona scope (``MemoryRecord.scope``, stored
in its metadata; ``"shared"`` when unset). ``remember(scope=...)`` writes into a scope,
``MemoryQuery.scopes`` limits what ``recall`` returns, and duplicate detection and consolidation
never cross a scope boundary -- a private fact must not reinforce, or supersede, a shared one.
Users stay isolated exactly as before (every lookup is owner-filtered); scopes partition *within*
one user. The filter is applied to the relational record after the vector search rather than in
the vector payload, so records written before scopes existed (whose vector points carry no scope)
are still found -- and still land in ``"shared"``.
"""

from __future__ import annotations

import time
from dataclasses import asdict, replace
from typing import Any, Mapping, Sequence

from ..domain.errors import EmbeddingError, MemoryStoreError, ValidationError
from ..domain.events import MemoryConsolidated, MemoryWritten
from ..domain.identifiers import MemoryId, SessionId, UserId
from ..domain.knowledge import IndexIdentity
from ..domain.memory import (
    MEMORY_POLICIES,
    SCOPE_METADATA_KEY,
    SHARED_MEMORY_SCOPE,
    ConsolidationOutcome,
    MemoryHit,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    Provenance,
)
from ..llm.embeddings import cosine_similarity
from ..observability.logging import get_logger
from ..ports.llm import EmbeddingProvider
from ..ports.storage import MemoryStore, VectorPoint, VectorStore

__all__ = ["MemoryEngine"]

logger = get_logger("memory")


class MemoryEngine:
    """Recall, write, consolidate."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        vectors: VectorStore,
        embedder: EmbeddingProvider,
        index_alias: str = "aura_memory",
        schema_version: int = 1,
        metrics: Any = None,
        event_bus: Any = None,
        max_records_per_owner: int = 20_000,
        duplicate_threshold: float = 0.94,
        clock: Any = None,
    ) -> None:
        self._store = store
        self._vectors = vectors
        self._embedder = embedder
        self._metrics = metrics
        self._events = event_bus
        self._max_records = max_records_per_owner
        self._duplicate_threshold = duplicate_threshold
        self._clock = clock
        self._identity = IndexIdentity(
            logical_name=index_alias,
            schema_version=schema_version,
            embedding_model_id=embedder.model_id,
            dimension=embedder.dimension,
        )
        self._index_ready = False

    @property
    def index_identity(self) -> IndexIdentity:
        return self._identity

    async def _ensure_index(self) -> None:
        if not self._index_ready:
            await self._vectors.ensure_index(self._identity)
            self._index_ready = True

    # -- write -------------------------------------------------------------------------

    async def remember(
        self,
        *,
        owner_id: UserId,
        content: str,
        kind: MemoryKind = MemoryKind.SEMANTIC,
        subject: str | None = None,
        confidence: float = 0.8,
        source: str = "unspecified",
        session_id: SessionId | None = None,
        extractor: str | None = None,
        extractor_version: str | None = None,
        document_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        scope: str = SHARED_MEMORY_SCOPE,
    ) -> MemoryRecord:
        """Write one memory, merging with a near-duplicate if one exists.

        Provenance is mandatory (``source`` has no default that hides its absence) because two
        operations depend on it: rolling back everything a bad extractor version produced, and
        honouring "forget everything you learned from document X".
        """
        text = content.strip()
        if not text:
            raise ValidationError("memory content must be non-empty")

        policy = MEMORY_POLICIES[kind]
        now = self._now()

        embedding = await self._embed(text)
        duplicate = await self._find_duplicate(
            owner_id, kind, embedding, threshold=policy.duplicate_threshold, scope=scope
        )
        if duplicate is not None:
            # Restating a fact is reinforcement, not a second fact. Bumping access_count and
            # confidence is what makes repeated preferences outrank one-off mentions.
            merged = replace(
                duplicate.touched(now=now),
                confidence=min(1.0, max(duplicate.confidence, confidence) + 0.05),
            )
            await self._store.update(merged)
            logger.info("memory reinforced", extra={"kind": str(kind), "memory_id": merged.id.value})
            return merged

        record = MemoryRecord(
            id=MemoryId.new(),
            kind=kind,
            owner_id=owner_id,
            content=text,
            subject=subject,
            confidence=confidence,
            session_id=session_id,
            provenance=Provenance(
                source=source,
                session_id=session_id,
                extractor=extractor,
                extractor_version=extractor_version,
                document_ref=document_ref,
            ),
            expires_at=(now + policy.ttl) if policy.ttl else None,
            # From the injected clock, not wall time: a record's age must be measured against
            # the same source that later decays and ranks it.
            created_at=now,
            last_accessed_at=now,
            metadata={**dict(metadata or {}), SCOPE_METADATA_KEY: scope or SHARED_MEMORY_SCOPE},
        )

        await self._enforce_quota(owner_id)
        await self._store.put(record)
        await self._ensure_index()
        await self._vectors.upsert(
            self._identity,
            [
                VectorPoint(
                    id=record.id.value,
                    vector=embedding,
                    payload={
                        "memory_id": record.id.value,
                        "kind": str(kind),
                        "owner_id": owner_id.value,
                        "subject": subject or "",
                        "scope": scope or SHARED_MEMORY_SCOPE,
                        # What forget_source filters on (audit FSA-066): without it the rows
                        # went and every vector point stayed behind.
                        "source": source,
                    },
                )
            ],
        )

        if self._metrics is not None:
            self._metrics.memory_writes_total.labels(kind=str(kind)).inc()
        await self._publish(
            MemoryWritten(
                memory_id=record.id,
                kind=str(kind),
                owner_id=owner_id,
                source=source,
                confidence=confidence,
            )
        )
        logger.info("memory written", extra={"kind": str(kind), "memory_id": record.id.value})
        return record

    async def remember_many(self, items: Sequence[Mapping[str, Any]]) -> list[MemoryRecord]:
        """Sequential rather than concurrent, deliberately.

        Duplicate detection reads the state that previous writes create; running the batch
        concurrently would let two near-identical facts from the same extraction both pass the
        duplicate check and both be stored.
        """
        written = []
        for item in items:
            written.append(await self.remember(**item))
        return written

    # -- recall ------------------------------------------------------------------------

    async def recall(self, query: MemoryQuery) -> list[MemoryHit]:
        """Retrieve and rank memories.

        Vector search finds candidates; the relational store supplies the full records; the
        per-kind policy does the ranking. Splitting it this way is what lets ranking use
        recency and reinforcement, which a vector store does not track.
        """
        started = time.perf_counter()
        if not query.text.strip():
            return []

        try:
            await self._ensure_index()
            embedding = await self._embed(query.text, for_query=True)
        except EmbeddingError as exc:
            # Memory is an enhancement, not a requirement. Losing it degrades answer quality;
            # failing the turn would be a worse trade.
            logger.warning("memory recall skipped: embedding failed", extra={"error": exc.kind})
            return []

        # Over-fetch: post-filtering by kind and scope, and re-ranking by recency/reinforcement,
        # can promote a candidate that vector similarity alone placed outside the top-k.
        candidate_limit = max(query.limit * 4, 20) * (2 if query.scopes is not None else 1)
        try:
            raw = await self._vectors.search(
                self._identity,
                embedding,
                limit=candidate_limit,
                owner_id=query.owner_id,
                include_global=False,
                filters={"kind": [str(k) for k in query.kinds]} if query.kinds else None,
            )
        except Exception as exc:
            logger.warning(
                "memory vector search failed",
                extra={"error": type(exc).__name__, "detail": str(exc)},
            )
            return []

        similarity_by_id = {payload.get("memory_id", point_id): score for point_id, score, payload in raw}
        if not similarity_by_id:
            return []

        records = await self._store.get_many([MemoryId(i) for i in similarity_by_id])
        now = self._now()
        hits: list[MemoryHit] = []
        touched: list[MemoryRecord] = []

        for record in records:
            if not query.include_superseded and not record.is_active:
                continue
            if record.kind not in query.kinds:
                continue
            if query.subject and record.subject != query.subject:
                continue
            if query.scopes is not None and record.scope not in query.scopes:
                continue
            if query.session_id and record.session_id and record.session_id != query.session_id:
                continue
            similarity = similarity_by_id.get(record.id.value, 0.0)
            score = record.retrieval_score(similarity, now=now)
            if score < query.min_score:
                continue
            hits.append(MemoryHit(record=record, similarity=similarity, score=score))
            touched.append(record)

        hits.sort(key=lambda h: h.score, reverse=True)
        hits = hits[: query.limit]

        if query.token_budget:
            hits = self._fit_budget(hits, query.token_budget)

        # Reinforce only what was actually used, not everything the vector search returned --
        # otherwise every recall inflates access_count for irrelevant records.
        used_ids = {h.record.id for h in hits}
        await self._reinforce(query.owner_id, [r.id for r in touched if r.id in used_ids], now)

        if self._metrics is not None:
            for kind in query.kinds:
                self._metrics.memory_recalls_total.labels(kind=str(kind)).inc()
            self._metrics.memory_recalled_records.observe(len(hits))
        logger.debug(
            "memory recall",
            extra={"returned": len(hits), "candidates": len(similarity_by_id),
                   "ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        return hits

    @staticmethod
    def _fit_budget(hits: Sequence[MemoryHit], budget: int) -> list[MemoryHit]:
        """Greedy fit in score order; memory competes with retrieval for the same context."""
        kept, used = [], 0
        for hit in hits:
            cost = max(1, (len(hit.record.content) + 3) // 4)
            if used + cost > budget:
                continue
            kept.append(hit)
            used += cost
        return kept

    async def _reinforce(self, owner_id: UserId, ids: Sequence[MemoryId], now) -> None:
        if not ids:
            return
        try:
            # A field-level touch of the current rows, never `update(record.touched())`: the
            # records were read before ranking, and writing that copy back reverted whatever
            # changed in between -- it resurrected superseded memories, re-created deleted
            # ones and lost concurrent increments (F-16).
            await self._store.touch_many(ids, owner_id=owner_id, at=now)
        except Exception:
            # Reinforcement is bookkeeping. Failing it must not fail the recall it accompanies.
            logger.debug("memory reinforcement failed", exc_info=True)

    # -- consolidation -----------------------------------------------------------------

    async def consolidate(self, owner_id: UserId, *, kinds: Sequence[MemoryKind] | None = None
                          ) -> ConsolidationOutcome:
        """Merge duplicates, supersede contradictions, decay, expire.

        Runs on a schedule rather than inline: it is O(n) in a user's memory and would add
        unpredictable latency to a turn. Called by the maintenance job.
        """
        started = time.perf_counter()
        target_kinds = tuple(kinds or (MemoryKind.SEMANTIC, MemoryKind.PROCEDURAL, MemoryKind.ENTITY))
        now = self._now()
        merged = superseded = expired = decayed = examined = 0

        for kind in target_kinds:
            policy = MEMORY_POLICIES[kind]
            if not policy.consolidate:
                continue
            records = await self._store.list_for_owner(owner_id, kinds=[kind], limit=5000)
            examined += len(records)

            # Keyed by (scope, subject): a persona's private fact never supersedes a shared one
            # (or another persona's), even on the same subject.
            by_subject: dict[tuple[str, str], list[MemoryRecord]] = {}
            for record in records:
                if not record.is_active:
                    continue
                if record.is_expired(now=now):
                    await self._forget(record)
                    expired += 1
                    continue
                decayed_record = record.decayed(now=now)
                if decayed_record.confidence < policy.min_confidence:
                    await self._forget(decayed_record)
                    expired += 1
                    continue
                if abs(decayed_record.confidence - record.confidence) > 1e-6:
                    await self._store.update(decayed_record)
                    decayed += 1
                if decayed_record.subject:
                    by_subject.setdefault(
                        (decayed_record.scope, decayed_record.subject), []
                    ).append(decayed_record)

            # Within a subject, the newest record wins and older ones are marked superseded
            # rather than deleted -- the user may be correcting the newer one, and an audit
            # trail of what was believed when is worth keeping.
            for subject, group in by_subject.items():
                if len(group) < 2:
                    continue
                group.sort(key=lambda r: r.created_at, reverse=True)
                newest = group[0]
                for older in group[1:]:
                    await self._store.update(older.superseded(newest.id))
                    superseded += 1

        outcome = ConsolidationOutcome(
            merged=merged,
            superseded=superseded,
            expired=expired,
            decayed=decayed,
            examined=examined,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        if self._metrics is not None:
            for action, count in (
                ("merged", merged), ("superseded", superseded),
                ("expired", expired), ("decayed", decayed),
            ):
                if count:
                    self._metrics.memory_consolidation_total.labels(action=action).inc(count)
        await self._publish(
            MemoryConsolidated(
                owner_id=owner_id, merged=merged, superseded=superseded,
                expired=expired, examined=examined,
            )
        )
        logger.info("memory consolidated", extra=asdict(outcome))
        return outcome

    # -- deletion ----------------------------------------------------------------------

    async def forget(self, memory_id: MemoryId, *, owner_id: UserId) -> bool:
        deleted = await self._store.delete(memory_id, owner_id=owner_id)
        if deleted:
            await self._vectors.delete(self._identity, point_ids=[memory_id.value])
        return deleted

    async def forget_owner(self, owner_id: UserId) -> int:
        """Erasure: every memory ``owner_id`` has, superseded ones included, from the store and
        the vector index. Returns how many were deleted."""
        removed = 0
        while True:
            records = await self._store.list_for_owner(
                owner_id, include_superseded=True, limit=200
            )
            if not records:
                return removed
            before = removed
            for record in records:
                if await self.forget(record.id, owner_id=owner_id):
                    removed += 1
            if removed == before:
                # Nothing in this page could be deleted; asking again would return the same page.
                raise MemoryStoreError(f"could not delete the memories of {owner_id.value!r}")

    async def forget_source(self, *, owner_id: UserId, source: str) -> int:
        """"Forget everything you learned from X."

        Only possible because every write records provenance. The vector points are removed via
        the same filter so the index does not retain orphans that would still be retrievable.
        """
        count = await self._store.delete_by_provenance(owner_id=owner_id, source=source)
        if count:
            await self._vectors.delete_by_filter(
                self._identity, filters={"source": source}, owner_id=owner_id
            )
        logger.info("memories forgotten by source", extra={"source": source, "count": count})
        return count

    async def _forget(self, record: MemoryRecord) -> None:
        try:
            await self._store.delete(record.id, owner_id=record.owner_id)
            await self._vectors.delete(self._identity, point_ids=[record.id.value])
        except Exception:
            logger.debug("memory deletion failed", exc_info=True)

    # -- helpers -----------------------------------------------------------------------

    async def _embed(self, text: str, *, for_query: bool = False) -> tuple[float, ...]:
        result = await self._embedder.embed([text], for_query=for_query)
        return result.first

    async def _find_duplicate(
        self,
        owner_id: UserId,
        kind: MemoryKind,
        embedding: Sequence[float],
        *,
        threshold: float,
        scope: str = SHARED_MEMORY_SCOPE,
    ) -> MemoryRecord | None:
        try:
            await self._ensure_index()
            raw = await self._vectors.search(
                self._identity,
                embedding,
                limit=6,
                owner_id=owner_id,
                include_global=False,
                filters={"kind": [str(kind)]},
            )
        except Exception:
            return None
        for point_id, score, payload in raw:
            if score >= threshold:
                record = await self._store.get(MemoryId(payload.get("memory_id", point_id)))
                if record is not None and record.is_active and record.scope == scope:
                    return record
        return None

    async def _enforce_quota(self, owner_id: UserId) -> None:
        """Bound per-user memory growth.

        Without a ceiling, an automated extractor writing on every turn grows a user's memory
        without limit -- which is slow to search, expensive to consolidate, and eventually a
        storage problem. Raising rather than silently evicting keeps the failure visible.
        """
        count = await self._store.count(owner_id)
        if count >= self._max_records:
            raise MemoryStoreError(
                "memory quota exceeded for this user; run consolidation or raise the limit",
                details={"count": count, "limit": self._max_records},
            )

    def _now(self):
        from datetime import datetime, timezone

        return self._clock.now() if self._clock is not None else datetime.now(timezone.utc)

    async def _publish(self, event: Any) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(event)
        except Exception:  # pragma: no cover - defensive
            logger.debug("memory event publication failed", exc_info=True)

    async def stats(self, owner_id: UserId) -> dict[str, int]:
        counts = {}
        for kind in MemoryKind:
            counts[str(kind)] = await self._store.count(owner_id, kind=kind)
        return counts
