"""Storage ports: vectors, documents, memories, the knowledge graph, and the cache.

Every method that reads user-scoped data takes an explicit ``owner_id``. That is deliberate:
tenant isolation is a *signature-level* obligation, not something an implementation may forget
to add to a filter. The legacy code partitions by passing ``user_id`` into an optional filter
argument that is ``None`` by default -- one missing argument leaks another user's data.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.identifiers import DocumentId, MemoryId, UserId
from ..domain.knowledge import (
    Chunk,
    Document,
    Entity,
    GraphDelta,
    IndexIdentity,
    Relation,
    RetrievedChunk,
)
from ..domain.memory import MemoryKind, MemoryRecord

__all__ = [
    "VectorStore",
    "DocumentStore",
    "MemoryStore",
    "GraphStore",
    "Cache",
    "VectorPoint",
]


class VectorPoint:
    """One vector plus its payload, ready for upsert."""

    __slots__ = ("id", "vector", "payload")

    def __init__(self, id: str, vector: Sequence[float], payload: Mapping[str, Any]) -> None:
        self.id = id
        self.vector = tuple(float(v) for v in vector)
        self.payload = dict(payload)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"VectorPoint(id={self.id!r}, dim={len(self.vector)})"


@runtime_checkable
class VectorStore(Protocol):
    """Approximate nearest-neighbour index, addressed by :class:`IndexIdentity`.

    The identity -- not a bare collection name -- is the unit of addressing throughout, which is
    what makes it structurally impossible to write 768-dimensional vectors into a
    384-dimensional index (SEC-03).
    """

    async def ensure_index(self, identity: IndexIdentity) -> None:
        """Create the index if absent. **Never** deletes or recreates an existing one.

        If an index with the same physical name exists with different parameters, raise
        :class:`~aura_core.domain.errors.ConflictError`. Deleting data is an explicit,
        audited admin operation, never a side effect of a mismatch.
        """
        ...

    async def index_exists(self, identity: IndexIdentity) -> bool:
        ...

    async def upsert(self, identity: IndexIdentity, points: Sequence[VectorPoint]) -> int:
        ...

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
    ) -> Sequence[tuple[str, float, Mapping[str, Any]]]:
        """Return ``(point_id, score, payload)`` best-first.

        ``owner_id=None`` means "global content only", not "all content". There is no way to
        express an unfiltered cross-tenant search through this port; that operation belongs to
        admin tooling with its own audited path.
        """
        ...

    async def delete(self, identity: IndexIdentity, *, point_ids: Sequence[str]) -> int:
        ...

    async def delete_by_filter(
        self, identity: IndexIdentity, *, filters: Mapping[str, Any], owner_id: UserId | None
    ) -> int:
        ...

    async def count(self, identity: IndexIdentity, *, owner_id: UserId | None = None) -> int:
        ...

    async def set_alias(self, alias: str, identity: IndexIdentity) -> None:
        """Atomically point ``alias`` at ``identity``. The promotion and rollback primitive
        (ADR-007) -- O(1), no data movement."""
        ...

    async def resolve_alias(self, alias: str) -> IndexIdentity | None:
        ...

    async def drop_index(self, identity: IndexIdentity) -> None:
        """Destroy an index. Implementations must refuse while any alias points at it."""
        ...


@runtime_checkable
class DocumentStore(Protocol):
    """Source of truth for document text and versions (ADR-007).

    The vector store holds derived data and can always be rebuilt from here; that asymmetry is
    what makes reindexing a routine operation.
    """

    async def put(self, document: Document, chunks: Sequence[Chunk]) -> None:
        ...

    async def get(self, document_id: DocumentId, *, version: int | None = None) -> Document | None:
        """``version=None`` returns the active version."""
        ...

    async def get_by_hash(self, content_hash: str, *, owner_id: UserId | None) -> Document | None:
        """Ingestion idempotency: unchanged content must not create a new version, or nightly
        refreshes would churn the index every run."""
        ...

    async def chunks_for(self, document_id: DocumentId, version: int) -> Sequence[Chunk]:
        ...

    async def list_active(
        self, *, owner_id: UserId | None, limit: int = 100, offset: int = 0
    ) -> Sequence[Document]:
        ...

    async def iter_all_chunks(self, *, batch_size: int = 500):
        """Async iterator over every active chunk, for index rebuilds.

        Streaming rather than materialising: a rebuild must not be bounded by available RAM.
        """
        ...

    async def supersede(self, document_id: DocumentId, *, old_version: int, new_version: int) -> None:
        ...

    async def tombstone(self, document_id: DocumentId, *, owner_id: UserId | None) -> int:
        """Mark deleted and return the number of chunks to purge from indexes."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """Persistence for :class:`MemoryRecord` (ADR-004).

    Vector similarity is *not* this port's job -- embeddings live in the vector store. This port
    owns the relational questions memory actually needs: "all active semantic records for this
    user with subject X", "everything derived from extractor version Y".
    """

    async def put(self, record: MemoryRecord) -> None:
        ...

    async def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        ...

    async def list_for_owner(
        self,
        owner_id: UserId,
        *,
        kinds: Sequence[MemoryKind] | None = None,
        subject: str | None = None,
        include_superseded: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[MemoryRecord]:
        ...

    async def get_many(self, memory_ids: Sequence[MemoryId]) -> Sequence[MemoryRecord]:
        """Batch fetch after a vector search returns ids -- one round trip, not N."""
        ...

    async def update(self, record: MemoryRecord) -> None:
        ...

    async def delete(self, memory_id: MemoryId, *, owner_id: UserId) -> bool:
        ...

    async def delete_by_provenance(self, *, owner_id: UserId, source: str) -> int:
        """"Forget everything you learned from X." Requires the provenance every write carries."""
        ...

    async def count(self, owner_id: UserId, *, kind: MemoryKind | None = None) -> int:
        ...


@runtime_checkable
class GraphStore(Protocol):
    """Entity/relation storage with neighbourhood queries."""

    async def apply(self, delta: GraphDelta, *, owner_id: UserId | None) -> None:
        """Apply a delta transactionally. Entities merge on canonical key; relations upsert on
        ``(subject, predicate, object)`` taking the higher confidence."""
        ...

    async def get_entity(self, key: str, *, owner_id: UserId | None) -> Entity | None:
        ...

    async def find_entities(
        self, *, owner_id: UserId | None, name: str | None = None, type: str | None = None,
        limit: int = 50,
    ) -> Sequence[Entity]:
        ...

    async def neighbours(
        self, key: str, *, owner_id: UserId | None, depth: int = 1, limit: int = 50
    ) -> tuple[Sequence[Entity], Sequence[Relation]]:
        """Bounded traversal. ``depth`` is capped by the implementation -- an unbounded graph
        walk on user input is a denial-of-service primitive."""
        ...

    async def revert(self, source: str, *, owner_id: UserId | None) -> int:
        """Undo everything a given source contributed. Required for ADR-008 rollback."""
        ...


@runtime_checkable
class Cache(Protocol):
    """Key/value cache with TTL.

    Deliberately minimal. Callers must treat every operation as best-effort: a cache that is
    down degrades latency, never correctness. Implementations therefore swallow their own
    connection errors and report a miss rather than raising -- the one place in this codebase
    where silent degradation is the right behaviour, because the fallback is recomputation.
    """

    async def get(self, key: str) -> bytes | None:
        ...

    async def set(self, key: str, value: bytes, *, ttl_s: int | None = None) -> None:
        ...

    async def delete(self, key: str) -> None:
        ...

    async def incr(self, key: str, *, ttl_s: int | None = None) -> int:
        """Atomic increment, returning the new value. Used by the rate limiter, where atomicity
        across replicas is the whole point."""
        ...

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, bytes]:
        ...
