"""The agent task: one ``/chat`` turn that runs server-side and outlives the connection (ADR-041).

Plain data. A task is *not* a new execution model: when it runs it is a kernel turn made from the
request the owner submitted, with the owner's identity and the capabilities they held then
(never more). What the task adds is an identity, a lifecycle a client can poll, and a result that
is still there after the client has gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping

from ..domain.errors import ConflictError

__all__ = [
    "TaskStatus", "TaskPriority", "AgentTask", "TaskOutcome", "PRIORITIES",
    "IdempotencyKeyReusedError", "IdempotencyInProgressError",
]


class IdempotencyKeyReusedError(ConflictError):
    """The same ``client_request_id`` / ``Idempotency-Key`` was sent with a different request.
    409, not retryable as sent: a key names one request for its whole TTL."""

    kind = "idempotency_key_reused"


class IdempotencyInProgressError(ConflictError):
    """The first request with this key has not finished being accepted. 409, retryable: send the
    same request again in a moment and it will return the task the first one created."""

    kind = "idempotency_in_progress"
    retryable = True


class TaskStatus:
    """Plain strings: stored verbatim and read by API clients."""

    QUEUED = "queued"
    RUNNING = "running"
    #: The run stopped on a confirm-gated tool call; ``result.pending_tool_calls`` lists what
    #: awaits approval, and ``POST /agents/tasks/{id}/approve`` resumes it. Not a failure.
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})
    #: States a live process of ours must be vouching for (heartbeat).
    LIVE = frozenset({QUEUED, RUNNING})
    ALL = (QUEUED, RUNNING, AWAITING_APPROVAL, SUCCEEDED, FAILED, CANCELLED)


class TaskPriority:
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    ALL = (HIGH, NORMAL, LOW)


#: Lower runs first.
PRIORITIES: Mapping[str, int] = {TaskPriority.HIGH: 0, TaskPriority.NORMAL: 1, TaskPriority.LOW: 2}


@dataclass(frozen=True, slots=True)
class AgentTask:
    id: str
    owner: str
    goal: str
    persona: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    #: When it may start; ``None`` means as soon as a slot is free.
    run_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: Refreshed by the process that holds the task while it is queued or running. A silent one
    #: has lost its process (see ``AgentTaskSettings.stale_after_s``).
    heartbeat_at: datetime | None = None
    #: The durable agent run (``GET /agents/runs/{id}``) the task's turn recorded; known when the
    #: turn finishes, ``None`` for a turn that planned nothing or has not finished.
    run_id: str | None = None
    turn_id: str | None = None
    #: Sent to the kernel as the turn's correlation id, so logs and audit lines name the task.
    correlation_id: str | None = None
    session_id: str | None = None
    #: How many times it has been queued (1, plus one per approval resume).
    attempts: int = 0
    cancel_requested: bool = False
    #: ``{"message", "summary", "truncated", "citations", "tools_used", "pending_tool_calls",
    #: "needs_confirmation", "plan", "usage", "provider", "model", "degraded", "agent_run_id"}``.
    result: Mapping[str, Any] | None = None
    #: ``{"kind", "message", "retryable", "details"}``.
    error: Mapping[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TaskStatus.TERMINAL

    def with_(self, **changes: Any) -> "AgentTask":
        return replace(self, **changes)

    def to_record(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "id": self.id, "owner": self.owner, "goal": self.goal, "persona": self.persona,
            "status": self.status, "priority": self.priority,
            "created_at": iso(self.created_at), "updated_at": iso(self.updated_at),
            "run_at": iso(self.run_at), "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at), "heartbeat_at": iso(self.heartbeat_at),
            "run_id": self.run_id, "turn_id": self.turn_id, "correlation_id": self.correlation_id,
            "session_id": self.session_id, "attempts": self.attempts,
            "cancel_requested": self.cancel_requested,
            "result": dict(self.result) if self.result is not None else None,
            "error": dict(self.error) if self.error is not None else None,
        }

    @classmethod
    def from_record(cls, data: Mapping[str, Any]) -> "AgentTask":
        def when(value: Any) -> datetime | None:
            return datetime.fromisoformat(value) if isinstance(value, str) and value else None

        created = when(data["created_at"])
        assert created is not None
        return cls(
            id=str(data["id"]), owner=str(data["owner"]), goal=str(data.get("goal", "")),
            persona=str(data.get("persona", "orchestrator")), status=str(data["status"]),
            priority=str(data.get("priority", TaskPriority.NORMAL)),
            created_at=created, updated_at=when(data.get("updated_at")) or created,
            run_at=when(data.get("run_at")), started_at=when(data.get("started_at")),
            finished_at=when(data.get("finished_at")), heartbeat_at=when(data.get("heartbeat_at")),
            run_id=data.get("run_id"), turn_id=data.get("turn_id"),
            correlation_id=data.get("correlation_id"), session_id=data.get("session_id"),
            attempts=int(data.get("attempts") or 0),
            cancel_requested=bool(data.get("cancel_requested")),
            result=data.get("result"), error=data.get("error"),
        )

    def public(self) -> dict[str, Any]:
        """What the API returns: the record without the owner (the caller is the owner)."""
        view = self.to_record()
        view.pop("owner", None)
        return view


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """What running a task came to, as the queue records it."""

    status: str
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    run_id: str | None = None
    turn_id: str | None = None
    #: Whether the plan reservation taken at submission should be given back (a task that did
    #: not produce an answer does not use up the owner's month -- the rule ``/chat`` applies).
    release_reservation: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)
