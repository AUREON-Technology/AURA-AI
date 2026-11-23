"""What the agent loop reports besides step progress: frames for the client, and a record of why it
stopped (ADR-037).

``StepEvent`` (``execution/engine.py``) says how a *step* went. The loop has its own news -- it
stopped because it was going in circles, a budget is filling up, the critic found a problem -- and
none of that fits a step. :class:`LoopEvent` carries it as a typed frame (``stuck``, ``budget``,
``context_pressure``, ``critic``) the kernel passes straight through to the stream, and
:class:`LoopReport` keeps the same facts on the :class:`~aura_core.execution.engine.ExecutionOutcome`
so a non-streaming turn, the run record and the final answer's execution note can all read them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["LoopEvent", "LoopReport"]


class LoopEvent:
    """One typed frame. ``kind`` is the stream frame ``type``; ``data`` is its payload."""

    __slots__ = ("kind", "data")

    def __init__(self, kind: str, **data: Any) -> None:
        self.kind = kind
        self.data = data

    def as_frame(self) -> dict[str, Any]:
        return {"type": self.kind, **self.data}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"LoopEvent({self.kind!r}, {self.data!r})"


@dataclass
class LoopReport:
    """The loop's account of itself, attached to the final outcome."""

    stop_reason: str | None = None
    #: ``"plan>execute>observe>replan>..."`` -- the states visited, from the state graph.
    trace: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    stuck: dict[str, Any] | None = None
    #: What the user is told when the loop gave up (stuck or escalated); empty when it did not.
    escalation: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    #: The last budget status, when the run has a token budget.
    budget: dict[str, Any] | None = None
    #: Whether the critic was on for this loop (the kernel checks the answer only if it was).
    critic_active: bool = False
    #: The ``context_pressure`` frame, if the run crossed the threshold.
    pressure: dict[str, Any] | None = None
    #: Set by the kernel when it answered that pressure by folding the session's older history
    #: into its summary (``EpisodicMemory.compact``).
    history_compacted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason, "trace": list(self.trace),
            "stuck": self.stuck, "escalation": self.escalation,
            "findings": list(self.findings), "observations": list(self.observations),
            "budget": self.budget, "critic_active": self.critic_active,
            "pressure": self.pressure, "history_compacted": self.history_compacted,
        }
