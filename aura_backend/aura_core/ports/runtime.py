"""Runtime ports: tools, events, clock, audit, telemetry, rate limiting.

These are the seams for everything that is neither a model nor a datastore. Two deserve
explanation:

* **Clock.** Injecting time is what makes memory decay, TTLs, budget windows, and scheduling
  testable without ``sleep`` or ``freezegun``. Every engine that cares about time takes a
  :class:`Clock`.
* **EventBus.** Cross-cutting reactions (audit, metrics, cache invalidation) subscribe rather
  than being called from the engine that caused them. Publication must never fail the caller:
  a broken subscriber cannot break a user's turn.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.events import DomainEvent
from ..domain.identifiers import UserId
from ..domain.telemetry import TurnTelemetry
from ..domain.tools import ToolSensitivity, ToolSpec

__all__ = [
    "Tool",
    "ToolContext",
    "EventBus",
    "Clock",
    "AuditSink",
    "AuditEvent",
    "TelemetrySink",
    "RateLimiter",
    "RateLimitDecision",
]


class ToolContext:
    """Everything a tool is allowed to know about its caller.

    Deliberately narrow. A tool receives the acting user, the correlation id, a deadline, and an
    explicit credential resolver -- never the raw request, never the conversation, never other
    users' data. Passing the whole request object into tools is how a tool ends up reading the
    system prompt.

    ``agent_id``, ``allowed_tools``, ``max_sensitivity`` and ``memory_scope`` are the one
    exception to "narrow": they are policy inputs, not caller data, carried here because
    :class:`ToolContext` is the one object that already flows unchanged from the kernel through
    the executor into :meth:`ToolPolicy.evaluate` (Phase C1, ``aura_core/agents/registry.py``).
    ``allowed_tools=None``/``max_sensitivity=None`` mean "no agent-level restriction beyond the
    caller's own capability grants" -- the pre-Phase-C1 behaviour, so every existing caller that
    builds a ``ToolContext`` without them is unaffected.
    """

    __slots__ = (
        "user_id", "correlation_id", "deadline_s", "tainted", "credentials", "metadata",
        "agent_id", "allowed_tools", "max_sensitivity", "memory_scope",
        "capabilities", "delegation_depth",
    )

    def __init__(
        self,
        *,
        user_id: UserId,
        correlation_id: str,
        deadline_s: float,
        tainted: bool = False,
        credentials: Mapping[str, str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        agent_id: str | None = None,
        allowed_tools: frozenset[str] | None = None,
        max_sensitivity: ToolSensitivity | None = None,
        memory_scope: str = "shared",
        capabilities: tuple[str, ...] = (),
        delegation_depth: int = 0,
    ) -> None:
        self.user_id = user_id
        self.correlation_id = correlation_id
        self.deadline_s = deadline_s
        # True when this turn's context contains quarantined content. Side-effecting tools
        # refuse to run in a tainted context without confirmation (ADR-006).
        self.tainted = tainted
        self.credentials = dict(credentials or {})
        self.metadata = dict(metadata or {})
        self.agent_id = agent_id
        self.allowed_tools = allowed_tools
        self.max_sensitivity = max_sensitivity
        self.memory_scope = memory_scope
        # The caller's own capability grants and how many `delegate_to_agent` hops produced this
        # turn -- both policy inputs for delegation (aura_core/tools/delegation.py), same
        # rationale as agent_id/allowed_tools/max_sensitivity above: carried here because
        # ToolContext is the one object that already flows from kernel to tool unchanged.
        self.capabilities = capabilities
        self.delegation_depth = delegation_depth


@runtime_checkable
class Tool(Protocol):
    """An executable capability.

    ``spec`` is what the model sees; ``execute`` is what the runtime calls. They are separate so
    the model can never receive a callable, and so a spec can be published to a provider without
    the implementation being loaded.
    """

    @property
    def spec(self) -> ToolSpec:
        ...

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> str:
        """Run the tool and return a string result for the model.

        Arguments have already been validated against ``spec.json_schema()`` by the registry, so
        implementations may trust their shape -- but not their *values*: a validated string is
        still attacker-influenced text.

        Raise :class:`~aura_core.domain.errors.ToolExecutionError` on failure. The runtime
        applies the timeout; implementations should still respect ``context.deadline_s`` for
        cooperative cancellation of long operations.
        """
        ...


@runtime_checkable
class EventBus(Protocol):
    """Publish/subscribe for :class:`DomainEvent`."""

    def subscribe(self, event_type: type[DomainEvent], handler: Callable[[DomainEvent], Awaitable[None]]) -> None:
        ...

    async def publish(self, event: DomainEvent) -> None:
        """Deliver to subscribers.

        Must never raise. A subscriber that fails is logged and counted
        (``aura_event_handler_failures_total``) but cannot propagate into the publisher --
        audit logging must not be able to fail a user's request.
        """
        ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, always timezone-aware UTC."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds, for durations. Wall-clock deltas are wrong across NTP steps."""
        ...

    async def sleep(self, seconds: float) -> None:
        ...


class AuditEvent:
    """A security-relevant action.

    Distinct from :class:`DomainEvent`: audit records answer "who did what to which resource,
    and what happened", have their own retention policy, and never contain prompt or document
    content.
    """

    __slots__ = ("action", "actor", "actor_role", "resource", "outcome", "correlation_id",
                 "at", "detail", "source_ip")

    def __init__(
        self,
        *,
        action: str,
        actor: str,
        outcome: str,
        resource: str = "",
        actor_role: str = "",
        correlation_id: str = "",
        at: datetime | None = None,
        detail: Mapping[str, Any] | None = None,
        source_ip: str | None = None,
    ) -> None:
        self.action = action
        self.actor = actor
        self.actor_role = actor_role
        self.resource = resource
        self.outcome = outcome
        self.correlation_id = correlation_id
        self.at = at
        self.detail = dict(detail or {})
        self.source_ip = source_ip

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "actor": self.actor,
            "actor_role": self.actor_role,
            "resource": self.resource,
            "outcome": self.outcome,
            "correlation_id": self.correlation_id,
            "at": self.at.isoformat() if self.at else None,
            "source_ip": self.source_ip,
            "detail": self.detail,
        }


@runtime_checkable
class AuditSink(Protocol):
    """Append-only audit log."""

    async def record(self, event: AuditEvent) -> None:
        ...

    async def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Sequence[AuditEvent]:
        ...


@runtime_checkable
class TelemetrySink(Protocol):
    """Where :class:`TurnTelemetry` goes: Prometheus, structured logs, and durable storage."""

    async def record_turn(self, telemetry: TurnTelemetry) -> None:
        ...


class RateLimitDecision:
    """Outcome of a rate-limit check, with the numbers needed for response headers."""

    __slots__ = ("allowed", "limit", "remaining", "retry_after_s", "scope")

    def __init__(
        self,
        *,
        allowed: bool,
        limit: int,
        remaining: int,
        retry_after_s: int = 0,
        scope: str = "",
    ) -> None:
        self.allowed = allowed
        self.limit = limit
        self.remaining = remaining
        self.retry_after_s = retry_after_s
        self.scope = scope

    def __bool__(self) -> bool:
        return self.allowed


@runtime_checkable
class RateLimiter(Protocol):
    """Cost-weighted limiting.

    ``cost`` exists because a health check and a full agent turn are not the same request. The
    legacy limiter counts both as 1 (``main.py:47``), so 100 agent turns per minute per IP is
    the actual limit -- far beyond what the hardware can serve.
    """

    async def check(self, *, key: str, scope: str, cost: int = 1) -> RateLimitDecision:
        ...

    async def reset(self, *, key: str, scope: str) -> None:
        ...
