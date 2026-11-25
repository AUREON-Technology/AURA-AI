"""A planned turn's time is split into planning / tools / replan (ticket problem 9).

``timings["planning"]`` used to cover plan + execute + every replan, so one slow tool read as
slow planning. The engine and the agent loop now measure tool and replan time; the kernel splits
the stage; the existing ``aura_stage_duration_seconds{stage}`` series carries the new stages.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.domain.identifiers import CorrelationId, PlanId, StepId, TurnId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.domain.telemetry import StageTiming, TurnTelemetry
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.critic import CriticFinding
from aura_core.execution.engine import ExecutionOutcome
from aura_core.kernel import AIKernel, TurnRequest
from aura_core.observability.metrics import PROMETHEUS_AVAILABLE, Metrics
from aura_core.ports.runtime import ToolContext

ADD_TASK = "add a task to buy milk tomorrow"


def _plan() -> Plan:
    step = PlanStep(id=StepId("s1"), kind=StepKind.RESPOND, description="respond")
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(step,))


class TestStageTiming:
    def test_the_new_stage_is_in_the_record_and_in_overhead(self) -> None:
        t = StageTiming(planning=10.0, tools=200.0, replan=30.0, reasoning=500.0, generation=700.0)
        assert t.as_dict()["replan"] == 30.0
        assert t.as_dict()["tools"] == 200.0
        # reasoning and generation are model time, so they stay out of "overhead".
        assert t.overhead == 10.0 + 200.0 + 30.0


class TestOutcomeTimings:
    def test_a_single_pass_reports_its_duration_as_tool_time(self) -> None:
        outcome = ExecutionOutcome(plan=_plan(), results={}, duration_ms=125.0)
        assert outcome.tools_ms == 125.0
        assert outcome.replan_ms == 0.0

    def test_explicit_values_win(self) -> None:
        outcome = ExecutionOutcome(
            plan=_plan(), results={}, duration_ms=10.0, tools_ms=300.0, replan_ms=40.0
        )
        assert (outcome.tools_ms, outcome.replan_ms) == (300.0, 40.0)


class _Planner:
    def can_replan(self, plan, *, limit=None) -> bool:
        return len(plan.revisions) < (2 if limit is None else limit)

    async def replan(self, plan, *, reason, available_tools=(), limit=None):
        await asyncio.sleep(0.05)
        return plan.revised(steps=list(plan.steps), reason=reason)


class _Executor:
    """Pass 1 fails after reporting 100 ms of tool time; pass 2 succeeds after 200 ms."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, plan, *, context, capabilities=(), confirmations=None,
                      on_event=None) -> ExecutionOutcome:
        self.calls += 1
        ok = self.calls >= 2
        steps = tuple(s.succeeded("ok", 1.0) if ok else s.failed("boom", 1.0) for s in plan.steps)
        return ExecutionOutcome(
            plan=plan.with_steps(steps), results={}, succeeded=ok,
            duration_ms=100.0 if self.calls == 1 else 200.0,
        )


class TestLoopAccumulates:
    async def test_tool_time_is_summed_over_passes_and_replans_are_timed(self) -> None:
        ctx = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)
        outcome = await AgenticExecutor(planner=_Planner(), executor=_Executor()).run(
            _plan(), context=ctx, max_iterations=3, deadline_s=10.0
        )
        assert outcome is not None and outcome.succeeded
        assert outcome.tools_ms == pytest.approx(300.0)      # 100 + 200, not just the last pass
        assert outcome.replan_ms >= 40.0                      # one replan that slept 50 ms

    async def test_the_critics_plan_revision_counts_as_replan_time(self) -> None:
        class _Critic:
            """Blocks the first plan once, then has nothing to say."""

            enabled = True
            max_plan_revisions = 1
            max_corrective_passes = 0

            async def plan_findings(self, plan, **_kw):
                return [CriticFinding(code="blocked", severity="error", message="revise it",
                                      blocking=True)]

            async def outcome_findings(self, plan, observations):
                return []

        class _Succeeds:
            async def execute(self, plan, **_kw) -> ExecutionOutcome:
                steps = tuple(s.succeeded("ok", 1.0) for s in plan.steps)
                return ExecutionOutcome(
                    plan=plan.with_steps(steps), results={}, succeeded=True, duration_ms=10.0
                )

        ctx = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)
        loop = AgenticExecutor(planner=_Planner(), executor=_Succeeds(), critic=_Critic())
        outcome = await loop.run(_plan(), context=ctx, max_iterations=3, deadline_s=10.0)

        assert outcome is not None and outcome.succeeded
        assert len(outcome.plan.revisions) == 1  # the revision happened, and no pass failed
        assert outcome.replan_ms >= 40.0         # so the 50 ms can only be the critic's replan


class TestKernelSplit:
    def test_the_three_parts_sum_to_the_old_planning_figure(self) -> None:
        timings = {"planning": 1000.0}
        outcome = ExecutionOutcome(
            plan=_plan(), results={}, duration_ms=0.0, tools_ms=600.0, replan_ms=150.0
        )
        AIKernel._split_execution_timings(timings, outcome)
        assert timings == {"planning": 250.0, "tools": 600.0, "replan": 150.0}

    def test_it_never_reports_more_than_was_measured(self) -> None:
        timings = {"planning": 100.0}
        outcome = ExecutionOutcome(
            plan=_plan(), results={}, tools_ms=900.0, replan_ms=900.0
        )
        AIKernel._split_execution_timings(timings, outcome)
        assert timings["tools"] == 100.0
        assert timings["replan"] == 0.0
        assert timings["planning"] == 0.0

    def test_a_turn_that_never_executed_keeps_its_planning_time(self) -> None:
        timings = {"planning": 80.0}
        AIKernel._split_execution_timings(timings, None)
        assert timings == {"planning": 80.0, "tools": 0.0, "replan": 0.0}


@pytest.mark.skipif(not PROMETHEUS_AVAILABLE, reason="prometheus_client is not installed")
class TestEndToEnd:
    async def test_a_planned_turn_that_runs_a_tool_emits_the_tools_stage(self, container) -> None:
        metrics = Metrics(enabled=True)
        container.metrics = metrics
        container.kernel._metrics = metrics
        await container.startup()

        async def plan(goal, *, available_tools=(), **_kw):
            step = PlanStep(
                id=StepId("s1"), kind=StepKind.TOOL, tool="calculator",
                description="add", arguments={"expression": "2+2"},
            )
            return Plan(id=PlanId.new(), goal=goal, steps=(step,))

        container.kernel._deps.planner.plan = plan  # a real tool still runs through the registry
        result = await container.kernel.run(
            TurnRequest(message="what is 2+2?", user_id=UserId("stage-user"), plan=True)
        )

        timings = result.telemetry.timings
        assert result.telemetry.plan_steps == 1
        assert timings.tools > 0.0, "a tool ran, so the tools stage must have a duration"
        assert timings.planning >= 0.0
        count = lambda stage: metrics.registry.get_sample_value(  # noqa: E731
            "aura_stage_duration_seconds_count", {"stage": stage}
        )
        assert count("tools") == 1.0
