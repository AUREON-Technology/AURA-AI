"""AURA domain layer.

Entities, value objects, domain errors, and domain events. **This package imports only the
Python standard library** -- no FastAPI, no SQLAlchemy, no vendor SDK, no pydantic. The rule is
enforced by ``tests/architecture/test_dependency_rules.py``, which walks every module's AST and
fails the build on a non-stdlib import.

Why it matters in practice: the entire cognitive core can be unit-tested on bare CPython with no
Docker, no network, and no Qdrant. That is the difference between a test suite that runs and one
that is described in a README.

See ``docs/architecture/001-hexagonal-core.md``.
"""

from .cag import CagDocRef, CagPrefix, CagPrefixIdentity
from .context import ContextBudget, ContextBundle, ContextItem, ContextSection, pack
from .errors import (
    AllProvidersFailedError,
    ApprovalRequiredError,
    AuraError,
    AuthenticationError,
    AuthorizationError,
    BudgetExceededError,
    ConfigurationError,
    ConflictError,
    DataIntegrityError,
    DependencyUnavailableError,
    EmbeddingError,
    EvaluationError,
    ExecutionError,
    IndexNotReadyError,
    KnowledgeError,
    MemoryStoreError,
    NotFoundError,
    PlanningError,
    ProviderError,
    RateLimitedError,
    ReasoningError,
    SafetyViolationError,
    TimeoutError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
    VectorStoreError,
)
from .events import DomainEvent
from .identifiers import (
    ChunkId,
    CorrelationId,
    DocumentId,
    GenerationId,
    MemoryId,
    PersonalOsRecordId,
    PlanId,
    SessionId,
    StepId,
    ToolCallId,
    TurnId,
    UserId,
    deterministic_uuid,
)
from .knowledge import (
    Chunk,
    Document,
    DocumentStatus,
    Entity,
    GraphDelta,
    IndexIdentity,
    KnowledgeSource,
    Relation,
    RetrievalResult,
    RetrievedChunk,
    SourceType,
    TrustTier,
)
from .llm import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingResult,
    FinishReason,
    ProviderCapabilities,
    RoutingPolicy,
    SamplingParams,
    StreamChunk,
    TokenUsage,
    ToolChoice,
)
from .memory import (
    MEMORY_POLICIES,
    ConsolidationOutcome,
    MemoryHit,
    MemoryKind,
    MemoryPolicy,
    MemoryQuery,
    MemoryRecord,
    Provenance,
)
from .messages import Conversation, Message, Role, ToolCall, ToolResult, TrustChannel
from .personal_os import PersonalOsRecord, RecordKind
from .planning import Goal, Plan, PlanStep, Priority, StepKind, StepStatus
from .reasoning import (
    ConfidenceAssessment,
    ReasoningStrategy,
    ReasoningTrace,
    Thought,
    ThoughtNode,
    Verification,
    VerificationOutcome,
)
from .safety import GuardAction, GuardStage, GuardVerdict, SafetyReport, Span, redact_spans
from .telemetry import (
    CacheOutcome,
    RetrievalTelemetry,
    StageTiming,
    ToolTelemetry,
    TurnOutcome,
    TurnTelemetry,
)

__all__ = [
    # identifiers
    "ChunkId", "CorrelationId", "DocumentId", "GenerationId", "MemoryId",
    "PersonalOsRecordId", "PlanId",
    "SessionId", "StepId", "ToolCallId", "TurnId", "UserId", "deterministic_uuid",
    # messages
    "Conversation", "Message", "Role", "ToolCall", "ToolResult", "TrustChannel",
    # personal_os
    "PersonalOsRecord", "RecordKind",
    # llm
    "CompletionRequest", "CompletionResponse", "EmbeddingResult", "FinishReason",
    "ProviderCapabilities", "RoutingPolicy", "SamplingParams", "StreamChunk", "TokenUsage",
    "ToolChoice",
    # context
    "ContextBudget", "ContextBundle", "ContextItem", "ContextSection", "pack",
    # cag
    "CagDocRef", "CagPrefix", "CagPrefixIdentity",
    # memory
    "MEMORY_POLICIES", "ConsolidationOutcome", "MemoryHit", "MemoryKind", "MemoryPolicy",
    "MemoryQuery", "MemoryRecord", "Provenance",
    # knowledge
    "Chunk", "Document", "DocumentStatus", "Entity", "GraphDelta", "IndexIdentity",
    "KnowledgeSource", "Relation", "RetrievalResult", "RetrievedChunk", "SourceType",
    "TrustTier",
    # planning / reasoning
    "Goal", "Plan", "PlanStep", "Priority", "StepKind", "StepStatus",
    "ConfidenceAssessment", "ReasoningStrategy", "ReasoningTrace", "Thought", "ThoughtNode",
    "Verification", "VerificationOutcome",
    # safety
    "GuardAction", "GuardStage", "GuardVerdict", "SafetyReport", "Span", "redact_spans",
    # telemetry / events
    "CacheOutcome", "RetrievalTelemetry", "StageTiming", "ToolTelemetry", "TurnOutcome",
    "TurnTelemetry", "DomainEvent",
    # errors
    "AllProvidersFailedError", "ApprovalRequiredError", "AuraError", "AuthenticationError",
    "AuthorizationError", "BudgetExceededError", "ConfigurationError", "ConflictError",
    "DataIntegrityError", "DependencyUnavailableError", "EmbeddingError", "EvaluationError",
    "ExecutionError",
    "IndexNotReadyError", "KnowledgeError", "MemoryStoreError", "NotFoundError",
    "PlanningError", "ProviderError", "RateLimitedError", "ReasoningError",
    "SafetyViolationError", "TimeoutError", "ToolError", "ToolExecutionError",
    "ToolNotFoundError", "ToolTimeoutError", "ValidationError", "VectorStoreError",
]
