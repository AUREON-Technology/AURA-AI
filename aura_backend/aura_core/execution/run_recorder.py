"""Durable run state for the agent loop (GAP-024): what one bounded run did, step by step.

:class:`RunRecorder` turns the loop's phases -- understand, plan, execute, observe, evaluate,
replan -- into :class:`~aura_core.domain.agent_runs.AgentStep` rows and keeps the run's
:class:`~aura_core.domain.agent_runs.AgentRun` aggregate current, writing both through the
``AgentRunStore`` port (``ports/coding.py``). It lives in ``execution/`` beside the loop it
observes and depends only on domain + ports, like every other engine.

Two rules it keeps:

* **Recording never fails a turn.** A store that is down costs the history view, not the user's
  answer -- every write is caught, logged once, and further writes for that run are skipped.
* **No raw content.** Only digests, counts, names, and structured reasons reach the store (see
  ``domain/agent_runs.py``'s module docstring for the rule and :func:`safe_detail` for its
  enforcement). In particular a plan step's model-written ``description`` is never stored, and a
  step's free-text ``error_kind`` is only kept when it looks like a structured kind.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.agent_runs import (
    AgentBudget,
    AgentPhase,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepKind,
    goal_digest,
    safe_detail,
)
from ..domain.identifiers import AgentRunId, AgentStepId, UserId
from ..domain.planning import Plan, StepStatus
from ..observability.logging import get_logger

__all__ = ["AgentRunContext", "RunRecorder", "structured_kind"]

logger = get_logger("agent_runs")

_STRUCTURED = re.compile(r"^[a-z][a-z0-9_.:\-]{0,63}$")


def structured_kind(value: str | None) -> str | None:
    """``value`` if it looks like a machine-readable kind (``policy_denied``,
    ``unresolved_reference``), else ``None`` -- ``ExecutionEngine`` falls back to the first 120
    characters of a tool's own message when a tool reports no kind, and that text is content."""
    if value and _STRUCTURED.match(value):
        return value
    return None


def arguments_digest(arguments: Mapping[str, Any] | None) -> str | None:
    """The same 16-hex-char digest ``ToolRegistry._audit_call`` records -- correlatable with the
    audit log, never reversible to the values."""
    if not arguments:
        return None
    return hashlib.sha256(
        json.dumps(dict(arguments), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class AgentRunContext:
    """Who and what a run is for. ``goal`` is only ever digested, never stored."""

    user_id: UserId
    agent_id: str
    turn_id: str
    correlation_id: str
    goal: str = ""
    tools_available: int = 0
    tainted: bool = False
    #: The earlier run this turn retries (``AgentRun.retry_of``); already checked to be the
    #: caller's own and retryable by the API layer.
    retry_of: str | None = None


class RunRecorder:
    """Accumulates one run's aggregate and appends its steps."""

    def __init__(
        self,
        *,
        context: AgentRunContext,
        budget: AgentBudget,
        store: Any = None,
        clock: Any = None,
        metrics: Any = None,
        inbox: Any = None,
    ) -> None:
        #: Optional in-app ``Inbox`` (``notifications/inbox.py``): a run that ends failed or out of
        #: budget leaves the person a notification. A courtesy -- ``notify`` never raises.
        self._inbox = inbox
        self._store = store
        self._clock = clock
        self._metrics = metrics
        self._context = context
        self._seq = 0
        self._store_ok = store is not None
        self._started = self._monotonic()
        now = self._now()
        self.run = AgentRun(
            id=AgentRunId.new(),
            user_id=context.user_id,
            agent_id=context.agent_id,
            turn_id=context.turn_id,
            correlation_id=context.correlation_id,
            status=AgentRunStatus.RUNNING,
            created_at=now,
            updated_at=now,
            goal_digest=goal_digest(context.goal),
            goal_chars=len(context.goal),
            budget=budget,
            retry_of=context.retry_of,
        )
        self.steps: list[AgentStep] = []
        #: Steps recorded since the last :meth:`flush`.
        self._pending: list[AgentStep] = []

    # -- clock -----------------------------------------------------------------------------

    def _now(self) -> datetime:
        if self._clock is not None and hasattr(self._clock, "now"):
            return self._clock.now()
        return datetime.now(timezone.utc)

    def _monotonic(self) -> float:
        if self._clock is not None and hasattr(self._clock, "monotonic"):
            return float(self._clock.monotonic())
        return time.monotonic()

    # -- persistence -----------------------------------------------------------------------

    async def _save(self) -> None:
        """Bring the in-memory run up to date. Not a write: :meth:`flush` persists it."""
        self.run = self.run.with_(
            updated_at=self._now(), elapsed_s=max(0.0, self._monotonic() - self._started)
        )

    async def flush(self) -> None:
        """Persist the run row and every buffered step: two awaited writes, however many steps.

        A pass used to cost one write per step plus a run save after each phase (k + 5 for k
        tool calls), every one awaited on the turn's path. The run row goes first (steps
        reference it); the steps then go in one batch, in order. A failure disables recording
        for the rest of the run, as before -- the loop never fails because its record did.
        """
        pending, self._pending = self._pending, []
        if not self._store_ok:
            return
        try:
            await self._store.save_run(self.run)
            if pending:
                append_steps = getattr(self._store, "append_steps", None)
                if append_steps is not None:
                    await append_steps(pending)
                else:  # a store written before the batch method existed
                    for step in pending:
                        await self._store.append_step(step)
        except Exception:
            self._store_ok = False
            logger.warning("failed to persist agent run; continuing without recording",
                           extra={"agent": self.run.agent_id})

    async def _step(
        self,
        *,
        iteration: int,
        phase: str,
        kind: str,
        status: str = "",
        tool: str | None = None,
        call_id: str | None = None,
        error_kind: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        risk: int = 0,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self._seq += 1
        step = AgentStep(
            id=AgentStepId.new(),
            run_id=self.run.id,
            user_id=self.run.user_id,
            seq=self._seq,
            iteration=iteration,
            phase=phase,
            kind=kind,
            created_at=self._now(),
            status=status,
            tool=tool,
            call_id=call_id,
            error_kind=structured_kind(error_kind),
            arguments_digest=arguments_digest(arguments),
            risk_cost=risk,
            detail=safe_detail(detail),
        )
        self.steps.append(step)
        self._pending.append(step)

    # -- phases ------------------------------------------------------------------------------

    async def start(self) -> None:
        """Understand: what the run was given (sizes and flags, not content)."""
        await self._save()
        await self._step(
            iteration=0, phase=AgentPhase.UNDERSTAND, kind=AgentStepKind.OBSERVATION,
            status="received",
            detail={
                "goal_chars": self.run.goal_chars,
                "tools_available": self._context.tools_available,
                "tainted": self._context.tainted,
            },
        )
        await self.flush()  # the run row exists before the first pass starts

    async def planned(
        self, plan: Plan, *, iteration: int, replan_reason: str | None = None,
    ) -> None:
        """Plan / replan: the decision, as tool names and step kinds."""
        self.run = self.run.with_(tokens_used=self.run.tokens_used + int(plan.generation_tokens))
        if replan_reason is not None:
            self.run = self.run.with_(replans=self.run.replans + 1)
        await self._step(
            iteration=iteration,
            phase=AgentPhase.REPLAN if replan_reason is not None else AgentPhase.PLAN,
            kind=AgentStepKind.DECISION,
            status="replanned" if replan_reason is not None else "planned",
            error_kind=replan_reason,
            detail={
                "steps": len(plan.steps),
                "revision": len(plan.revisions),
                "tools": [s.tool for s in plan.steps if s.tool],
                "kinds": [str(s.kind) for s in plan.steps],
                "generation_tokens": int(plan.generation_tokens),
            },
        )
        await self._save()

    async def observed(
        self,
        outcome: Any,
        *,
        iteration: int,
        gate: Any,
        succeeded_before: frozenset[str],
    ) -> None:
        """Execute + observe: one step per tool call, approval, and budget refusal, then a
        summary of the pass."""
        run = self.run
        tool_calls = run.tool_calls
        tool_calls_ok = run.tool_calls_ok
        unsafe = run.unsafe_prevented
        costs = getattr(gate, "costs", {}) if gate is not None else {}
        for report in outcome.tool_reports:
            risk = int(costs.get(report.call_id, 0))
            if report.needs_confirmation:
                unsafe += 1
                await self._step(
                    iteration=iteration, phase=AgentPhase.EXECUTE, kind=AgentStepKind.APPROVAL,
                    status="awaiting_confirmation", tool=report.tool, call_id=report.call_id,
                    error_kind=report.error_kind, arguments=report.arguments, risk=risk,
                    detail={"policy_reason": structured_kind(report.policy_reason)},
                )
                continue
            tool_calls += 1
            if report.ok:
                tool_calls_ok += 1
            if not report.allowed:
                unsafe += 1
            await self._step(
                iteration=iteration, phase=AgentPhase.EXECUTE, kind=AgentStepKind.TOOL_CALL,
                status="succeeded" if report.ok else ("denied" if not report.allowed else "failed"),
                tool=report.tool, call_id=report.call_id, error_kind=report.error_kind,
                risk=risk,
                detail={
                    "allowed": bool(report.allowed),
                    "policy_reason": structured_kind(report.policy_reason),
                    "duration_ms": round(float(report.duration_ms or 0.0), 2),
                    "result_chars": int(report.result_chars or 0),
                },
            )
        for refusal in (gate.take_refusals() if gate is not None else ()):
            if refusal.side_effecting:
                unsafe += 1
            await self._step(
                iteration=iteration, phase=AgentPhase.EXECUTE, kind=AgentStepKind.TOOL_CALL,
                status="refused", tool=refusal.tool, call_id=refusal.call_id,
                error_kind=refusal.reason, risk=refusal.risk,
                detail={"side_effecting": refusal.side_effecting},
            )

        steps = outcome.plan.steps
        succeeded_now = frozenset(s.id.value for s in steps if s.status is StepStatus.SUCCEEDED)
        progressed = bool(succeeded_now - succeeded_before) or bool(outcome.succeeded)
        replans_recovered = run.replans_recovered + (1 if iteration > 1 and progressed else 0)
        self.run = run.with_(
            iterations=iteration,
            tool_calls=tool_calls,
            tool_calls_ok=tool_calls_ok,
            unsafe_prevented=unsafe,
            risk_spent=int(getattr(gate, "risk_spent", run.risk_spent)),
            replans_recovered=replans_recovered,
            plan_steps_total=len(steps),
            plan_steps_succeeded=sum(
                1 for s in steps if s.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
            ),
        )
        await self._step(
            iteration=iteration, phase=AgentPhase.OBSERVE, kind=AgentStepKind.OBSERVATION,
            status="succeeded" if outcome.succeeded else "incomplete",
            error_kind=outcome.blocked_reason,
            detail={
                "steps": len(steps),
                "succeeded": sum(1 for s in steps if s.status is StepStatus.SUCCEEDED),
                "failed": sum(1 for s in steps if s.status is StepStatus.FAILED),
                "blocked": sum(1 for s in steps if s.status is StepStatus.BLOCKED),
                "progressed": progressed,
                "steps_run": int(getattr(outcome, "steps_run", 0) or 0),
            },
        )
        await self._save()

    async def evaluated(self, *, iteration: int, stop_reason: str | None) -> None:
        """Evaluate: continue (replan) or stop, and why."""
        await self._step(
            iteration=iteration, phase=AgentPhase.EVALUATE, kind=AgentStepKind.EVALUATION,
            status="stop" if stop_reason else "replan", error_kind=stop_reason,
            detail={
                "tokens_used": self.run.tokens_used,
                "tool_calls": self.run.tool_calls,
                "risk_spent": self.run.risk_spent,
            },
        )
        await self.flush()  # end of the pass: plan + tool calls + observation + evaluation

    async def finish(
        self, outcome: Any, *, stop_reason: str | None, exhausted_budget: str | None,
    ) -> AgentRun:
        """Completion or failure: the terminal status, and the run-level metrics."""
        if stop_reason == "run_cancelled":
            status = AgentRunStatus.CANCELLED
        elif outcome is None:
            status = AgentRunStatus.FAILED
        elif outcome.succeeded and stop_reason in (None, "completed"):
            # Every step ran *and* the loop agreed it was done: with test results as a loop input
            # a pass whose steps all ran can still end unfinished (tests red, out of attempts).
            status = AgentRunStatus.COMPLETED
        elif outcome.blocked_reason == "needs_confirmation":
            status = AgentRunStatus.AWAITING_APPROVAL
        elif exhausted_budget is not None:
            status = AgentRunStatus.BUDGET_EXHAUSTED
        elif stop_reason == "stuck":
            status = AgentRunStatus.STUCK
        elif self.run.plan_steps_succeeded > 0:
            status = AgentRunStatus.PARTIAL
        else:
            status = AgentRunStatus.FAILED
        self.run = self.run.with_(
            status=status, stop_reason=stop_reason, exhausted_budget=exhausted_budget,
            finished_at=self._now(),
        )
        succeeded = status == AgentRunStatus.COMPLETED
        await self._step(
            iteration=self.run.iterations, phase=AgentPhase.EVALUATE,
            kind=AgentStepKind.COMPLETION if succeeded or status in (
                AgentRunStatus.AWAITING_APPROVAL, AgentRunStatus.PARTIAL
            ) else AgentStepKind.FAILURE,
            status=status, error_kind=stop_reason,
            detail={
                "iterations": self.run.iterations,
                "replans": self.run.replans,
                "exhausted_budget": exhausted_budget,
                "plan_steps_total": self.run.plan_steps_total,
                "plan_steps_succeeded": self.run.plan_steps_succeeded,
            },
        )
        await self._save()
        await self.flush()
        self._observe_metrics()
        await self._tell_the_person()
        return self.run

    async def _tell_the_person(self) -> None:
        """A run that ended **failed** or **out of budget** is the one a person did not see finish
        (a partial or awaiting-approval run has its own outcome inline, and a cancelled one was their
        own doing). Identifiers only: the run id, the agent and the status -- never the instruction
        or any output."""
        if self._inbox is None or self.run.status not in (AgentRunStatus.FAILED, AgentRunStatus.BUDGET_EXHAUSTED):
            return
        try:
            await self._inbox.notify(
                self.run.user_id, "run_failed",
                {"run_id": self.run.id.value, "agent_id": self.run.agent_id, "status": str(self.run.status)},
                dedupe_key=f"run:{self.run.id.value}",
            )
        except Exception:  # noqa: BLE001 -- the run's own result must never depend on a courtesy
            logger.debug("could not notify about a failed run", exc_info=True)

    async def crashed(self, exc: BaseException) -> None:
        """The loop raised: record the failure (by exception type, never message) and re-raise
        at the caller."""
        self.run = self.run.with_(
            status=AgentRunStatus.FAILED, stop_reason="error", finished_at=self._now()
        )
        await self._step(
            iteration=self.run.iterations, phase=AgentPhase.EXECUTE, kind=AgentStepKind.FAILURE,
            status=AgentRunStatus.FAILED, error_kind="error",
            detail={"exception": type(exc).__name__},
        )
        await self._save()
        await self.flush()
        self._observe_metrics()
        await self._tell_the_person()

    def _observe_metrics(self) -> None:
        metrics = self._metrics
        if metrics is None:
            return
        agent = self.run.agent_id
        try:
            metrics.agent_runs_total.labels(agent=agent, status=self.run.status).inc()
            metrics.agent_run_iterations.labels(agent=agent).observe(self.run.iterations)
            if self.run.exhausted_budget:
                metrics.agent_budget_exhausted_total.labels(
                    agent=agent, budget=self.run.exhausted_budget
                ).inc()
        except Exception:  # pragma: no cover - a metrics object without these series
            logger.debug("agent run metrics unavailable", exc_info=True)
