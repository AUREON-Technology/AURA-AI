"""Domain events.

Event-driven where it pays for itself (ADR: "event-driven architecture where beneficial"):
cross-cutting reactions -- audit, metrics, cache invalidation, index maintenance, notifications
-- subscribe to events rather than being called explicitly from the engine that caused them.
That keeps the engines focused and lets a deployment turn a reaction off without editing the
producer.

Events are **facts about the past**: past tense names, immutable, no behaviour, no references to
live objects. They carry ids and counts, never content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .identifiers import (
    CorrelationId,
    DocumentId,
    GenerationId,
    MemoryId,
    SessionId,
    TurnId,
    UserId,
)

__all__ = [
    "DomainEvent",
    "TurnStarted",
    "TurnCompleted",
    "TurnFailed",
    "SafetyTriggered",
    "ProviderFailedOver",
    "ProviderHealthChanged",
    "ToolInvoked",
    "MemoryWritten",
    "MemoryConsolidated",
    "DocumentIngested",
    "DocumentSuperseded",
    "IndexBuilt",
    "IndexSwitched",
    "KnowledgeRefreshCompleted",
    "ApprovalRequested",
    "ApprovalDecided",
    "ModelPromoted",
    "BudgetExhausted",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base event.

    ``correlation_id`` is optional because some events (nightly refresh, consolidation) are not
    caused by a user turn. When present it links the event to logs, metrics exemplars, traces,
    and the audit record for that turn.
    """

    correlation_id: CorrelationId | None = None
    occurred_at: datetime = field(default_factory=_utcnow)

    @property
    def name(self) -> str:
        return type(self).__name__

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        def _encode(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if hasattr(value, "value") and type(value).__name__.endswith("Id"):
                return value.value
            return value

        payload = {k: _encode(v) for k, v in asdict(self).items()}
        payload["event"] = self.name
        return payload


# --------------------------------------------------------------------------------------
# Turn lifecycle
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnStarted(DomainEvent):
    turn_id: TurnId | None = None
    session_id: SessionId | None = None
    user_id: UserId | None = None
    route: str = "chat"


@dataclass(frozen=True, slots=True)
class TurnCompleted(DomainEvent):
    turn_id: TurnId | None = None
    outcome: str = "success"
    provider: str | None = None
    model: str | None = None
    total_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class TurnFailed(DomainEvent):
    turn_id: TurnId | None = None
    error_kind: str = "internal_error"
    stage: str = "unknown"
    retryable: bool = False


# --------------------------------------------------------------------------------------
# Safety and providers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SafetyTriggered(DomainEvent):
    """A guard acted. Consumed by audit and by the dataset builder that trains future
    classifiers on real detections (ADR-006)."""

    guard: str = ""
    stage: str = "input"
    action: str = "flag"
    reason: str = ""
    user_id: UserId | None = None
    turn_id: TurnId | None = None


@dataclass(frozen=True, slots=True)
class ProviderFailedOver(DomainEvent):
    from_provider: str = ""
    to_provider: str | None = None
    error_kind: str = ""
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class ProviderHealthChanged(DomainEvent):
    provider: str = ""
    healthy: bool = True
    circuit_state: str = "closed"
    consecutive_failures: int = 0


@dataclass(frozen=True, slots=True)
class BudgetExhausted(DomainEvent):
    scope: str = "user"  # user | tenant | global | turn
    budget: str = "cost"  # cost | tokens | tool_calls | generations
    limit: float = 0.0
    used: float = 0.0
    user_id: UserId | None = None


@dataclass(frozen=True, slots=True)
class ToolInvoked(DomainEvent):
    tool: str = ""
    allowed: bool = True
    ok: bool = True
    duration_ms: float = 0.0
    error_kind: str | None = None
    user_id: UserId | None = None
    turn_id: TurnId | None = None
    tainted_context: bool = False


# --------------------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoryWritten(DomainEvent):
    memory_id: MemoryId | None = None
    kind: str = "semantic"
    owner_id: UserId | None = None
    source: str = ""
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class MemoryConsolidated(DomainEvent):
    owner_id: UserId | None = None
    merged: int = 0
    superseded: int = 0
    expired: int = 0
    examined: int = 0


# --------------------------------------------------------------------------------------
# Knowledge
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentIngested(DomainEvent):
    document_id: DocumentId | None = None
    version: int = 1
    chunks: int = 0
    source_type: str = "upload"
    owner_id: UserId | None = None
    quarantined: bool = False


@dataclass(frozen=True, slots=True)
class DocumentSuperseded(DomainEvent):
    document_id: DocumentId | None = None
    old_version: int = 1
    new_version: int = 2


@dataclass(frozen=True, slots=True)
class IndexBuilt(DomainEvent):
    index_name: str = ""
    documents: int = 0
    chunks: int = 0
    embedding_model: str = ""
    dimension: int = 0
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class IndexSwitched(DomainEvent):
    """The alias moved. This is the only moment a knowledge change becomes user-visible, and
    the only event that needs to be reversed to roll back (ADR-007)."""

    alias: str = ""
    from_index: str | None = None
    to_index: str = ""
    generation: GenerationId | None = None
    reason: str = "promotion"


@dataclass(frozen=True, slots=True)
class KnowledgeRefreshCompleted(DomainEvent):
    generation: GenerationId | None = None
    promoted: bool = False
    sources_processed: int = 0
    documents_ingested: int = 0
    documents_skipped: int = 0
    quarantined: int = 0
    gate_failures: tuple[str, ...] = ()
    duration_ms: float = 0.0


# --------------------------------------------------------------------------------------
# Governance
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalRequested(DomainEvent):
    approval_id: str = ""
    kind: str = "finetune"
    summary: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalDecided(DomainEvent):
    approval_id: str = ""
    approved: bool = False
    decided_by: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class ModelPromoted(DomainEvent):
    model_id: str = ""
    version: str = ""
    from_stage: str = "candidate"
    to_stage: str = "production"
    approval_id: str | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
