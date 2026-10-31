"""Storage ports: vectors, documents, memories, the knowledge graph, and the cache.

Every method that reads user-scoped data takes an explicit ``owner_id``. That is deliberate:
tenant isolation is a *signature-level* obligation, not something an implementation may forget
to add to a filter. The legacy code partitions by passing ``user_id`` into an optional filter
argument that is ``None`` by default -- one missing argument leaks another user's data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.errors import ValidationError
from ..domain.events import DomainEvent
from ..domain.github_identity import GitHubIdentity
from ..domain.graph import FactProvenance
from ..domain.goals import GoalSnapshot
from ..domain.identifiers import DocumentId, MemoryId, PersonalOsRecordId, UserId
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
from ..domain.outbox import OutboxEntry
from ..domain.personal_os import PersonalOsRecord, RecordKind
from ..domain.user_keys import UserKeyring

__all__ = [
    "GLOBAL_OWNER",
    "require_point_owner",
    "VectorStore",
    "DocumentStore",
    "MemoryStore",
    "PersonalOsStore",
    "UserDataKeyStore",
    "GraphStore",
    "Cache",
    "GoalsPort",
    "EventOutbox",
    "InsightSink",
    "GitHubIdentityPort",
    "VectorPoint",
]


#: The only owner value that makes a vector point visible to every caller (CB-11, SEC-09). A
#: point without an ``owner_id``, or with an empty one, is refused on write and matches nobody on
#: read: "no owner" used to mean "global", so a write path that forgot to set the owner published
#: one user's content to all of them.
GLOBAL_OWNER = "global"


def require_point_owner(point: "VectorPoint") -> None:
    """Raise :class:`ValidationError` unless ``point`` names its owner (a user id or
    :data:`GLOBAL_OWNER`). Both vector adapters call this before writing anything."""
    owner = point.payload.get("owner_id")
    if not isinstance(owner, str) or not owner.strip():
        raise ValidationError(
            "a vector point must name its owner: a user id, or the explicit global sentinel",
            details={"point": point.id, "sentinel": GLOBAL_OWNER},
        )


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
        """The chunks of ``version`` while it is the document's *active* version.

        Empty once that version is superseded or tombstoned. Retrieval resolves chunk ids from a
        per-process lexical index through here, and another worker's index may still name a
        retired version; this filter is what keeps that text out of context (audit F-14). A
        caller retiring a version reads its chunks *before* superseding it.
        """
        ...

    async def purge_owner(self, owner_id: UserId) -> int:
        """Erasure: delete every document row and chunk ``owner_id`` owns, every version and
        status included -- a tombstone keeps chunk text, which an erasure must not. Returns how
        many document rows were removed. Never touches global (owner-less) content."""
        ...

    async def list_active(
        self, *, owner_id: UserId | None, limit: int = 100, offset: int = 0
    ) -> Sequence[Document]:
        ...

    async def set_cag_pin(
        self, document_id: DocumentId, *, owner_id: UserId | None, pinned: bool
    ) -> Document:
        """Toggle CAG pin on the active version. Pinning is not a new version.

        ``owner_id=None`` means the document must be global (admin path). A user may only
        pin documents they own. Raises :class:`~aura_core.domain.errors.NotFoundError` when
        the document is missing or not visible to the caller.
        """
        ...

    async def list_cag_pinned(self, *, owner_id: UserId | None) -> Sequence[Document]:
        """Active pinned documents: the caller's own pins plus every global pin.

        ``owner_id=None`` returns global pins only.
        """
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

    async def touch_many(
        self,
        memory_ids: Sequence[MemoryId],
        *,
        now: datetime,
        confidence_floor: float | None = None,
    ) -> int:
        """Reinforce records in place, field by field; return how many rows were touched.

        Increments ``access_count``, moves ``last_accessed_at`` forward (never back), and, when
        ``confidence_floor`` is given, raises ``confidence`` to at least that value (capped at 1).
        It must not write any other column, and it must skip superseded records.

        This exists because reinforcement used to be ``update(record.touched())`` -- a
        whole-row write from the snapshot a recall read seconds earlier. A supersede, decay or
        edit that landed in between was silently undone: a superseded memory came back to life
        (audit F-16 / AR-06). Each implementation makes this one atomic statement (or one
        critical section), so it cannot interleave with a concurrent whole-row write.
        """
        ...

    async def delete(self, memory_id: MemoryId, *, owner_id: UserId) -> bool:
        ...

    async def delete_by_provenance(self, *, owner_id: UserId, source: str) -> int:
        """"Forget everything you learned from X." Requires the provenance every write carries."""
        ...

    async def count(self, owner_id: UserId, *, kind: MemoryKind | None = None) -> int:
        ...


@runtime_checkable
class PersonalOsStore(Protocol):
    """Persistence for the user's personal-OS records: tasks, calendar events, finance and
    health records (Phase C2; ADR-032).

    Deliberately one port for all four kinds rather than four: they differ in ``fields``, not in
    how they are stored, listed, or torn down, and a caller filters by ``kind`` the same way it
    filters :class:`MemoryStore` by :class:`MemoryKind`.

    In production this is the app's own synced tables (``PostgresSyncedPersonalOsStore``), so a
    record written here reaches the user's devices on their next sync and the agent sees what the
    user created in the app.
    """

    async def put(self, record: PersonalOsRecord) -> PersonalOsRecord | None:
        """Store a new record. May return the record as stored when it differs from the one
        given -- e.g. a health record merged onto that day's existing row, under that row's id.
        ``None`` means it was stored as given."""
        ...

    async def get(self, record_id: PersonalOsRecordId, *, owner_id: UserId) -> PersonalOsRecord | None:
        ...

    async def list_for_owner(
        self, owner_id: UserId, *, kind: RecordKind | None = None, limit: int = 100,
    ) -> Sequence[PersonalOsRecord]:
        ...

    async def update(self, record: PersonalOsRecord) -> None:
        ...

    async def delete(self, record_id: PersonalOsRecordId, *, owner_id: UserId) -> bool:
        ...


@runtime_checkable
class UserDataKeyStore(Protocol):
    """Per-user data keys (ADR-032): generated server-side, stored only wrapped by the server's
    key-encryption key, and handed to the user's own devices so they and the server encrypt the
    synced personal-OS fields with the same key."""

    async def keyring(self, owner_id: UserId, *, create: bool = True) -> UserKeyring | None:
        """The owner's keys, unwrapped. With ``create`` a first primary key is generated when the
        owner has none; without it, ``None`` is returned instead."""
        ...

    async def rotate(self, owner_id: UserId) -> UserKeyring:
        """Demote the current primary key to decrypt-only and make a new primary, atomically."""
        ...


@runtime_checkable
class GraphStore(Protocol):
    """Entity/relation storage with neighbourhood queries."""

    async def apply(self, delta: GraphDelta, *, owner_id: UserId | None) -> None:
        """Apply a delta transactionally.

        Entities merge on canonical key; relations upsert on ``(subject, predicate, object)``.
        Each source in a fact's ``sources`` becomes one provenance record (the higher confidence
        wins when the same source asserts the same fact twice), and the fact's confidence is
        recomputed from its live provenance with :func:`~aura_core.domain.graph.combine_confidence`.
        A fact with no source is stored under the empty source id -- the store does not refuse it,
        but the ingestion pipeline never produces one (ADR-005, "Knowledge graph").
        Re-asserting an expired fact makes it current again.
        """
        ...

    async def get_entity(self, key: str, *, owner_id: UserId | None) -> Entity | None:
        """One entity by key, current *or* expired -- the caller can read ``valid_to``."""
        ...

    async def find_entities(
        self, *, owner_id: UserId | None, name: str | None = None, type: str | None = None,
        limit: int = 50, include_expired: bool = False,
    ) -> Sequence[Entity]:
        ...

    async def neighbours(
        self, key: str, *, owner_id: UserId | None, depth: int = 1, limit: int = 50
    ) -> tuple[Sequence[Entity], Sequence[Relation]]:
        """Bounded traversal over *current* facts. ``depth`` is capped by the implementation --
        an unbounded graph walk on user input is a denial-of-service primitive."""
        ...

    async def list_relations(
        self, *, owner_id: UserId | None, entity_keys: Sequence[str] | None = None,
        limit: int = 100, include_expired: bool = False,
    ) -> Sequence[Relation]:
        """Relations of one owner, optionally only those touching ``entity_keys``."""
        ...

    async def provenance(
        self, *, owner_id: UserId | None, entity_key: str | None = None,
        relation_key: tuple[str, str, str] | None = None,
    ) -> Sequence[FactProvenance]:
        """Every provenance record (live and retired) of one entity or one relation."""
        ...

    async def retire_document(
        self, document_id: str, *, owner_id: UserId | None, keep_version: int | None = None,
        at: datetime | None = None,
    ) -> int:
        """A document was superseded: retire the provenance of every version but ``keep_version``.

        Facts left with no live provenance are expired (``valid_to`` set), not deleted. Returns
        the number of facts that expired.
        """
        ...

    async def purge_document(self, document_id: str, *, owner_id: UserId | None) -> int:
        """A document was deleted: remove its provenance (every version) and every fact left with
        no provenance at all. Returns the number of facts removed."""
        ...

    async def revert(self, source: str, *, owner_id: UserId | None) -> int:
        """Undo everything a given source contributed. Required for ADR-008 rollback.

        Facts another source also asserts survive with that source's evidence only.
        """
        ...


@runtime_checkable
class EntityExtractor(Protocol):
    """Text in, candidate graph delta out.

    Output is a *candidate*: the ingestion pipeline (``knowledge/graph_pipeline.py``) normalises,
    validates, attaches provenance and calibrates it before anything is stored. An extractor
    backed by a model is untrusted in exactly the way retrieved text is -- its output is data to
    validate, never instructions.
    """

    name: str

    async def extract(self, text: str, *, source: str = "") -> GraphDelta:
        ...


@runtime_checkable
class GoalsPort(Protocol):
    """Read-only access to a user's active goals, plaintext fields only (Phase D2).

    Deliberately narrower than :class:`PersonalOsStore`: this port never writes, and never
    returns ``title_enc``/``description_enc`` -- callers (the context engine, Phase D3's
    prioritisation tool) need to reason about goals, not display them.
    """

    async def active_summaries(
        self, owner_id: UserId, *, limit: int = 5
    ) -> Sequence[GoalSnapshot]:
        ...


@runtime_checkable
class GitHubIdentityPort(Protocol):
    """One connected GitHub account per user (per-user OAuth connect flow).

    Backs the engineer agent's GitHub tools once ``GitHubSettings.oauth_enabled`` -- each user's
    tool calls resolve their *own* stored identity instead of the single deployment-wide PAT
    ``GitHubSettings.token`` provides. Read and write both belong on the same port, unlike
    :class:`GoalsPort`'s read-only split, because this table's only writer besides the OAuth
    callback is token refresh, which happens lazily on the same path that reads it.
    """

    async def get(self, user_id: UserId) -> GitHubIdentity | None:
        ...

    async def upsert(self, user_id: UserId, identity: GitHubIdentity) -> None:
        """Insert or replace the caller's identity. A user has at most one connected GitHub
        account at a time -- reconnecting overwrites rather than accumulating rows."""
        ...

    async def delete(self, user_id: UserId) -> None:
        """Disconnect. Idempotent: deleting an already-absent identity is not an error."""
        ...


@runtime_checkable
class EventOutbox(Protocol):
    """Durable, at-least-once delivery for domain events a live handler failure must not lose
    (Phase E1). ``InMemoryEventBus.publish`` catches and logs every handler exception -- correct
    for metrics/audit/cache-invalidation reactions, wrong for a reaction whose only record of
    happening is that log line. A durable-marked event is written here before it is handed to
    any handler; :mod:`aura_core.learning.jobs`' outbox drain is the only thing that ever
    delivers it onward, so there is exactly one delivery path to reason about, not two racing
    ones.

    ``claim_batch`` must be safe for multiple concurrent drain workers -- an entry claimed by one
    worker must not also be claimed by another until it times back out or the claiming worker
    marks it processed/failed.
    """

    async def enqueue(self, event: DomainEvent) -> str:
        """Persist ``event``, returning the new entry's id."""
        ...

    async def claim_batch(self, *, limit: int = 50) -> Sequence[OutboxEntry]:
        ...

    async def mark_processed(self, entry_id: str) -> None:
        ...

    async def mark_failed(self, entry_id: str, *, error: str, max_attempts: int = 5) -> None:
        """Record a failed delivery attempt. Under ``max_attempts`` the entry is left claimable
        again; at or over it, the entry is dead-lettered (never claimed again, `error` kept for
        diagnosis) rather than retried forever."""
        ...


@runtime_checkable
class InsightSink(Protocol):
    """Write path for a proactive, per-user surfaced item (Phase E2's triggers, the daily
    briefing). Deliberately separate from :class:`PersonalOsStore`: an insight is generated by
    the system about the user, not a record the user or an agent authored."""

    async def surface(
        self, owner_id: UserId, *, insight_id: str, kind: str, message: str, explanation: str,
    ) -> None:
        """Idempotent on ``insight_id`` -- a redelivered event (Phase E1's at-least-once
        outbox) must not pile up duplicate insights."""
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
