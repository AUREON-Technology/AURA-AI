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
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from ..domain.agent_runs import AgentBudget, BudgetKind, risk_cost
from ..domain.planning import Plan, PlanStep, StepStatus
from ..observability.logging import get_logger
from ..ports.runtime import ToolContext
from .confirmations import ConfirmationGrant
from .engine import ExecutionEngine, ExecutionOutcome, StepEvent
from .run_recorder import AgentRunContext, RunRecorder

__all__ = ["AgenticExecutor", "AgentRunContext", "RunBudgetGate", "BudgetRefusal"]

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
        "tool_budget_exceeded", "risk_budget_exceeded", "run_cancelled",
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
        #: Set by the loop to the run's cancel watch. Consulted before every dispatch: once the
        #: owner has asked the run to stop, no further tool call is admitted.
        self.cancelled: Callable[[], bool] | None = None

    def admit(self, step: PlanStep, spec: Any) -> str | None:
        sensitivity = int(getattr(spec, "sensitivity", 0) or 0) if spec is not None else 0
        # An unknown tool is refused by the registry anyway; for budgeting assume the worst
        # about whether it would have acted.
        side_effecting = bool(getattr(spec, "side_effecting", True)) if spec is not None else True
        cost = risk_cost(sensitivity, side_effecting=side_effecting) if spec is not None else 0
        reason: str | None = None
        if self.cancelled is not None and self.cancelled():
            reason = "run_cancelled"
        elif self._budget.tool_budget is not None and self.tools_used + 1 > self._budget.tool_budget:
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
        cancellation: Any = None,
    ) -> None:
        #: ``RunCancellation`` (``execution/cancellation.py``); ``None`` means runs cannot be
        #: cancelled by id (a client closing its connection still ends the turn).
        self._cancellation = cancellation
        self._planner = planner
        self._executor = executor
        self._metrics = metrics
        self._clock = clock
        #: ``AgentRunStore`` (``ports/coding.py``); ``None`` records nothing durable (metrics
        #: are still emitted when a run context is given).
        self._runs = runs
        self._gate_execute = _accepts(getattr(executor, "execute", None), "gate")
        self._gate_stream = _accepts(getattr(executor, "stream", None), "gate")

    def _monotonic(self) -> float:
        if self._clock is not None and hasattr(self._clock, "monotonic"):
            return float(self._clock.monotonic())
        return time.monotonic()

    # -- evaluate --------------------------------------------------------------------------

    def _evaluate(
        self,
        outcome: ExecutionOutcome,
        *,
        deadline: float,
        max_iterations: int,
        tokens_used: int,
        budget: AgentBudget,
        cancelled: bool = False,
    ) -> tuple[str | None, str | None]:
        """``(stop_reason, exhausted_budget)``, or ``(None, None)`` to replan."""
        if outcome.succeeded:
            return "completed", None
        if cancelled:
            return "run_cancelled", None
        if self._monotonic() > deadline:
            return "time_budget_exhausted", BudgetKind.TIME
        if outcome.blocked_reason in _TERMINAL_BLOCKS:
            return outcome.blocked_reason, _BLOCK_BUDGET.get(outcome.blocked_reason)
        if not outcome.plan.failed_steps and not self._repairable(outcome.plan):
            # Nothing a revised plan could repair.
            return "nothing_to_repair", None
        if max_iterations <= 0:
            return "single_pass", None
        if budget.token_budget is not None and tokens_used >= budget.token_budget:
            return "token_budget_exhausted", BudgetKind.TOKENS
        if not self._planner.can_replan(outcome.plan, limit=max_iterations):
            return "iteration_budget_exhausted", BudgetKind.ITERATIONS
        return None, None

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

    async def _next_plan(
        self,
        outcome: ExecutionOutcome,
        *,
        available_tools: Sequence[Mapping[str, Any]],
        max_iterations: int,
    ) -> Plan | None:
        try:
            return await self._planner.replan(
                outcome.plan,
                reason=_replan_reason(outcome),
                available_tools=available_tools,
                limit=max_iterations,
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
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
        common = dict(
            context=context, capabilities=capabilities,
            confirmations=confirmations,
        )
        stream = getattr(self._executor, "stream", None)
        if streaming and callable(stream):
            if self._gate_stream:
                common["gate"] = gate
            async for item in stream(plan, **common):
                yield item
            return
        if self._gate_execute:
            common["gate"] = gate
        yield await self._executor.execute(plan, on_event=on_event, **common)

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
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
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
        # A run that records itself can be cancelled by id; the watch is how the gate and this
        # loop learn of it. Closed on every way out (below).
        watch = (
            self._cancellation.watch(run_context.user_id.value, recorder.run.id.value)
            if recorder is not None and self._cancellation is not None else None
        )
        if watch is not None:
            gate.cancelled = lambda: watch.cancelled
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
            while True:
                iteration += 1
                succeeded_before = frozenset(
                    s.id.value for s in current.steps if s.status is StepStatus.SUCCEEDED
                )
                outcome = None
                async for item in self._pass(
                    current, streaming=streaming, gate=gate, context=context,
                    capabilities=capabilities, confirmations=confirmations,
                    on_event=on_event,
                ):
                    if isinstance(item, StepEvent):
                        yield item
                    else:
                        outcome = item
                if outcome is None:
                    stop_reason = "no_outcome"
                    break
                if recorder is not None:
                    await recorder.observed(
                        outcome, iteration=iteration, gate=gate,
                        succeeded_before=succeeded_before,
                    )
                stop_reason, exhausted = self._evaluate(
                    outcome, deadline=deadline, max_iterations=max_iterations,
                    tokens_used=tokens_used, budget=budget,
                    cancelled=watch is not None and watch.cancelled,
                )
                if recorder is not None:
                    await recorder.evaluated(iteration=iteration, stop_reason=stop_reason)
                if stop_reason is not None:
                    break
                revised = await self._next_plan(
                    outcome, available_tools=available_tools, max_iterations=max_iterations
                )
                if revised is None:
                    stop_reason = "replan_failed"
                    break
                tokens_used += int(revised.generation_tokens)
                if recorder is not None:
                    await recorder.planned(
                        revised, iteration=iteration + 1,
                        replan_reason=outcome.blocked_reason or "step_failed",
                    )
                current = revised
        except BaseException as exc:
            if recorder is not None:
                try:
                    await recorder.crashed(exc)
                except Exception:  # pragma: no cover - recording must never mask the error
                    logger.debug("recording a crashed run failed", exc_info=True)
            if watch is not None:
                await watch.close()
            raise
        if recorder is not None:
            await recorder.finish(outcome, stop_reason=stop_reason, exhausted_budget=exhausted)
            if outcome is not None:
                outcome.run_id = recorder.run.id.value
        if watch is not None:
            await watch.close()
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
    ) -> AsyncIterator[StepEvent | ExecutionOutcome]:
        """Stream step progress across every iteration, then yield the final outcome.

        Intermediate outcomes are not yielded -- only :class:`StepEvent`s during each pass and the
        loop's final :class:`ExecutionOutcome` at the end, matching
        :meth:`~aura_core.execution.engine.ExecutionEngine.stream`'s own "progress, then the
        outcome" contract so callers (``AIKernel._iter_execution``) need no special case for the
        looping path.
        """
        async for item in self._drive(
            plan, streaming=True, context=context, capabilities=capabilities,
            confirmations=confirmations,
            available_tools=available_tools, max_iterations=max_iterations,
            deadline_s=deadline_s, on_event=None, budget=budget, run_context=run_context,
        ):
            yield item
