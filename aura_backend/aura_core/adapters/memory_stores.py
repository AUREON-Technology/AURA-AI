"""In-memory adapters for every storage port.

These are the reference implementations. They exist for three reasons, in order of importance:

1. **The test suite runs without infrastructure.** Every engine, the kernel, and the API can be
   exercised end to end on bare CPython. That is the difference between a test suite that runs
   in CI and one that requires a Docker Compose stack nobody starts locally.
2. **They define the contract.** A Qdrant or Postgres adapter is correct if it behaves like
   these; ``tests/unit/adapters/test_store_contract.py`` runs the same suite against both.
3. **Local development needs no services.** ``python -m aura_core.cli chat`` works on a fresh
   clone.

They are refused in staging and production by ``AuraSettings.validate()``: an in-memory vector
store loses every document on restart, which is not a system of record.

Concurrency: guarded by ``asyncio.Lock`` where a read-modify-write sequence could interleave.
Single-process asyncio does not need a lock for atomic dict operations, but ``upsert`` and
``apply`` are not atomic, and a concurrent turn can interleave at every ``await``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Sequence

from ..domain.errors import ConflictError, ValidationError
from ..domain.goals import GoalSnapshot
from ..domain.identifiers import DocumentId, MemoryId, PersonalOsRecordId, UserId
from ..domain.knowledge import (
    Chunk,
    Document,
    DocumentStatus,
    Entity,
    GraphDelta,
    IndexIdentity,
    Relation,
)
from ..domain.memory import MemoryKind, MemoryRecord
from ..domain.personal_os import PersonalOsRecord, RecordKind
from ..llm.embeddings import cosine_similarity
from ..ports.storage import VectorPoint

__all__ = [
    "InMemoryVectorStore",
    "InMemoryDocumentStore",
    "InMemoryMemoryStore",
    "InMemoryPersonalOsStore",
    "InMemoryGraphStore",
    "InMemoryGoalsPort",
]


class InMemoryVectorStore:
    """Exhaustive-scan vector search.

    O(n) per query with no ANN index. That is correct rather than fast, which is the right
    trade for a reference implementation -- an approximate index here would make test
    assertions about ranking non-deterministic, and the production adapter is Qdrant.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, dict[str, VectorPoint]] = {}
        self._identities: dict[str, IndexIdentity] = {}
        self._aliases: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def ensure_index(self, identity: IndexIdentity) -> None:
        name = identity.physical_name
        async with self._lock:
            existing = self._identities.get(name)
            if existing is None:
                self._indexes[name] = {}
                self._identities[name] = identity
                return
            if existing != identity:
                # Never silently reuse an index whose parameters differ -- the failure mode this
                # whole design exists to prevent (SEC-03).
                raise ConflictError(
                    f"index {name} exists with different parameters",
                    details={"existing": str(existing), "requested": str(identity)},
                )

    async def index_exists(self, identity: IndexIdentity) -> bool:
        return identity.physical_name in self._indexes

    async def upsert(self, identity: IndexIdentity, points: Sequence[VectorPoint]) -> int:
        await self.ensure_index(identity)
        index = self._indexes[identity.physical_name]
        async with self._lock:
            for point in points:
                if len(point.vector) != identity.dimension:
                    raise ValidationError(
                        f"vector dimension {len(point.vector)} does not match index "
                        f"dimension {identity.dimension}",
                        details={"index": identity.physical_name},
                    )
                index[point.id] = point
        return len(points)

    @staticmethod
    def _matches(payload: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
        if not filters:
            return True
        for key, wanted in filters.items():
            value = payload.get(key)
            if isinstance(wanted, (list, tuple, set)):
                if value not in wanted:
                    return False
            elif value != wanted:
                return False
        return True

    async def search(
        self,
        identity: IndexIdentity,
        vector: Sequence[float],
        *,
        limit: int,
        owner_id: UserId | None,
        include_global: bool = True,
        filters: Mapping[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[str, float, Mapping[str, Any]]]:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return []

        # Tenant isolation is applied before scoring, not after: filtering a scored list would
        # mean a cross-tenant document could displace a legitimate one out of the top-k.
        owner_value = owner_id.value if owner_id else None
        results: list[tuple[str, float, Mapping[str, Any]]] = []
        for point in index.values():
            payload_owner = point.payload.get("owner_id")
            if owner_value is None:
                if payload_owner not in (None, "", "global"):
                    continue
            elif payload_owner != owner_value:
                if not (include_global and payload_owner in (None, "", "global")):
                    continue
            if not self._matches(point.payload, filters):
                continue
            score = cosine_similarity(vector, point.vector)
            if score_threshold is not None and score < score_threshold:
                continue
            results.append((point.id, score, point.payload))

        results.sort(key=lambda item: item[1], reverse=True)
        return results[:limit]

    async def delete(self, identity: IndexIdentity, *, point_ids: Sequence[str]) -> int:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return 0
        async with self._lock:
            return sum(1 for pid in point_ids if index.pop(pid, None) is not None)

    async def delete_by_filter(
        self, identity: IndexIdentity, *, filters: Mapping[str, Any], owner_id: UserId | None
    ) -> int:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return 0
        owner_value = owner_id.value if owner_id else None
        async with self._lock:
            doomed = [
                pid
                for pid, point in index.items()
                if self._matches(point.payload, filters)
                and (owner_value is None or point.payload.get("owner_id") == owner_value)
            ]
            for pid in doomed:
                index.pop(pid, None)
        return len(doomed)

    async def count(self, identity: IndexIdentity, *, owner_id: UserId | None = None) -> int:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return 0
        if owner_id is None:
            return len(index)
        return sum(1 for p in index.values() if p.payload.get("owner_id") == owner_id.value)

    async def set_alias(self, alias: str, identity: IndexIdentity) -> None:
        await self.ensure_index(identity)
        self._aliases[alias] = identity.physical_name

    async def resolve_alias(self, alias: str) -> IndexIdentity | None:
        physical = self._aliases.get(alias)
        return self._identities.get(physical) if physical else None

    async def drop_index(self, identity: IndexIdentity) -> None:
        name = identity.physical_name
        if name in self._aliases.values():
            raise ConflictError(
                f"cannot drop {name}: an alias still points at it",
                details={"aliases": [a for a, p in self._aliases.items() if p == name]},
            )
        self._indexes.pop(name, None)
        self._identities.pop(name, None)

    def index_names(self) -> list[str]:
        return sorted(self._indexes)


class InMemoryDocumentStore:
    def __init__(self) -> None:
        self._documents: dict[tuple[str, int], Document] = {}
        self._chunks: dict[tuple[str, int], list[Chunk]] = {}
        self._by_hash: dict[tuple[str | None, str], tuple[str, int]] = {}
        self._lock = asyncio.Lock()

    async def put(self, document: Document, chunks: Sequence[Chunk]) -> None:
        key = (document.id.value, document.version)
        async with self._lock:
            self._documents[key] = document
            self._chunks[key] = list(chunks)
            owner = document.owner_id.value if document.owner_id else None
            self._by_hash[(owner, document.content_hash)] = key

    async def get(self, document_id: DocumentId, *, version: int | None = None) -> Document | None:
        if version is not None:
            return self._documents.get((document_id.value, version))
        candidates = [
            d for (doc_id, _), d in self._documents.items()
            if doc_id == document_id.value and d.status is DocumentStatus.ACTIVE
        ]
        return max(candidates, key=lambda d: d.version) if candidates else None

    async def get_by_hash(self, content_hash: str, *, owner_id: UserId | None) -> Document | None:
        owner = owner_id.value if owner_id else None
        key = self._by_hash.get((owner, content_hash))
        return self._documents.get(key) if key else None

    async def chunks_for(self, document_id: DocumentId, version: int) -> list[Chunk]:
        return list(self._chunks.get((document_id.value, version), []))

    async def list_active(
        self, *, owner_id: UserId | None, limit: int = 100, offset: int = 0
    ) -> list[Document]:
        owner = owner_id.value if owner_id else None
        active = [
            d for d in self._documents.values()
            if d.status is DocumentStatus.ACTIVE
            and ((d.owner_id.value if d.owner_id else None) == owner)
        ]
        active.sort(key=lambda d: (d.id.value, -d.version))
        return active[offset : offset + limit]

    async def iter_all_chunks(self, *, batch_size: int = 500) -> AsyncIterator[list[Chunk]]:
        """Stream chunks for index rebuilds.

        Streaming rather than materialising: a rebuild must not be bounded by available memory,
        and the Postgres adapter implementing this port will page over a cursor.
        """
        batch: list[Chunk] = []
        for (doc_id, version), document in list(self._documents.items()):
            if document.status is not DocumentStatus.ACTIVE:
                continue
            for chunk in self._chunks.get((doc_id, version), []):
                batch.append(chunk)
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch

    async def supersede(self, document_id: DocumentId, *, old_version: int, new_version: int) -> None:
        key = (document_id.value, old_version)
        document = self._documents.get(key)
        if document is not None:
            self._documents[key] = document.superseded()

    async def tombstone(self, document_id: DocumentId, *, owner_id: UserId | None) -> int:
        removed = 0
        owner = owner_id.value if owner_id else None
        async with self._lock:
            for key, document in list(self._documents.items()):
                if key[0] != document_id.value:
                    continue
                if (document.owner_id.value if document.owner_id else None) != owner:
                    continue
                from dataclasses import replace

                self._documents[key] = replace(document, status=DocumentStatus.TOMBSTONED)
                removed += len(self._chunks.get(key, []))
        return removed


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: MemoryRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        return self._records.get(memory_id.value)

    async def get_many(self, memory_ids: Sequence[MemoryId]) -> list[MemoryRecord]:
        return [self._records[m.value] for m in memory_ids if m.value in self._records]

    async def list_for_owner(
        self,
        owner_id: UserId,
        *,
        kinds: Sequence[MemoryKind] | None = None,
        subject: str | None = None,
        include_superseded: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        wanted = set(kinds) if kinds else None
        matches = [
            r for r in self._records.values()
            if r.owner_id == owner_id
            and (wanted is None or r.kind in wanted)
            and (subject is None or r.subject == subject)
            and (include_superseded or r.superseded_by is None)
        ]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[offset : offset + limit]

    async def update(self, record: MemoryRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def delete(self, memory_id: MemoryId, *, owner_id: UserId) -> bool:
        record = self._records.get(memory_id.value)
        if record is None or record.owner_id != owner_id:
            # Ownership is checked on delete, not assumed. A memory id is not a capability.
            return False
        async with self._lock:
            self._records.pop(memory_id.value, None)
        return True

    async def delete_by_provenance(self, *, owner_id: UserId, source: str) -> int:
        async with self._lock:
            doomed = [
                mid for mid, r in self._records.items()
                if r.owner_id == owner_id and r.provenance.source == source
            ]
            for mid in doomed:
                self._records.pop(mid, None)
        return len(doomed)

    async def count(self, owner_id: UserId, *, kind: MemoryKind | None = None) -> int:
        return sum(
            1 for r in self._records.values()
            if r.owner_id == owner_id and (kind is None or r.kind == kind)
        )


class InMemoryPersonalOsStore:
    """Reference implementation of :class:`~aura_core.ports.storage.PersonalOsStore` (Phase C2)."""

    def __init__(self) -> None:
        self._records: dict[str, PersonalOsRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: PersonalOsRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def get(
        self, record_id: PersonalOsRecordId, *, owner_id: UserId
    ) -> PersonalOsRecord | None:
        record = self._records.get(record_id.value)
        if record is None or record.owner_id != owner_id:
            return None
        return record

    async def list_for_owner(
        self, owner_id: UserId, *, kind: RecordKind | None = None, limit: int = 100,
    ) -> list[PersonalOsRecord]:
        matches = [
            r for r in self._records.values()
            if r.owner_id == owner_id and (kind is None or r.kind == kind)
        ]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[:limit]

    async def update(self, record: PersonalOsRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def delete(self, record_id: PersonalOsRecordId, *, owner_id: UserId) -> bool:
        record = self._records.get(record_id.value)
        if record is None or record.owner_id != owner_id:
            return False
        async with self._lock:
            self._records.pop(record_id.value, None)
        return True


_FAR_FUTURE = datetime(9999, 12, 31, tzinfo=timezone.utc)


class InMemoryGoalsPort:
    """Reference implementation of :class:`~aura_core.ports.storage.GoalsPort` (Phase D2).

    Test/local-dev only: the real source of truth is the legacy ``goals`` table synced from the
    Flutter client, which only :class:`~aura_core.adapters.postgres_goals.PostgresGoalsPort`
    can see. ``seed`` lets a test populate goals directly, since there is no write path here.
    """

    def __init__(self) -> None:
        self._by_owner: dict[str, list[GoalSnapshot]] = {}

    def seed(self, owner_id: UserId, snapshots: Sequence[GoalSnapshot]) -> None:
        self._by_owner.setdefault(owner_id.value, []).extend(snapshots)

    async def active_summaries(
        self, owner_id: UserId, *, limit: int = 5
    ) -> Sequence[GoalSnapshot]:
        goals = [g for g in self._by_owner.get(owner_id.value, []) if g.status == "active"]
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        goals.sort(
            key=lambda g: (
                priority_rank.get(g.priority, 1),
                g.target_date or _FAR_FUTURE,
            )
        )
        return goals[:limit]


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._entities: dict[tuple[str | None, str], Entity] = {}
        self._relations: dict[tuple[str | None, str, str, str], Relation] = {}
        self._lock = asyncio.Lock()

    async def apply(self, delta: GraphDelta, *, owner_id: UserId | None) -> None:
        owner = owner_id.value if owner_id else None
        async with self._lock:
            for entity in delta.entities:
                key = (owner, entity.key)
                existing = self._entities.get(key)
                # Merge rather than overwrite: a second mention of the same entity should add
                # attributes and aliases, not replace what was already known.
                self._entities[key] = existing.merged_with(entity) if existing else entity
            for relation in delta.relations:
                key = (owner, relation.subject_key, relation.predicate, relation.object_key)
                existing = self._relations.get(key)
                if existing is None or relation.confidence > existing.confidence:
                    self._relations[key] = relation

    async def get_entity(self, key: str, *, owner_id: UserId | None) -> Entity | None:
        return self._entities.get((owner_id.value if owner_id else None, key))

    async def find_entities(
        self, *, owner_id: UserId | None, name: str | None = None, type: str | None = None,
        limit: int = 50,
    ) -> list[Entity]:
        owner = owner_id.value if owner_id else None
        needle = name.lower() if name else None
        matches = [
            e for (o, _), e in self._entities.items()
            if o == owner
            and (type is None or e.type == type)
            and (
                needle is None
                or needle in e.name.lower()
                or any(needle in alias.lower() for alias in e.aliases)
            )
        ]
        matches.sort(key=lambda e: e.mention_count, reverse=True)
        return matches[:limit]

    async def neighbours(
        self, key: str, *, owner_id: UserId | None, depth: int = 1, limit: int = 50
    ) -> tuple[list[Entity], list[Relation]]:
        """Breadth-first traversal with a hard depth cap.

        Depth is clamped to 3 regardless of what the caller asks for: an unbounded graph walk
        driven by model-chosen arguments is a denial-of-service primitive.
        """
        owner = owner_id.value if owner_id else None
        depth = max(1, min(depth, 3))

        frontier = {key}
        seen = {key}
        edges: list[Relation] = []
        for _ in range(depth):
            next_frontier: set[str] = set()
            for (o, subject, _predicate, obj), relation in self._relations.items():
                if o != owner:
                    continue
                if subject in frontier and obj not in seen:
                    edges.append(relation)
                    next_frontier.add(obj)
                elif obj in frontier and subject not in seen:
                    edges.append(relation)
                    next_frontier.add(subject)
                if len(edges) >= limit:
                    break
            seen |= next_frontier
            frontier = next_frontier
            if not frontier or len(edges) >= limit:
                break

        entities = [self._entities[(owner, k)] for k in seen if (owner, k) in self._entities]
        return entities, edges[:limit]

    async def revert(self, source: str, *, owner_id: UserId | None) -> int:
        owner = owner_id.value if owner_id else None
        async with self._lock:
            doomed_relations = [
                k for k, r in self._relations.items() if k[0] == owner and source in r.sources
            ]
            for k in doomed_relations:
                self._relations.pop(k, None)
            doomed_entities = [
                k for k, e in self._entities.items()
                if k[0] == owner and e.sources == (source,)
            ]
            for k in doomed_entities:
                self._entities.pop(k, None)
        return len(doomed_relations) + len(doomed_entities)

    def size(self) -> tuple[int, int]:
        return len(self._entities), len(self._relations)
