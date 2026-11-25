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
from ..domain.tools import ToolSensitivity, ToolSpec, tool_grant_permits

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
    "Closeable",
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
    builds a ``ToolContext`` without them is unaffected. ``allowed_tool_prefixes`` extends a
    non-``None`` ``allowed_tools`` by name prefix (audit T-03); ask :meth:`permits_tool`, never
    ``name in allowed_tools``, so both halves of the grant are honoured.
    """

    __slots__ = (
        "user_id", "correlation_id", "deadline_s", "tainted", "credentials", "metadata",
        "agent_id", "allowed_tools", "allowed_tool_prefixes", "max_sensitivity", "memory_scope",
        "capabilities", "delegation_depth", "knowledge_owner", "session_id", "autonomy", "cancel_event",
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
        allowed_tool_prefixes: frozenset[str] = frozenset(),
        max_sensitivity: ToolSensitivity | None = None,
        memory_scope: str = "shared",
        capabilities: tuple[str, ...] = (),
        delegation_depth: int = 0,
        knowledge_owner: UserId | None = None,
        session_id: str | None = None,
        autonomy: str = "standard",
        cancel_event: Any = None,
    ) -> None:
        self.user_id = user_id
        # The conversation this turn belongs to (``None`` for a turn without one). Carried for the
        # session tools (checkpoint save/recall) and the handoff builder; every one of them still
        # scopes by ``user_id`` as well, so a session id is never a capability on its own.
        self.session_id = session_id
        # The user's review profile (ADR-041): ``strict`` | ``standard`` | ``autonomous``. A policy
        # input like ``max_sensitivity``; ``ToolPolicy`` decides what each value means and falls
        # back to ``standard`` for one it does not honour.
        self.autonomy = autonomy
        # Set by the kill switch (``POST /agents/stop-all``) to say "this turn was stopped".
        # Tools never read it; the registry and kernel use it to cancel work in flight.
        self.cancel_event = cancel_event
        # ADR-034: the owner for shareable data (the knowledge tools). ``None`` means ``user_id``.
        self.knowledge_owner = knowledge_owner
        self.correlation_id = correlation_id
        self.deadline_s = deadline_s
        # True when this turn's context contains quarantined content. Side-effecting tools
        # refuse to run in a tainted context without confirmation (ADR-006).
        self.tainted = tainted
        self.credentials = dict(credentials or {})
        self.metadata = dict(metadata or {})
        self.agent_id = agent_id
        self.allowed_tools = allowed_tools
        self.allowed_tool_prefixes = frozenset(allowed_tool_prefixes)
        self.max_sensitivity = max_sensitivity
        self.memory_scope = memory_scope
        # The caller's own capability grants and how many `delegate_to_agent` hops produced this
        # turn -- both policy inputs for delegation (aura_core/tools/delegation.py), same
        # rationale as agent_id/allowed_tools/max_sensitivity above: carried here because
        # ToolContext is the one object that already flows from kernel to tool unchanged.
        self.capabilities = capabilities
        self.delegation_depth = delegation_depth

    def permits_tool(self, name: str) -> bool:
        """Whether this turn's agent-level grant (names and prefixes) permits ``name``."""
        return tool_grant_permits(name, self.allowed_tools, self.allowed_tool_prefixes)


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
class Closeable(Protocol):
    """An adapter that holds a connection, pool or client and can release it.

    Optional on purpose, and separate from the storage and cache ports: an in-memory adapter has
    nothing to release, and making every port demand a no-op ``close`` would only invite adapters
    that pretend. The composition root closes whatever satisfies this at shutdown (R2.7).

    ``close`` must be idempotent and must not connect: an adapter that never built its client
    returns at once rather than building one to close it.
    """

    async def close(self) -> None: ...


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

    async def purge_before(self, cutoff: datetime) -> int:
        """Delete events recorded before ``cutoff``; returns how many (retention, audit FSA-016)."""
        ...

    async def pseudonymise_actor(self, actor: str, replacement: str) -> int:
        """Replace ``actor`` with ``replacement`` and drop the source IP on every event it made
        (erasure). The events stay: who did what to the system is a security record; the identity
        in it is personal data. Returns how many were changed."""
        ...


@runtime_checkable
class TelemetrySink(Protocol):
    """Where :class:`TurnTelemetry` goes: Prometheus, structured logs, and durable storage."""

    async def record_turn(self, telemetry: TurnTelemetry) -> None:
        ...

    async def purge_before(self, cutoff: datetime) -> int:
        """Delete turns started before ``cutoff``; returns how many (retention, audit FSA-016)."""
        ...

    async def purge_sessions(self, session_ids: Any) -> int:
        """Delete every turn of the given conversations (erasure); returns how many."""
        ...

    async def get_turn_usage(self, turn_id: str, owner_ref: str) -> dict[str, Any] | None:
        """The usage view of ``turn_id`` if -- and only if -- ``owner_ref`` is its owner's.

        ``None`` for an unknown turn, someone else's turn, a turn recorded without an owner, and
        an empty ``owner_ref``: the caller cannot tell these apart, so a turn id cannot be probed
        for existence. Content-free (see ``TurnTelemetry.usage_view``).
        """
        ...

    async def purge_owner(self, owner_ref: str) -> int:
        """Delete every turn owned by ``owner_ref`` (account erasure); returns how many."""
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
