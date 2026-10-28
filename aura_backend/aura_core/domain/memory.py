"""Memory entities (ADR-004).

Six kinds with genuinely different lifecycles, one record shape. The shape is uniform so the
retrieval facade can rank across kinds; the *policies* differ, and they live in
:class:`MemoryPolicy` rather than in scattered ``if kind == ...`` branches.

The legacy system has exactly one kind -- an LLM-extracted "user fact" in the same Qdrant
collection as document chunks -- with no decay, no consolidation, no provenance, and no
contradiction handling. Facts accumulate forever and contradictory ones are both retrieved.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .identifiers import MemoryId, SessionId, TurnId, UserId

__all__ = [
    "SHARED_MEMORY_SCOPE",
    "SCOPE_METADATA_KEY",
    "readable_scopes",
    "MemoryKind",
    "MemoryPolicy",
    "MEMORY_POLICIES",
    "Provenance",
    "MemoryRecord",
    "MemoryQuery",
    "MemoryHit",
    "ConsolidationOutcome",
]


#: The scope every persona can read (GAP-024). A record with no scope -- everything written
#: before scopes existed -- is in it, so scoping never hides a pre-existing memory.
SHARED_MEMORY_SCOPE = "shared"
#: Where a record's scope lives: in ``MemoryRecord.metadata`` rather than a new column, so every
#: memory store (in-memory, Postgres ``aura_memories.metadata`` JSONB) carries it unchanged.
SCOPE_METADATA_KEY = "memory_scope"
#: When decay was last applied to ``confidence`` (ISO-8601). Kept in metadata for the same reason
#: as the scope: no schema change, and every store already round-trips it.
DECAYED_AT_METADATA_KEY = "decayed_at"


def readable_scopes(scope: str | None) -> tuple[str, ...] | None:
    """The scopes a persona on ``scope`` may read: its own plus the shared one.

    ``None`` means "every scope" and is only for the memory's *owner* acting directly (the v3
    ``/memory`` endpoints) -- never for a persona's turn.
    """
    if scope is None:
        return None
    if scope == SHARED_MEMORY_SCOPE:
        return (SHARED_MEMORY_SCOPE,)
    return (scope, SHARED_MEMORY_SCOPE)


class MemoryKind(str, enum.Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    ENTITY = "entity"
    PROCEDURAL = "procedural"
    REFLECTION = "reflection"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    """Per-kind retention and ranking policy.

    ``half_life`` drives recency decay; ``w_*`` weight the three retrieval signals. Semantic
    memory weights similarity heavily and recency lightly (a fact from a year ago is still a
    fact); episodic memory does the opposite (what we said five minutes ago matters more than
    last month). Encoding that as data rather than as code is what makes the difference
    testable.
    """

    kind: MemoryKind
    half_life: timedelta
    ttl: timedelta | None
    w_similarity: float
    w_recency: float
    w_reinforcement: float
    min_confidence: float = 0.3
    consolidate: bool = True
    duplicate_threshold: float = 0.94
    max_records_per_owner: int = 10_000

    def recency_factor(self, age: timedelta) -> float:
        """Exponential decay with the configured half-life; 1.0 at age zero.

        A negative age is clamped to zero. Clock skew between replicas, and an injected clock
        running behind wall time, both produce one -- and an un-clamped negative exponent grows
        the factor above 1, which would *increase* confidence through the decay path.
        """
        if self.half_life.total_seconds() <= 0:
            return 1.0
        seconds = max(0.0, age.total_seconds())
        return math.pow(0.5, seconds / self.half_life.total_seconds())


MEMORY_POLICIES: Mapping[MemoryKind, MemoryPolicy] = {
    MemoryKind.WORKING: MemoryPolicy(
        kind=MemoryKind.WORKING,
        half_life=timedelta(minutes=5),
        ttl=timedelta(hours=1),
        w_similarity=0.5,
        w_recency=0.5,
        w_reinforcement=0.0,
        consolidate=False,
        max_records_per_owner=500,
    ),
    MemoryKind.EPISODIC: MemoryPolicy(
        kind=MemoryKind.EPISODIC,
        half_life=timedelta(days=3),
        ttl=timedelta(days=30),
        w_similarity=0.5,
        w_recency=0.4,
        w_reinforcement=0.1,
        duplicate_threshold=0.98,
        max_records_per_owner=50_000,
    ),
    MemoryKind.SEMANTIC: MemoryPolicy(
        kind=MemoryKind.SEMANTIC,
        half_life=timedelta(days=180),
        ttl=None,
        w_similarity=0.75,
        w_recency=0.10,
        w_reinforcement=0.15,
        min_confidence=0.6,
    ),
    MemoryKind.ENTITY: MemoryPolicy(
        kind=MemoryKind.ENTITY,
        half_life=timedelta(days=120),
        ttl=None,
        w_similarity=0.7,
        w_recency=0.15,
        w_reinforcement=0.15,
        min_confidence=0.5,
    ),
    MemoryKind.PROCEDURAL: MemoryPolicy(
        kind=MemoryKind.PROCEDURAL,
        half_life=timedelta(days=60),
        ttl=None,
        # Preferences are established by repetition, so reinforcement dominates.
        w_similarity=0.45,
        w_recency=0.15,
        w_reinforcement=0.40,
        min_confidence=0.5,
    ),
    MemoryKind.REFLECTION: MemoryPolicy(
        kind=MemoryKind.REFLECTION,
        half_life=timedelta(days=90),
        ttl=timedelta(days=365),
        w_similarity=0.6,
        w_recency=0.25,
        w_reinforcement=0.15,
        min_confidence=0.5,
    ),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_instant(value: Any) -> datetime | None:
    """An ISO-8601 stamp from metadata, or None when absent or unreadable (decay then starts over
    from the last access, which is the pre-stamp behaviour, rather than failing consolidation)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a memory came from.

    Mandatory for every written record. Two things depend on it: rolling back everything
    produced by a bad extractor version, and honouring "forget everything you learned from
    document X".
    """

    source: str
    turn_id: TurnId | None = None
    session_id: SessionId | None = None
    extractor: str | None = None
    extractor_version: str | None = None
    document_ref: str | None = None
    recorded_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("Provenance.source must be non-empty")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One memory, of any kind.

    ``superseded_by`` rather than deletion: when a newer fact contradicts an older one the older
    is marked, not removed. It stays available for audit, and the user may well be correcting
    the *newer* one.
    """

    id: MemoryId
    kind: MemoryKind
    owner_id: UserId
    content: str
    provenance: Provenance
    subject: str | None = None
    confidence: float = 0.7
    embedding_ref: str | None = None
    session_id: SessionId | None = None
    created_at: datetime = field(default_factory=_utcnow)
    last_accessed_at: datetime = field(default_factory=_utcnow)
    access_count: int = 0
    superseded_by: MemoryId | None = None
    expires_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        if not self.content.strip():
            raise ValueError("memory content must be non-empty")

    @property
    def policy(self) -> MemoryPolicy:
        return MEMORY_POLICIES[self.kind]

    @property
    def scope(self) -> str:
        """The persona scope this memory belongs to (``SHARED_MEMORY_SCOPE`` if unset)."""
        return str(self.metadata.get(SCOPE_METADATA_KEY) or SHARED_MEMORY_SCOPE)

    @property
    def is_active(self) -> bool:
        if self.superseded_by is not None:
            return False
        return not self.is_expired()

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or _utcnow()) >= self.expires_at

    def touched(self, *, now: datetime | None = None) -> "MemoryRecord":
        """Record an access. Reinforcement is what keeps frequently-used memories alive."""
        moment = now or _utcnow()
        return replace(self, last_accessed_at=moment, access_count=self.access_count + 1)

    def superseded(self, by: MemoryId) -> "MemoryRecord":
        return replace(self, superseded_by=by)

    def decayed(self, *, now: datetime | None = None) -> "MemoryRecord":
        """Apply confidence decay based on time since last access.

        Decay is applied to *confidence*, not to the retrieval score, so a memory that has gone
        stale eventually falls below ``min_confidence`` and is expired by consolidation rather
        than lingering at the bottom of every result set forever.
        """
        moment = now or _utcnow()

        # Blend toward the decayed value rather than replacing it: a high-confidence fact
        # should not become worthless purely through disuse.
        def blend(age: timedelta) -> float:
            return 0.5 + 0.5 * self.policy.recency_factor(age)

        # Consolidation persists the result and runs daily, so the stored confidence already
        # carries the decay up to the previous run. Multiplying it by the blend for the *total*
        # age again would compound -- a 180-day half-life fact was gone in about three weeks.
        # Apply only the step since the last decay instead: the steps telescope, so after any
        # number of runs ``confidence == undecayed * blend(total age)``, exactly what one run at
        # that age gives. Access resets the age, so a decay stamp older than the last access no
        # longer counts.
        previous = _parse_instant(self.metadata.get(DECAYED_AT_METADATA_KEY))
        if previous is not None and moment <= previous:
            # A clock behind the last run (replica skew, an injected clock) would make the ratio
            # below exceed 1 and *raise* confidence. Nothing has elapsed; nothing to apply.
            return self
        already = (
            previous - self.last_accessed_at
            if previous is not None and previous > self.last_accessed_at
            else timedelta(0)
        )
        new_confidence = self.confidence * blend(moment - self.last_accessed_at) / blend(already)
        metadata = {**self.metadata, DECAYED_AT_METADATA_KEY: moment.isoformat()}
        # Clamped rather than trusted: confidence is a validated field, and a decay step must
        # never be able to produce a value the entity would reject.
        return replace(
            self,
            confidence=round(min(1.0, max(0.0, new_confidence)), 6),
            metadata=metadata,
        )

    def retrieval_score(self, similarity: float, *, now: datetime | None = None) -> float:
        """Blend similarity, recency, and reinforcement using this kind's weights."""
        policy = self.policy
        moment = now or _utcnow()
        recency = policy.recency_factor(moment - self.last_accessed_at)
        reinforcement = math.log1p(self.access_count) / math.log(101)  # ~1.0 at 100 accesses
        return (
            policy.w_similarity * similarity
            + policy.w_recency * recency
            + policy.w_reinforcement * min(reinforcement, 1.0)
        ) * self.confidence


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    """A recall request.

    ``token_budget`` is part of the query, not an afterthought: memory competes with retrieved
    knowledge and conversation history for a fixed context, so the caller states its budget up
    front and the engine returns what fits.
    """

    owner_id: UserId
    text: str = ""
    kinds: tuple[MemoryKind, ...] = (
        MemoryKind.SEMANTIC,
        MemoryKind.PROCEDURAL,
        MemoryKind.ENTITY,
    )
    session_id: SessionId | None = None
    limit: int = 8
    min_score: float = 0.25
    token_budget: int | None = None
    include_superseded: bool = False
    subject: str | None = None
    #: Which persona scopes to read (``readable_scopes``). ``None`` reads every scope -- the
    #: owner's own view; a persona's turn always passes its scopes.
    scopes: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        if not self.kinds:
            raise ValueError("at least one memory kind must be requested")


@dataclass(frozen=True, slots=True)
class MemoryHit:
    record: MemoryRecord
    similarity: float
    score: float

    @property
    def content(self) -> str:
        return self.record.content


@dataclass(frozen=True, slots=True)
class ConsolidationOutcome:
    """Result of one consolidation pass, reported to metrics and the admin API."""

    merged: int = 0
    superseded: int = 0
    expired: int = 0
    decayed: int = 0
    examined: int = 0
    duration_ms: float = 0.0

    def __add__(self, other: "ConsolidationOutcome") -> "ConsolidationOutcome":
        return ConsolidationOutcome(
            merged=self.merged + other.merged,
            superseded=self.superseded + other.superseded,
            expired=self.expired + other.expired,
            decayed=self.decayed + other.decayed,
            examined=self.examined + other.examined,
            duration_ms=self.duration_ms + other.duration_ms,
        )
