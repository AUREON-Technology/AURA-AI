"""Durable agent-run state: one :class:`AgentRun` per bounded loop, one :class:`AgentStep` per
thing that happened inside it (Agent Platform 2.0, GAP-024).

``domain/engineering.py``'s :class:`~aura_core.domain.engineering.EngineerRun` records *one
engineer tool call* -- it answers "what did that ``git_push`` do", for one persona. What it
cannot answer is "what did this agent's turn do as a whole": how many plan/execute/replan
iterations it ran, which budget stopped it, which calls policy refused, whether a replan
recovered. That is this module, and it is persona-agnostic -- every planned turn of every agent
produces one run, whether the persona's :class:`~aura_core.agents.registry.AgentSpec` enables the
multi-iteration loop or runs a single pass.

The two ledgers are joined by ``(turn_id, correlation_id)``, not by a foreign key: an engineer
run row predates this module and is synced to the client on its own contract, and making it
depend on a run row existing would couple two independently useful records.

**Content rule.** A run stores no more raw prompt or response content than an ``EngineerRun``
already does -- which is none of the user's message and none of the model's generated text. The
goal is kept as a SHA-256 digest and a length; a plan step is kept as its tool name, kind, and
status, never its model-written description; a tool call's arguments as a digest (the same
16-hex-char digest the audit log already uses), never their values. ``AgentStep.detail`` is for
*structured* facts (counts, names, reasons), and :func:`safe_detail` enforces that by dropping any
value that is not a short scalar or a list of them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping

from .identifiers import AgentRunId, AgentStepId, UserId

__all__ = [
    "AgentBudget",
    "BudgetKind",
    "AgentRunStatus",
    "AgentStepKind",
    "AgentPhase",
    "AgentRun",
    "AgentStep",
    "goal_digest",
    "risk_cost",
    "safe_detail",
    "run_metrics",
]


class BudgetKind:
    """Which bound stopped a run. Plain strings: stored verbatim, and used as a metric label."""

    ITERATIONS = "iterations"
    TOKENS = "tokens"
    TIME = "time"
    TOOLS = "tools"
    RISK = "risk"

    ALL = (ITERATIONS, TOKENS, TIME, TOOLS, RISK)


@dataclass(frozen=True, slots=True)
class AgentBudget:
    """The five bounds on one agent run, as data on an ``AgentSpec``.

    ``max_iterations`` is the number of *replans* the loop may make after its first pass -- the
    meaning ``PlanningEngine.can_replan(limit=...)`` and ``AgenticExecutor`` have always given it,
    kept so the ``engineer`` persona's ``max_plan_iterations=8`` means exactly what it meant
    before. A single-pass persona has ``max_iterations=0``.

    ``None`` for any other bound means "not bounded by this", never "zero". ``time_budget_s`` is
    measured on the injected clock's ``monotonic()`` (``FrozenClock``-compatible), and when it is
    ``None`` the caller's own deadline (``settings.reasoning.engineer_loop_deadline_s``) still
    applies, as it always has.

    ``risk_budget`` spends :func:`risk_cost` per dispatched tool call -- the tool's sensitivity
    (0 public .. 3 system) plus one if it is side-effecting -- so a persona can be allowed many
    reads but only a few writes without enumerating either.
    """

    max_iterations: int = 0
    token_budget: int | None = None
    time_budget_s: float | None = None
    tool_budget: int | None = None
    risk_budget: int | None = None

    def __post_init__(self) -> None:
        if self.max_iterations < 0:
            raise ValueError("max_iterations must be >= 0")
        for name in ("token_budget", "time_budget_s", "tool_budget", "risk_budget"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0 when set")

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_iterations": self.max_iterations,
            "token_budget": self.token_budget,
            "time_budget_s": self.time_budget_s,
            "tool_budget": self.tool_budget,
            "risk_budget": self.risk_budget,
        }


def risk_cost(sensitivity: int, *, side_effecting: bool) -> int:
    """What one dispatched tool call spends from a run's ``risk_budget``."""
    return int(sensitivity) + (1 if side_effecting else 0)


class AgentRunStatus:
    """Plain string constants (stored verbatim; read back by API clients)."""

    RUNNING = "running"
    #: Every plan step succeeded.
    COMPLETED = "completed"
    #: The run stopped with some plan steps succeeded and others not.
    PARTIAL = "partial"
    #: The run stopped with nothing it planned achieved.
    FAILED = "failed"
    #: A confirm-gated tool call is waiting on the user (not a failure).
    AWAITING_APPROVAL = "awaiting_approval"
    #: A budget (``exhausted_budget`` says which) stopped the run before it finished.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: The owner asked for it to stop (``POST /agents/runs/{id}/cancel``). Takes effect at the next
    #: step boundary: a step already in flight (a model call, a tool) is not interrupted.
    CANCELLED = "cancelled"

    TERMINAL = frozenset({COMPLETED, PARTIAL, FAILED, AWAITING_APPROVAL, BUDGET_EXHAUSTED, CANCELLED})
    #: Runs a person may retry: one that ended without achieving what it planned. A completed run
    #: has nothing to retry, and one awaiting approval is resumed with its approval ids, not retried.
    RETRYABLE = frozenset({PARTIAL, FAILED, BUDGET_EXHAUSTED, CANCELLED})


class AgentStepKind:
    OBSERVATION = "observation"
    DECISION = "decision"
    TOOL_CALL = "tool_call"
    APPROVAL = "approval"
    EVALUATION = "evaluation"
    FAILURE = "failure"
    COMPLETION = "completion"


class AgentPhase:
    """Which phase of understand -> plan -> execute -> observe -> evaluate -> replan a step
    belongs to."""

    UNDERSTAND = "understand"
    PLAN = "plan"
    EXECUTE = "execute"
    OBSERVE = "observe"
    EVALUATE = "evaluate"
    REPLAN = "replan"


def goal_digest(text: str) -> str:
    """A stable digest of the goal text -- enough to correlate two runs on the same request,
    not enough to recover it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


_MAX_DETAIL_STR = 120
_MAX_DETAIL_LIST = 32


def safe_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only short structured facts: numbers, booleans, short strings, and short lists of
    them. Anything else -- a nested mapping, a long string that could be model or user text --
    is dropped rather than truncated, because a truncated prompt is still a prompt."""
    out: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if value is None or isinstance(value, (bool, int, float)):
            out[str(key)] = value
        elif isinstance(value, str):
            if len(value) <= _MAX_DETAIL_STR:
                out[str(key)] = value
        elif isinstance(value, (list, tuple)):
            items = [
                v for v in value[:_MAX_DETAIL_LIST]
                if isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= _MAX_DETAIL_STR)
            ]
            out[str(key)] = items
    return out


@dataclass(frozen=True, slots=True)
class AgentRun:
    """One bounded agent loop. Mutable state expressed as ``replace()``d copies; the store
    upserts by ``id``."""

    id: AgentRunId
    user_id: UserId
    agent_id: str
    turn_id: str
    correlation_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    goal_digest: str = ""
    goal_chars: int = 0
    budget: AgentBudget = field(default_factory=AgentBudget)
    #: Execution passes made (the first pass plus one per replan).
    iterations: int = 0
    replans: int = 0
    #: Replans after which the next pass made progress (a step that had not succeeded before
    #: did). The numerator of ``replan_success``.
    replans_recovered: int = 0
    tokens_used: int = 0
    tool_calls: int = 0
    tool_calls_ok: int = 0
    risk_spent: int = 0
    elapsed_s: float = 0.0
    #: Tool calls refused before they could act: policy denial, confirmation gate, or a
    #: tool/risk budget refusal of a side-effecting call.
    unsafe_prevented: int = 0
    plan_steps_total: int = 0
    plan_steps_succeeded: int = 0
    stop_reason: str | None = None
    exhausted_budget: str | None = None
    finished_at: datetime | None = None
    #: The earlier run this one retries, set when the client resends a turn with ``retry_of``. A
    #: link only: the instruction itself is never stored (the content rule above), so a retry is
    #: the client sending it again, and this is what lets the history show the chain.
    retry_of: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in AgentRunStatus.TERMINAL

    def with_(self, **changes: Any) -> "AgentRun":
        return replace(self, **changes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "agent_id": self.agent_id,
            "turn_id": self.turn_id,
            "correlation_id": self.correlation_id,
            "status": self.status,
            "goal_digest": self.goal_digest,
            "goal_chars": self.goal_chars,
            "budget": self.budget.as_dict(),
            "iterations": self.iterations,
            "replans": self.replans,
            "replans_recovered": self.replans_recovered,
            "tokens_used": self.tokens_used,
            "tool_calls": self.tool_calls,
            "tool_calls_ok": self.tool_calls_ok,
            "risk_spent": self.risk_spent,
            "elapsed_s": round(self.elapsed_s, 3),
            "unsafe_prevented": self.unsafe_prevented,
            "plan_steps_total": self.plan_steps_total,
            "plan_steps_succeeded": self.plan_steps_succeeded,
            "stop_reason": self.stop_reason,
            "exhausted_budget": self.exhausted_budget,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "retry_of": self.retry_of,
        }


@dataclass(frozen=True, slots=True)
class AgentStep:
    """One thing that happened in a run. Append-only: a step is never updated."""

    id: AgentStepId
    run_id: AgentRunId
    user_id: UserId
    seq: int
    iteration: int
    phase: str
    kind: str
    created_at: datetime
    status: str = ""
    tool: str | None = None
    call_id: str | None = None
    error_kind: str | None = None
    arguments_digest: str | None = None
    risk_cost: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "run_id": self.run_id.value,
            "seq": self.seq,
            "iteration": self.iteration,
            "phase": self.phase,
            "kind": self.kind,
            "status": self.status,
            "tool": self.tool,
            "call_id": self.call_id,
            "error_kind": self.error_kind,
            "arguments_digest": self.arguments_digest,
            "risk_cost": self.risk_cost,
            "detail": dict(self.detail),
            "created_at": self.created_at.isoformat(),
        }


def run_metrics(run: AgentRun) -> dict[str, Any]:
    """Run-level evaluation metrics (GAP-024), derived from the run record alone.

    * ``completed`` -- every plan step succeeded;
    * ``partial_completion`` -- fraction of plan steps that succeeded (1.0 when completed);
    * ``tool_accuracy`` -- dispatched tool calls that succeeded, over those dispatched (``None``
      with no dispatch; confirmation-gated calls did not dispatch and are not counted);
    * ``iteration_utilization`` -- replans used over replans allowed (``None`` for a
      single-pass persona, whose allowance is zero);
    * ``replan_success`` -- replans after which the next pass made progress, over replans made
      (``None`` with no replan);
    * ``unsafe_prevented`` -- calls refused before acting (policy, confirmation gate, budget).
    """
    completed = run.status == AgentRunStatus.COMPLETED
    if completed:
        partial = 1.0
    elif run.plan_steps_total:
        partial = run.plan_steps_succeeded / run.plan_steps_total
    else:
        partial = 0.0
    return {
        "completed": completed,
        "partial_completion": round(partial, 4),
        "tool_accuracy": round(run.tool_calls_ok / run.tool_calls, 4) if run.tool_calls else None,
        "iterations": run.iterations,
        "iteration_utilization": (
            round(run.replans / run.budget.max_iterations, 4)
            if run.budget.max_iterations else None
        ),
        "replan_success": round(run.replans_recovered / run.replans, 4) if run.replans else None,
        "unsafe_prevented": run.unsafe_prevented,
        "budget_exhausted": run.exhausted_budget,
    }
