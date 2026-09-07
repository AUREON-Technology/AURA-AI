"""AgenticExecutor: the bounded plan-execute-observe-replan loop.

Uses a scripted fake planner/executor rather than the real engines -- ``PlanningEngine.replan``
and ``ExecutionEngine.execute`` are already covered in ``test_planning_execution.py``; what needs
covering here is the loop-control logic this module adds: does it stop on success, does it stop
at the iteration budget, does it stop on a terminal (unrecoverable-by-replanning) block reason,
and does the wired-through ``limit`` actually reach ``PlanningEngine.replan`` so its own internal
guard cannot reject a revision its caller already allowed.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.engine import ExecutionOutcome, StepEvent
from aura_core.ports.runtime import ToolContext


def _plan() -> Plan:
    goal = Goal(description="do the thing")
    step = PlanStep(id=StepId("s1"), kind=StepKind.RESPOND, description="respond")
    return Plan(id=PlanId.new(), goal=goal, steps=(step,))


def _outcome(plan: Plan, *, succeeded: bool, blocked_reason: str | None = None) -> ExecutionOutcome:
    steps = tuple(
        s.succeeded("ok", 1.0) if succeeded else s.failed("boom", 1.0) for s in plan.steps
    )
    return ExecutionOutcome(
        plan=plan.with_steps(steps), results={}, succeeded=succeeded, blocked_reason=blocked_reason,
    )


class _FakePlanner:
    def __init__(self, *, max_replans: int = 2) -> None:
        self.max_replans = max_replans
        self.replan_calls = 0
        self.last_limit: int | None = None

    def can_replan(self, plan: Plan, *, limit: int | None = None) -> bool:
        effective = self.max_replans if limit is None else limit
        return len(plan.revisions) < effective

    async def replan(self, plan: Plan, *, reason: str, available_tools=(), limit: int | None = None) -> Plan:
        self.replan_calls += 1
        self.last_limit = limit
        if not self.can_replan(plan, limit=limit):
            raise RuntimeError("replanning limit reached")
        return plan.revised(steps=list(plan.steps), reason=reason)


class _ScriptedExecutor:
    """Builds each outcome from the *actual* plan it is given (so revisions genuinely
    accumulate across a loop) rather than from a pre-baked, stale plan snapshot -- the outcome's
    success/blocked-reason is scripted per call index, everything else reflects the real plan.
    """

    def __init__(self, results: list[bool], *, blocked_reason: str | None = None) -> None:
        self._results = results
        self._blocked_reason = blocked_reason
        self.execute_calls = 0

    def _outcome_for(self, plan: Plan) -> ExecutionOutcome:
        succeeded = self._results[min(self.execute_calls, len(self._results) - 1)]
        self.execute_calls += 1
        return _outcome(
            plan, succeeded=succeeded, blocked_reason=None if succeeded else self._blocked_reason
        )

    async def execute(
        self, plan, *, context, capabilities=(), confirmed_tools=(), confirmed_call_ids=(),
        on_event=None,
    ) -> ExecutionOutcome:
        return self._outcome_for(plan)

    async def stream(self, plan, *, context, capabilities=(), confirmed_tools=(), confirmed_call_ids=()):
        yield StepEvent(step_id="s1", kind="respond", status="succeeded", description="respond")
        yield self._outcome_for(plan)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0)


class TestRun:
    async def test_stops_immediately_on_success_without_replanning(self, ctx) -> None:
        plan = _plan()
        executor = _ScriptedExecutor([True])
        planner = _FakePlanner()
        agentic = AgenticExecutor(planner=planner, executor=executor)

        outcome = await agentic.run(plan, context=ctx, max_iterations=5, deadline_s=10.0)

        assert outcome.succeeded
        assert executor.execute_calls == 1
        assert planner.replan_calls == 0

    async def test_replans_on_failure_until_a_later_pass_succeeds(self, ctx) -> None:
        plan = _plan()
        executor = _ScriptedExecutor([False, True])
        planner = _FakePlanner(max_replans=5)
        agentic = AgenticExecutor(planner=planner, executor=executor)

        outcome = await agentic.run(plan, context=ctx, max_iterations=5, deadline_s=10.0)

        assert outcome.succeeded
        assert executor.execute_calls == 2
        assert planner.replan_calls == 1

    async def test_stops_at_the_iteration_budget_when_failures_never_stop(self, ctx) -> None:
        plan = _plan()
        executor = _ScriptedExecutor([False])
        planner = _FakePlanner(max_replans=99)  # engine default is not the binding constraint
        agentic = AgenticExecutor(planner=planner, executor=executor)

        outcome = await agentic.run(plan, context=ctx, max_iterations=2, deadline_s=10.0)

        assert not outcome.succeeded
        # initial execute + 2 replans (revisions 0->1, 1->2), then can_replan(limit=2) is false
        # at revisions==2 -- three execute() calls, two replan() calls.
        assert executor.execute_calls == 3
        assert planner.replan_calls == 2
        assert planner.last_limit == 2

    async def test_terminal_block_reason_stops_without_replanning(self, ctx) -> None:
        plan = _plan()
        executor = _ScriptedExecutor([False], blocked_reason="budget_exceeded")
        planner = _FakePlanner(max_replans=5)
        agentic = AgenticExecutor(planner=planner, executor=executor)

        outcome = await agentic.run(plan, context=ctx, max_iterations=5, deadline_s=10.0)

        assert not outcome.succeeded
        assert executor.execute_calls == 1
        assert planner.replan_calls == 0

    async def test_needs_confirmation_stops_without_replanning(self, ctx) -> None:
        """A confirm-gated tool call is not exhaustion or an unrecoverable error -- it is the turn
        correctly waiting on the user -- but it must stop the loop exactly like a terminal block,
        for a different reason: `run` only ever keeps the *latest* outcome (never accumulates
        `tool_reports` across iterations), so a replan here would silently overwrite the outcome
        carrying the confirmation report with one from a plan that never asked for it. This is the
        `engineer` persona's real bug (`max_plan_iterations=8`, so this loop is what it runs
        through) found via manual end-to-end verification against a real Docker sandbox."""
        plan = _plan()
        executor = _ScriptedExecutor([False], blocked_reason="needs_confirmation")
        planner = _FakePlanner(max_replans=5)
        agentic = AgenticExecutor(planner=planner, executor=executor)

        outcome = await agentic.run(plan, context=ctx, max_iterations=5, deadline_s=10.0)

        assert not outcome.succeeded
        assert outcome.blocked_reason == "needs_confirmation"
        assert executor.execute_calls == 1
        assert planner.replan_calls == 0

    async def test_replan_failure_stops_the_loop_rather_than_raising(self, ctx) -> None:
        plan = _plan()
        executor = _ScriptedExecutor([False])

        class _BrokenPlanner(_FakePlanner):
            async def replan(self, *args, **kwargs):
                raise RuntimeError("planner is down")

        agentic = AgenticExecutor(planner=_BrokenPlanner(), executor=executor)
        outcome = await agentic.run(plan, context=ctx, max_iterations=5, deadline_s=10.0)

        assert not outcome.succeeded
        assert executor.execute_calls == 1


class TestStream:
    async def test_yields_step_events_then_the_final_outcome(self, ctx) -> None:
        plan = _plan()
        executor = _ScriptedExecutor([False, True])
        planner = _FakePlanner(max_replans=5)
        agentic = AgenticExecutor(planner=planner, executor=executor)

        items = [
            item
            async for item in agentic.stream(plan, context=ctx, max_iterations=5, deadline_s=10.0)
        ]

        step_events = [i for i in items if isinstance(i, StepEvent)]
        outcomes = [i for i in items if isinstance(i, ExecutionOutcome)]
        assert len(step_events) == 2  # one per execute pass
        assert len(outcomes) == 1  # only the final outcome, not every intermediate one
        assert outcomes[0].succeeded
