"""Domain error taxonomy.

Every failure in AURA is one of these. Adapters translate vendor exceptions
(``qdrant_client.http.exceptions.UnexpectedResponse``, ``google.api_core.exceptions``,
``httpx.ConnectError``, ...) into a domain error at the adapter boundary, so no engine ever
sees a vendor type and no ``except Exception`` is needed to guess what went wrong.

This replaces the pattern found throughout ``aura_backend/services`` (47 sites) where any
failure is logged and an empty result is returned -- making "the vector store is down"
indistinguishable from "nothing matched". See ``docs/analysis/04-technical-debt-report.md``
TD-09.

Each error carries:
  ``kind``      -- a stable machine-readable slug used as a metric label and in API responses.
  ``retryable`` -- whether the *same* call could plausibly succeed on retry.
  ``details``   -- structured context, never free text the caller has to parse.

``kind`` values are deliberately low-cardinality: they are used as Prometheus labels.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "AuraError",
    "ConfigurationError",
    "ValidationError",
    "NotFoundError",
    "ConflictError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitedError",
    "BudgetExceededError",
    "TimeoutError",
    "DependencyUnavailableError",
    "ProviderError",
    "AllProvidersFailedError",
    "OverloadedError",
    "UserConcurrencyLimitError",
    "EmbeddingError",
    "VectorStoreError",
    "MemoryStoreError",
    "KnowledgeError",
    "IndexNotReadyError",
    "ToolError",
    "ToolNotFoundError",
    "ToolExecutionError",
    "ToolTimeoutError",
    "SafetyViolationError",
    "PlanningError",
    "ReasoningError",
    "ExecutionError",
    "EvaluationError",
    "ApprovalRequiredError",
]


class AuraError(Exception):
    """Base class for every error raised by AURA's own code.

    Anything that escapes as a non-``AuraError`` is, by definition, a bug or an
    untranslated vendor exception -- the gateway logs those at ``error`` with a stack trace and
    returns 500, while ``AuraError`` subclasses map to specific status codes.
    """

    kind: str = "internal_error"
    retryable: bool = False
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
        self.cause = cause
        if retryable is not None:
            self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form for API responses. Never includes the cause's message, which
        may contain vendor internals or fragments of user content."""
        return {
            "error": self.kind,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(kind={self.kind!r}, message={self.message!r}, details={self.details!r})"


# --------------------------------------------------------------------------------------
# Configuration and input
# --------------------------------------------------------------------------------------


class ConfigurationError(AuraError):
    """The deployment is misconfigured. Raised at startup wherever possible, so the process
    fails fast rather than serving degraded traffic (see ``config/settings.py``)."""

    kind = "configuration_error"
    http_status = 500


class ValidationError(AuraError):
    kind = "validation_error"
    http_status = 422


class NotFoundError(AuraError):
    kind = "not_found"
    http_status = 404


class ConflictError(AuraError):
    kind = "conflict"
    http_status = 409


# --------------------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------------------


class AuthenticationError(AuraError):
    kind = "authentication_error"
    http_status = 401


class AuthorizationError(AuraError):
    kind = "authorization_error"
    http_status = 403


class RateLimitedError(AuraError):
    """Includes ``retry_after`` seconds so the gateway can set the header rather than
    leaving the client to guess."""

    kind = "rate_limited"
    http_status = 429
    retryable = True

    def __init__(self, message: str, *, retry_after: int = 60, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details.setdefault("retry_after", retry_after)
        super().__init__(message, details=details, **kwargs)
        self.retry_after = retry_after


class BudgetExceededError(AuraError):
    """A cost or token budget was exhausted -- distinct from a rate limit, which is about
    request frequency. Retrying immediately will not help; the budget must reset or be raised."""

    kind = "budget_exceeded"
    http_status = 429


class ApprovalRequiredError(AuraError):
    """A privileged transition (promoting a model version, launching a fine-tune) needs a
    recorded human decision. See ADR-008."""

    kind = "approval_required"
    http_status = 409


# --------------------------------------------------------------------------------------
# Infrastructure
# --------------------------------------------------------------------------------------


class TimeoutError(AuraError):  # noqa: A001 - deliberately shadows the builtin within this package
    kind = "timeout"
    http_status = 504
    retryable = True


class DependencyUnavailableError(AuraError):
    """A required downstream (Redis, Postgres, Qdrant, an inference host) is unreachable.

    Raised instead of silently falling back, so the caller decides whether a degraded mode is
    acceptable for *this* operation. Some callers legitimately degrade (a cache miss); none
    should degrade by inventing data.
    """

    kind = "dependency_unavailable"
    http_status = 503
    retryable = True

    def __init__(self, message: str, *, dependency: str, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details.setdefault("dependency", dependency)
        super().__init__(message, details=details, **kwargs)
        self.dependency = dependency


# --------------------------------------------------------------------------------------
# LLM / embeddings
# --------------------------------------------------------------------------------------


class ProviderError(AuraError):
    """One provider failed. The router catches this and tries the next candidate; it escapes
    to the caller only via :class:`AllProvidersFailedError`."""

    kind = "provider_error"
    http_status = 502
    retryable = True

    def __init__(self, message: str, *, provider: str, model: str | None = None, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details.setdefault("provider", provider)
        if model:
            details.setdefault("model", model)
        super().__init__(message, details=details, **kwargs)
        self.provider = provider
        self.model = model


class AllProvidersFailedError(AuraError):
    """Every candidate provider failed or was unhealthy.

    Unlike the current system -- which falls through to hard-coded mock strings that are
    indistinguishable from a real answer (``services/agent.py:349``) -- this surfaces as an
    explicit error. Fabricating a response is never a fallback.
    """

    kind = "all_providers_failed"
    http_status = 503
    retryable = True

    def __init__(self, message: str, *, attempts: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details.setdefault("attempts", attempts or [])
        super().__init__(message, details=details, **kwargs)
        self.attempts = attempts or []


class OverloadedError(AuraError):
    """A generation was refused at admission because its serving pool is saturated (P2.4).

    Distinct from :class:`AllProvidersFailedError`: nothing is broken, there is simply no capacity
    right now. The request was never sent to a model, so retrying after ``retry_after`` seconds is
    safe and is exactly what the client should do. ``reason`` is one of ``queue_full``,
    ``queue_timeout``, ``preempted`` (evicted from a full queue by a higher-priority request) or
    ``user_quota``.

    Maps to **503** with ``Retry-After``: the server is temporarily unable to serve, not the
    caller misbehaving.
    """

    kind = "overloaded"
    http_status = 503
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        retry_after: int,
        pool: str,
        reason: str,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details.setdefault("retry_after", retry_after)
        details.setdefault("pool", pool)
        details.setdefault("reason", reason)
        super().__init__(message, details=details, **kwargs)
        self.retry_after = retry_after
        self.pool = pool
        self.reason = reason


class UserConcurrencyLimitError(OverloadedError):
    """One user already has their quota of generations running and queued.

    **429**, not 503: capacity may well exist for other users -- this caller is the one who must
    back off. Kept a subclass so code that handles "no capacity, retry later" handles both.
    """

    kind = "user_concurrency_limit"
    http_status = 429


class EmbeddingError(AuraError):
    """Embedding generation failed.

    Critically, this is an *error* and not a zero vector. ``services/rag.py:154`` substitutes
    ``[0.0] * dim`` on failure, which is then written to the vector store and used for search --
    producing a silently unsearchable corpus. See TD-03.
    """

    kind = "embedding_error"
    http_status = 502
    retryable = True


class VectorStoreError(AuraError):
    kind = "vector_store_error"
    http_status = 502
    retryable = True


class MemoryStoreError(AuraError):
    kind = "memory_store_error"
    http_status = 502
    retryable = True


# --------------------------------------------------------------------------------------
# Knowledge
# --------------------------------------------------------------------------------------


class KnowledgeError(AuraError):
    kind = "knowledge_error"
    http_status = 500


class IndexNotReadyError(KnowledgeError):
    """The index identity required by the current embedding model does not exist yet.

    The correct response is to build it (ADR-007), never to delete and recreate whatever is
    there -- which is what the legacy code does on a dimension mismatch (TD-04/SEC-03).
    """

    kind = "index_not_ready"
    http_status = 503
    retryable = False


# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


class ToolError(AuraError):
    kind = "tool_error"
    http_status = 500

    def __init__(self, message: str, *, tool: str, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details.setdefault("tool", tool)
        super().__init__(message, details=details, **kwargs)
        self.tool = tool


class ToolNotFoundError(ToolError):
    kind = "tool_not_found"
    http_status = 404


class ToolExecutionError(ToolError):
    kind = "tool_execution_error"
    http_status = 502
    retryable = True


class ToolTimeoutError(ToolError):
    kind = "tool_timeout"
    http_status = 504
    retryable = True


# --------------------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------------------


class SafetyViolationError(AuraError):
    """A guard blocked the request or response.

    ``details`` names the guard and the action but deliberately does **not** echo the offending
    content back to the caller -- that would turn the error channel into an oracle for probing
    guard thresholds.
    """

    kind = "safety_violation"
    http_status = 400

    def __init__(self, message: str, *, guard: str, action: str, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        details.setdefault("guard", guard)
        details.setdefault("action", action)
        super().__init__(message, details=details, **kwargs)
        self.guard = guard
        self.action = action


# --------------------------------------------------------------------------------------
# Cognition
# --------------------------------------------------------------------------------------


class PlanningError(AuraError):
    kind = "planning_error"
    http_status = 500


class ReasoningError(AuraError):
    kind = "reasoning_error"
    http_status = 500


class ExecutionError(AuraError):
    kind = "execution_error"
    http_status = 500


class EvaluationError(AuraError):
    kind = "evaluation_error"
    http_status = 500
