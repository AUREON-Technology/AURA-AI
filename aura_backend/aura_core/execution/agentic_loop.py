"""The agent loop: understand -> plan -> execute -> observe -> evaluate -> replan, bounded.

``PlanningEngine.replan`` (``planning/engine.py``) and ``ExecutionEngine.execute``
(``execution/engine.py``) already do everything a Cursor/Antigravity-style agent loop needs; the
one thing missing was an outer loop that calls them in sequence more than once per turn. That is
all this module adds -- it is the one standard loop every persona runs through (GAP-024). A
persona whose ``AgentSpec`` enables it (``max_plan_iterations > 1``; today ``engineer``) may
replan; every other persona runs the same loop with a replan limit of zero, which is one pass --
exactly what a single ``ExecutionEngine.execute`` call was before.

Every iteration still goes through the same two engines a single-shot turn uses: a planner
completion producing text-JSON, then registry-mediated tool dispatch. A model's own emitted
``tool_calls`` are never dispatched directly, here or anywhere else (ADR-006) -- looping only
changes how many times the plan/execute pair runs, never how a tool call reaches the registry.
The budget gate below can only *refuse* a dispatch before it happens; an admitted call still goes
through ``ToolRegistry.execute`` and ``ToolPolicy`` unchanged.

Five bounds, all data on the persona's :class:`~aura_core.domain.agent_runs.AgentBudget`:

* ``max_iterations`` -- replans after the first pass (``PlanningEngine.can_replan``'s ``limit``);
* ``token_budget`` -- planner-generation tokens (``Plan.generation_tokens``) summed across the
  first plan and every revision; checked before spending another replan;
* ``time_budget_s`` -- wall time on the injected clock's ``monotonic()`` (``FrozenClock`` in
  tests), never tighter than the caller's own ``deadline_s`` allows;
* ``tool_budget`` / ``risk_budget`` -- enforced *before* dispatch by :class:`RunBudgetGate`, so a
  pass cannot overshoot them mid-batch.

Each iteration also still respects the execution engine's own per-pass step and deadline budget.
When a run context is supplied, :class:`~aura_core.execution.run_recorder.RunRecorder` persists
the run and its steps through the ``AgentRunStore`` port.

ADR-039 adds the loop's *intelligence* around that skeleton, each piece optional (``None`` = off,
which is what a bare ``AgenticExecutor(planner=..., executor=...)`` gets) and wired from settings by
the container:

* the transitions are an explicit graph (``state_graph.py``): PLAN, EXECUTE, OBSERVE, REPLAN,
  VERIFY, and the terminal DONE / STUCK / ESCALATE, with :func:`~.state_graph.classify` deciding
  what OBSERVE does from a :class:`~.state_graph.Signals` snapshot;
* test results are an input to it (``runner_output.py``): a run whose tests failed is not done;
* a :class:`~.stuck.StuckDetector` stops a loop that is repeating itself, with an explanation;
* a :class:`~.recovery.RecoveryPolicy` decides, per failure kind, whether to retry, replan, surface
  or stop;
* a :class:`~.critic.Critic` checks the plan before it runs and the outcome before it is claimed;
* a :class:`~.token_monitor.TokenMonitor` reports budget use each pass and compresses older tool
  output once when context pressure builds.

None of it dispatches a tool: the loop still only ever calls ``ExecutionEngine`` and the planner.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from ..domain.agent_runs import AgentBudget, BudgetKind, risk_cost
from ..domain.planning import Plan, PlanStep, StepKind, StepStatus
from ..observability.logging import get_logger
from ..ports.runtime import ToolContext
from .confirmations import ConfirmationGrant
from .critic import Critic, critique_outcome, feedback_lines
from .engine import ExecutionEngine, ExecutionOutcome, StepEvent
from .loop_events import LoopEvent, LoopReport
from .recovery import RecoveryAction, RecoveryPolicy
from .run_recorder import AgentRunContext, RunRecorder
from .runner_output import observe_plan, unresolved_failures
from .state_graph import LoopState, Signals, classify, transition
from .stuck import (
    CallRecord, StuckConfig, StuckDetector, StuckVerdict, digest, escalation_message, stable_text,
)
from .token_monitor import RECOMMENDATIONS, TokenMonitor, compress_results

__all__ = ["AgenticExecutor", "AgentRunContext", "RunBudgetGate", "BudgetRefusal", "LoopEvent"]

logger = get_logger("agentic_loop")

# Reasons ExecutionOutcome.blocked_reason can carry that mean "budget exhausted for this pass",
# not "something failed that a revised plan could fix" -- replanning again would just spend
# another generation to produce a plan that hits the same wall. "needs_confirmation" belongs here
# for a different reason: it is not exhaustion, it is the turn correctly waiting on the user, and
# no revised plan makes a confirm-gated tool stop needing confirmation. Without it here, the loop
# would replan past a pending confirmation and the outcome carrying it -- the only place the
# kernel can find it -- gets overwritten by the next iteration's outcome (the loop only ever
# keeps the latest `outcome`), silently dropping the confirmation request.
_TERMINAL_BLOCKS = frozenset(
    {
        "deadline_exceeded", "step_budget_exceeded", "budget_exceeded", "needs_confirmation",
        "tool_budget_exceeded", "risk_budget_exceeded",
    }
)

#: Which run-level budget a terminal block reason exhausted. ``deadline_exceeded`` and
#: ``step_budget_exceeded`` are the execution engine's own per-pass bounds; ``budget_exceeded``
#: is the turn's generation budget (``BudgetExceededError``).
_BLOCK_BUDGET = {
    "deadline_exceeded": BudgetKind.TIME,
    "step_budget_exceeded": BudgetKind.TOOLS,
    "budget_exceeded": BudgetKind.TOKENS,
    "tool_budget_exceeded": BudgetKind.TOOLS,
    "risk_budget_exceeded": BudgetKind.RISK,
}


def _replan_reason(outcome: ExecutionOutcome) -> str:
    """A `reason` string that carries the *specific* failure kind(s), not just the generic
    ``blocked_reason``.

    ``PlanningEngine.replan()`` already puts each failed step's ``error_kind`` into the revised
    goal's context block (``f"Failed: {s.description} ({s.error_kind})"``) -- this only makes the
    top-level ``reason`` (recorded on the ``PlanRevision`` for audit, and folded into the same
    context by callers that pass it through) carry the same specificity, e.g.
    ``"step_failed (unresolved_argument)"`` instead of the previous bare ``"step_failed"``.
    """
    reason = outcome.blocked_reason or "step_failed"
    # `unexecuted_steps()` rather than `blocked_steps()`: by the time an outcome exists the
    # executor has already moved every blocked step out of PENDING, which is the only status
    # `blocked_steps()` reports, so it is always empty here and the fallback never fired.
    failing = outcome.plan.failed_steps or outcome.plan.unexecuted_steps()
    kinds = sorted({s.error_kind for s in failing if s.error_kind})
    if kinds:
        return f"{reason} ({', '.join(kinds)})"
    return reason


@dataclass(frozen=True, slots=True)
class BudgetRefusal:
    step_id: str
    call_id: str
    tool: str
    reason: str
    risk: int
    side_effecting: bool


class RunBudgetGate:
    """Pre-dispatch admission for one run's ``tool_budget`` and ``risk_budget``.

    Charged at admission, per dispatch attempt (a retried step is a second dispatch and pays
    again). Also records each admitted call's risk cost by call id, so the run record can show
    what every call spent even when neither budget is set.
    """

    def __init__(self, budget: AgentBudget) -> None:
        self._budget = budget
        self.tools_used = 0
        self.risk_spent = 0
        self.costs: dict[str, int] = {}
        self._refusals: list[BudgetRefusal] = []

    def admit(self, step: PlanStep, spec: Any) -> str | None:
        sensitivity = int(getattr(spec, "sensitivity", 0) or 0) if spec is not None else 0
        # An unknown tool is refused by the registry anyway; for budgeting assume the worst
        # about whether it would have acted.
        side_effecting = bool(getattr(spec, "side_effecting", True)) if spec is not None else True
        cost = risk_cost(sensitivity, side_effecting=side_effecting) if spec is not None else 0
        reason: str | None = None
        if self._budget.tool_budget is not None and self.tools_used + 1 > self._budget.tool_budget:
            reason = "tool_budget_exceeded"
        elif (
            self._budget.risk_budget is not None
            and self.risk_spent + cost > self._budget.risk_budget
        ):
            reason = "risk_budget_exceeded"
        call_id = f"step_{step.id.value}"
        if reason is not None:
            self._refusals.append(BudgetRefusal(
                step_id=step.id.value, call_id=call_id, tool=step.tool or "", reason=reason,
                risk=cost, side_effecting=side_effecting,
            ))
            return reason
        self.tools_used += 1
        self.risk_spent += cost
        self.costs[call_id] = cost
        return None

    def take_refusals(self) -> list[BudgetRefusal]:
        refusals, self._refusals = self._refusals, []
        return refusals


def _accepts(fn: Any, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class AgenticExecutor:
    """Wraps one planner/executor pair in the bounded agent loop."""

    def __init__(
        self,
        *,
        planner: Any,
        executor: ExecutionEngine,
        metrics: Any = None,
        clock: Any = None,
        runs: Any = None,
        stuck: StuckConfig | None = None,
        recovery: RecoveryPolicy | None = None,
        critic: Critic | None = None,
        test_signals: bool = False,
        pressure_threshold: float = 0.8,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._metrics = metrics
        self._clock = clock
        #: ``AgentRunStore`` (``ports/coding.py``); ``None`` records nothing durable (metrics
        #: are still emitted when a run context is given).
        self._runs = runs
        #: ADR-039 pieces. All optional; ``None`` / ``False`` is the loop as it was before them.
        self._stuck = stuck
        self._recovery = recovery
        self._critic = critic
        self._test_signals = bool(test_signals)
        self._pressure_threshold = pressure_threshold
        self._gate_execute = _accepts(getattr(executor, "execute", None), "gate")
        self._gate_stream = _accepts(getattr(executor, "stream", None), "gate")
        self._replan_takes_feedback = _accepts(getattr(planner, "replan", None), "feedback")
        self._deadline_execute = _accepts(getattr(executor, "execute", None), "deadline_s")
        self._deadline_stream = _accepts(getattr(executor, "stream", None), "deadline_s")

    def _monotonic(self) -> float:
        if self._clock is not None and hasattr(self._clock, "monotonic"):
            return float(self._clock.monotonic())
        return time.monotonic()

    def _spec_for(self, tool: str | None) -> Any:
        fn = getattr(self._executor, "spec_for", None)
        if fn is None or not tool:
            return None
        try:
            return fn(tool)
        except Exception:
            return None

    @staticmethod
    def _repairable(plan: Plan) -> bool:
        """Whether a step that never ran is worth another planning iteration.

        A BLOCKED step is: it was ready to contribute and lost its input, so a revised plan that
        repairs the dependency can still run it. That is the case this method was added for --
        `blocked_steps()` was asked the same question and always answered no, because it reports
        only steps still PENDING and the executor marks dependents BLOCKED before the outcome is
        built. The loop therefore saw a plan with a skipped step and a blocked step as
        "nothing_to_repair", returned without replanning, and let the turn answer having run
        nothing.

        A SKIPPED step is deliberately *not* repairable. Skipping means the engine has no
        handler for that step kind, which replanning cannot change -- and since no handler
        exists for any non-TOOL kind, treating skips as repairable would replan nearly every
        plan that contains a RESPOND step. On a provider sustaining 5-10 tokens/sec that is a
        second planning generation on almost every turn, to reach the same result. The honest
        answer to a skipped step is to say it did not run (the kernel's execution note), not to
        plan again.
        """
        return any(s.status is StepStatus.BLOCKED for s in plan.steps)

    async def _replan(
        self,
        plan: Plan,
        *,
        reason: str,
        available_tools: Sequence[Mapping[str, Any]],
        max_iterations: int,
        feedback: Sequence[str] = (),
    ) -> Plan | None:
        kwargs: dict[str, Any] = {}
        if feedback and self._replan_takes_feedback:
            kwargs["feedback"] = list(feedback)
        try:
            return await self._planner.replan(
                plan,
                reason=reason,
                available_tools=available_tools,
                limit=max_iterations,
                **kwargs,
            )
        except Exception:
            logger.warning("replan failed; stopping the agentic loop")
            return None

    # -- one pass ----------------------------------------------------------------------------

    async def _pass(
        self,
        plan: Plan,
        *,
        streaming: bool,
        gate: RunBudgetGate,
        context: ToolContext,
        capabilities: Sequence[str],
        confirmations: ConfirmationGrant | None,
        on_event: Callable[[StepEvent], Any] | None,
        pass_deadline_s: float | None = None,
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
        common = dict(
            context=context, capabilities=capabilities,
            confirmations=confirmations,
        )
        stream = getattr(self._executor, "stream", None)
        if streaming and callable(stream):
            if self._gate_stream:
                common["gate"] = gate
            if self._deadline_stream and pass_deadline_s is not None:
                common["deadline_s"] = pass_deadline_s
            async for item in stream(plan, **common):
                yield item
            return
        if self._gate_execute:
            common["gate"] = gate
        if self._deadline_execute and pass_deadline_s is not None:
            common["deadline_s"] = pass_deadline_s
        yield await self._executor.execute(plan, on_event=on_event, **common)

    # -- observation helpers -------------------------------------------------------------------

    @staticmethod
    def _executed(before: Plan, after: Plan) -> list[PlanStep]:
        """Steps this pass actually dispatched: pending going in, succeeded or failed coming out."""
        pending = {s.id.value for s in before.steps if s.status is StepStatus.PENDING}
        return [
            s for s in after.steps
            if s.id.value in pending and s.status in (StepStatus.SUCCEEDED, StepStatus.FAILED)
        ]

    @staticmethod
    def _call_records(steps: Sequence[PlanStep], outcome: ExecutionOutcome) -> list[CallRecord]:
        records: list[CallRecord] = []
        for step in steps:
            if step.kind is not StepKind.TOOL or not step.tool:
                continue
            ok = step.status is StepStatus.SUCCEEDED
            body = step.result if ok else outcome.failure_messages.get(step.id.value, "")
            records.append(CallRecord(
                tool=step.tool, args_digest=digest(dict(step.arguments or {})), ok=ok,
                error_kind=None if ok else step.error_kind,
                result_digest=digest(stable_text(body or "")), description=(step.description or "")[:100],
            ))
        return records

    def _side_effecting(self, step: PlanStep) -> bool:
        spec = self._spec_for(step.tool)
        return bool(getattr(spec, "side_effecting", True)) if spec is not None else True

    def _recovery_signals(
        self, outcome: ExecutionOutcome, replans_by_kind: Mapping[str, int],
    ) -> tuple[str | None, str | None, str | None, list[str], list[str]]:
        """``(stop, surface, escalate, replan_notes, unknown_kinds)`` from the failed steps."""
        if self._recovery is None:
            return None, None, None, [], []
        stop = escalate = None
        decisions = []
        notes: list[str] = []
        unknown: list[str] = []
        for step in outcome.plan.failed_steps:
            kind = step.error_kind or "failed"
            message = outcome.failure_messages.get(step.id.value, "")
            decision = self._recovery.decide(
                step.error_kind, message=message, attempt=step.attempts,
                max_attempts=step.max_attempts, side_effecting=self._side_effecting(step),
                prior_replans_for_kind=replans_by_kind.get(kind, 0),
            )
            decisions.append(decision)
            if decision.action is RecoveryAction.STOP and stop is None:
                stop = kind
            if decision.action is RecoveryAction.ESCALATE and escalate is None:
                escalate = kind
            if decision.action is RecoveryAction.REPLAN and decision.failure_class.value == "unknown":
                unknown.append(kind)
            if message:
                notes.append(f"Step {step.id.value} failed ({kind}): {message[:300]}")
            if decision.action is RecoveryAction.SURFACE:
                notes.append(
                    f"Step {step.id.value} was refused ({kind}); that is a decision, so do not "
                    "plan the same call again or a way around it"
                )
        surface = None
        if decisions and all(d.action is RecoveryAction.SURFACE for d in decisions):
            surface = outcome.plan.failed_steps[0].error_kind or "refused"
        return stop, surface, escalate, notes, unknown

    @staticmethod
    def _escalation(
        stop_reason: str | None, verdict: StuckVerdict | None,
        unresolved: Sequence[tuple[str, Any]], outcome: ExecutionOutcome | None,
    ) -> str:
        """What the user is told when the loop gave up. Empty where the existing end-of-run
        notes (a pending confirmation, an exhausted budget) already say it."""
        if stop_reason == "stuck" and verdict is not None:
            return escalation_message(verdict)
        if outcome is None or stop_reason in (None, "completed"):
            return ""
        if stop_reason.startswith("unrecoverable:"):
            return (
                f"I stopped without retrying: a safety guard withheld a result "
                f"({stop_reason.split(':', 1)[1]}), and that is not something to attempt again. "
                "Nothing further was attempted."
            )
        if stop_reason.startswith("escalated:"):
            return (
                "I stopped after one revised plan: the same unexpected failure "
                f"({stop_reason.split(':', 1)[1]}) came back, so another attempt would most "
                "likely fail the same way. Please look at what that step needs (access, a "
                "missing file, a service that is down) or tell me what to change."
            )
        if stop_reason.startswith("not_retried:"):
            return (
                "I did not retry: the step was refused by policy or needs your confirmation "
                f"({stop_reason.split(':', 1)[1]}). That is your call to make, not something to "
                "work around."
            )
        if unresolved and stop_reason in (
            "iteration_budget_exhausted", "token_budget_exhausted", "time_budget_exhausted",
            "single_pass", "replan_failed",
        ):
            sid, obs = unresolved[0]
            return (
                f"The work is not finished: the last test run (step {sid}) is still failing "
                f"({obs.summary}). I ran out of attempts before fixing it."
            )
        return ""

    # -- the loop ----------------------------------------------------------------------------

    async def _drive(
        self,
        plan: Plan,
        *,
        streaming: bool,
        context: ToolContext,
        capabilities: Sequence[str],
        confirmations: ConfirmationGrant | None,
        available_tools: Sequence[Mapping[str, Any]],
        max_iterations: int,
        deadline_s: float,
        on_event: Callable[[StepEvent], Any] | None,
        budget: AgentBudget | None,
        run_context: AgentRunContext | None,
    ) -> AsyncIterator[StepEvent | LoopEvent | ExecutionOutcome]:
        budget = budget or AgentBudget(max_iterations=max(0, max_iterations))
        time_limit = deadline_s
        if budget.time_budget_s is not None:
            time_limit = min(deadline_s, budget.time_budget_s)
        deadline = self._monotonic() + time_limit
        gate = RunBudgetGate(budget)
        recorder = (
            RunRecorder(
                context=run_context, budget=budget, store=self._runs, clock=self._clock,
                metrics=self._metrics,
            )
            if run_context is not None else None
        )
        looping = max_iterations > 0
        tool_names = {str(t.get("name", "")) for t in available_tools if t.get("name")}
        detector = (
            StuckDetector(self._stuck)
            if looping and self._stuck is not None and self._stuck.enabled else None
        )
        monitor = TokenMonitor(budget.token_budget, threshold=self._pressure_threshold)
        critic = self._critic if looping and self._critic is not None and self._critic.enabled else None
        report = LoopReport(critic_active=critic is not None)
        state = LoopState.PLAN
        report.trace.append(state.value)
        replans_by_kind: dict[str, int] = {}
        plan_revisions = corrective_passes = 0
        verdict_stuck: StuckVerdict | None = None
        unresolved: list[tuple[str, Any]] = []

        def go(event: str) -> LoopState:
            nonlocal state
            state = transition(state, event)
            report.trace.append(state.value)
            return state

        def emit(kind: str, **data: Any) -> LoopEvent:
            event = LoopEvent(kind, **data)
            report.events.append(event.as_frame())
            return event

        tokens_used = int(plan.generation_tokens)
        stop_reason: str | None = None
        exhausted: str | None = None
        outcome: ExecutionOutcome | None = None
        current = plan
        iteration = 0
        try:
            if recorder is not None:
                await recorder.start()
                await recorder.planned(plan, iteration=1)

            # PLAN: the critic's look at the plan before anything runs --------------------------
            if critic is not None:
                findings = await critic.plan_findings(
                    current, available_tools=tool_names, spec_for=self._spec_for
                )
                if findings:
                    report.findings.extend(f.as_dict() for f in findings)
                    yield emit("critic", stage="plan", findings=[f.as_dict() for f in findings])
                blocking = [f for f in findings if f.blocking]
                if (
                    blocking and plan_revisions < critic.max_plan_revisions
                    and self._planner.can_replan(current, limit=max_iterations)
                    and not (budget.token_budget is not None and tokens_used >= budget.token_budget)
                ):
                    revised = await self._replan(
                        current, reason="critic_review", available_tools=available_tools,
                        max_iterations=max_iterations, feedback=feedback_lines(blocking),
                    )
                    if revised is not None:
                        plan_revisions += 1
                        go("critic_revise")
                        tokens_used += int(revised.generation_tokens)
                        if recorder is not None:
                            await recorder.planned(
                                revised, iteration=1, replan_reason="critic_review"
                            )
                        current = revised
                        go("replanned")
            if state is LoopState.PLAN:
                go("planned")

            while True:
                iteration += 1
                before = current
                succeeded_before = frozenset(
                    s.id.value for s in current.steps if s.status is StepStatus.SUCCEEDED
                )
                outcome = None
                async for item in self._pass(
                    current, streaming=streaming, gate=gate, context=context,
                    capabilities=capabilities, confirmations=confirmations,
                    on_event=on_event,
                    # The pass may use whatever the loop has left (already the min of the loop
                    # deadline and the run's time budget), not the engine's flat default.
                    pass_deadline_s=max(0.0, deadline - self._monotonic()),
                ):
                    if isinstance(item, StepEvent):
                        yield item
                    else:
                        outcome = item
                if outcome is None:
                    stop_reason = "no_outcome"
                    break
                go("executed")
                if recorder is not None:
                    await recorder.observed(
                        outcome, iteration=iteration, gate=gate,
                        succeeded_before=succeeded_before,
                    )

                # OBSERVE: read what the pass actually showed ---------------------------------
                observations = observe_plan(outcome.plan) if looping else []
                unresolved = unresolved_failures(observations)
                executed = self._executed(before, outcome.plan)
                verdict_stuck = None
                if detector is not None:
                    verdict_stuck = detector.record_pass(
                        self._call_records(executed, outcome),
                        new_success=any(s.status is StepStatus.SUCCEEDED for s in executed),
                        failure_signals=[f"{o.kind}:{o.signature}" for _, o in unresolved],
                    )
                stop_kind, surface_kind, escalate_kind, recovery_notes, unknown_kinds = (
                    self._recovery_signals(outcome, replans_by_kind)
                )

                status = monitor.status(
                    outcome.plan, generation_tokens=tokens_used, iteration=iteration
                )
                if status is not None:
                    report.budget = status.as_dict()
                    yield emit("budget", **status.as_dict())
                    if monitor.pressure_due(status):
                        keep = frozenset(s.id.value for s in executed)
                        new_plan, new_results, saved = compress_results(
                            outcome.plan, outcome.results, keep_step_ids=keep
                        )
                        if saved:
                            outcome.plan, outcome.results = new_plan, new_results
                        pressure = emit(
                            "context_pressure", percent=status.percent, used=status.used,
                            budget=status.budget, threshold=self._pressure_threshold,
                            recommendations=list(RECOMMENDATIONS),
                            action="compressed_tool_output" if saved else "nothing_to_compress",
                            characters_saved=saved, iteration=iteration,
                        )
                        # Kept on the report too: the kernel acts on it once the loop is done
                        # (it folds the session's older history into its summary).
                        report.pressure = pressure.as_frame()
                        yield pressure

                tests_input = self._test_signals and bool(unresolved)
                signals = Signals(
                    succeeded=outcome.succeeded,
                    past_deadline=self._monotonic() > deadline,
                    blocked_reason=outcome.blocked_reason,
                    terminal_blocks=_TERMINAL_BLOCKS,
                    block_budgets=_BLOCK_BUDGET,
                    unresolved_tests=tests_input,
                    repairable=bool(outcome.plan.failed_steps) or self._repairable(outcome.plan),
                    max_iterations=max_iterations,
                    stuck=verdict_stuck is not None,
                    recovery_stop=stop_kind,
                    recovery_surface=surface_kind,
                    recovery_escalate=escalate_kind,
                    tokens_exhausted=(
                        budget.token_budget is not None and tokens_used >= budget.token_budget
                    ),
                    can_replan=self._planner.can_replan(outcome.plan, limit=max_iterations),
                )
                verdict = classify(signals)
                feedback: list[str] = []
                replan_reason = _replan_reason(outcome)
                recorded_reason = outcome.blocked_reason or "step_failed"

                if verdict.event == "success":
                    go("success")  # OBSERVE -> VERIFY
                    findings = (
                        await critic.outcome_findings(outcome.plan, observations)
                        if critic is not None else []
                    )
                    if findings:
                        report.findings.extend(f.as_dict() for f in findings)
                        yield emit("critic", stage="outcome", findings=[f.as_dict() for f in findings])
                    blocking = [f for f in findings if f.blocking]
                    if (
                        blocking and critic is not None
                        and corrective_passes < critic.max_corrective_passes
                        and signals.can_replan and not signals.tokens_exhausted
                        and self._monotonic() <= deadline
                    ):
                        corrective_passes += 1
                        go("corrective")  # VERIFY -> REPLAN: one more pass, with the critic's words
                        feedback = feedback_lines(blocking)
                        replan_reason = "critic_corrective (" + ", ".join(f.code for f in blocking) + ")"
                        recorded_reason = "critic_corrective"
                    else:
                        go("verified")
                        stop_reason, exhausted = "completed", None
                        if recorder is not None:
                            await recorder.evaluated(iteration=iteration, stop_reason=stop_reason)
                        break
                elif verdict.event == "replan":
                    go("replan")
                    if tests_input and unresolved:
                        feedback.extend(
                            f"Step {sid} ran `{o.tool}` and {o.summary}" for sid, o in unresolved
                        )
                        if not outcome.plan.failed_steps:
                            replan_reason = "tests_failed (" + "; ".join(
                                o.summary for _, o in unresolved
                            )[:160] + ")"
                            recorded_reason = "tests_failed"
                    feedback.extend(recovery_notes)
                    if critic is not None:
                        # Rules only (no model call per replan): an unresolved test failure is
                        # the finding worth putting in front of the planner.
                        findings = critique_outcome(outcome.plan, observations)
                        notable = [f for f in findings if f.severity == "error"]
                        if notable:
                            report.findings.extend(f.as_dict() for f in notable)
                            feedback.extend(feedback_lines(notable))
                            yield emit("critic", stage="outcome", findings=[f.as_dict() for f in notable])
                    for kind in unknown_kinds:
                        replans_by_kind[kind] = replans_by_kind.get(kind, 0) + 1
                else:
                    go(verdict.event)  # stuck | escalate
                    stop_reason, exhausted = verdict.stop_reason, verdict.exhausted_budget
                    if recorder is not None:
                        await recorder.evaluated(iteration=iteration, stop_reason=stop_reason)
                    break

                if recorder is not None:
                    await recorder.evaluated(iteration=iteration, stop_reason=None)
                revised = await self._replan(
                    outcome.plan, reason=replan_reason, available_tools=available_tools,
                    max_iterations=max_iterations, feedback=feedback,
                )
                if revised is None:
                    go("replan_failed")
                    stop_reason, exhausted = "replan_failed", None
                    break
                go("replanned")
                tokens_used += int(revised.generation_tokens)
                if recorder is not None:
                    await recorder.planned(
                        revised, iteration=iteration + 1, replan_reason=recorded_reason,
                    )
                current = revised
        except BaseException as exc:
            if recorder is not None:
                try:
                    await recorder.crashed(exc)
                except Exception:  # pragma: no cover - recording must never mask the error
                    logger.debug("recording a crashed run failed", exc_info=True)
            raise

        if outcome is not None:
            report.stop_reason = stop_reason
            report.observations = [o.as_dict() | {"step_id": sid} for sid, o in observe_plan(outcome.plan)]
            if verdict_stuck is not None and stop_reason == "stuck":
                report.stuck = verdict_stuck.as_dict()
            report.escalation = self._escalation(stop_reason, verdict_stuck, unresolved, outcome)
            outcome.loop = report
            if stop_reason == "stuck":
                yield emit(
                    "stuck", rule=verdict_stuck.rule if verdict_stuck else None,
                    detail=verdict_stuck.detail if verdict_stuck else "",
                    count=verdict_stuck.count if verdict_stuck else 0,
                    tool=verdict_stuck.tool if verdict_stuck else None,
                    escalation=report.escalation, iterations=iteration,
                )
        if recorder is not None:
            await recorder.finish(outcome, stop_reason=stop_reason, exhausted_budget=exhausted)
            if outcome is not None:
                outcome.run_id = recorder.run.id.value
        if outcome is not None:
            yield outcome

    async def run(
        self,
        plan: Plan,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmations: ConfirmationGrant | None = None,
        available_tools: Sequence[Mapping[str, Any]] = (),
        max_iterations: int = 1,
        deadline_s: float = 600.0,
        on_event: Callable[[StepEvent], Any] | None = None,
        budget: AgentBudget | None = None,
        run_context: AgentRunContext | None = None,
    ) -> ExecutionOutcome | None:
        """Run ``plan`` to completion, replanning on failure up to ``max_iterations`` times."""
        outcome: ExecutionOutcome | None = None
        async for item in self._drive(
            plan, streaming=False, context=context, capabilities=capabilities,
            confirmations=confirmations,
            available_tools=available_tools, max_iterations=max_iterations,
            deadline_s=deadline_s, on_event=on_event, budget=budget, run_context=run_context,
        ):
            if isinstance(item, ExecutionOutcome):
                outcome = item
        return outcome

    async def stream(
        self,
        plan: Plan,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmations: ConfirmationGrant | None = None,
        available_tools: Sequence[Mapping[str, Any]] = (),
        max_iterations: int = 1,
        deadline_s: float = 600.0,
        budget: AgentBudget | None = None,
        run_context: AgentRunContext | None = None,
    ) -> AsyncIterator[StepEvent | LoopEvent | ExecutionOutcome]:
        """Stream step progress across every iteration, then yield the final outcome.

        Intermediate outcomes are not yielded -- only :class:`StepEvent`s during each pass,
        :class:`LoopEvent`s (``budget``, ``context_pressure``, ``critic``, ``stuck``; ADR-039) when
        the loop has something to say, and the loop's final :class:`ExecutionOutcome` at the end,
        matching :meth:`~aura_core.execution.engine.ExecutionEngine.stream`'s own "progress, then
        the outcome" contract so callers (``AIKernel._iter_execution``) need no special case for
        the looping path.
        """
        async for item in self._drive(
            plan, streaming=True, context=context, capabilities=capabilities,
            confirmations=confirmations,
            available_tools=available_tools, max_iterations=max_iterations,
            deadline_s=deadline_s, on_event=None, budget=budget, run_context=run_context,
        ):
            yield item
