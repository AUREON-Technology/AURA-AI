"""The agent loop's remaining time bounds each execution pass (not the engine's flat default)."""

from __future__ import annotations

import asyncio

import pytest

from aura_core.domain.identifiers import PlanId, StepId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.execution import ExecutionEngine


@pytest.fixture
def tool_context(user):
    from aura_core.ports.runtime import ToolContext

    return ToolContext(user_id=user, correlation_id="test-correlation", deadline_s=30.0)


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)


def _gen(sid: str, *deps: str) -> PlanStep:
    return PlanStep(
        kind=StepKind.GENERATE, id=StepId(sid), description=sid,
        depends_on=tuple(StepId(d) for d in deps),
    )


class TestLoopDeadlineReachesTheEngine:
    """Engineer plans: a long step must not leave its dependent ``deadline_exceeded`` while the
    agent loop still has time (the engine's flat default used to win)."""

    async def test_dependent_runs_when_loop_time_remains(self, tools, tool_context) -> None:
        from aura_core.execution.agentic_loop import AgenticExecutor

        async def handler(step: PlanStep) -> str:
            if step.id.value == "s1":
                await asyncio.sleep(0.13)  # longer than the engine's own default, below
            return "ok"

        # Stand-in for the 120 s default vs a 130 s step, scaled 1/1000.
        engine = ExecutionEngine(tools=tools, deadline_s=0.1)
        plan = _plan(_gen("s1"), _gen("s2", "s1"))

        class _Planner:
            def can_replan(self, *_a, **_k) -> bool:
                return False

        loop = AgenticExecutor(planner=_Planner(), executor=engine)
        # `run` has no handlers parameter; drive the engine through a wrapper that supplies them.
        real_execute = engine.execute

        async def execute(plan, **kw):
            return await real_execute(plan, handlers={StepKind.GENERATE: handler}, **kw)

        engine.execute = execute  # type: ignore[method-assign]
        outcome = await loop.run(plan, context=tool_context, deadline_s=5.0, max_iterations=0)

        assert outcome is not None and outcome.succeeded
        assert outcome.blocked_reason is None
        assert {s.id.value: s.status for s in outcome.plan.steps}["s2"] is StepStatus.SUCCEEDED

    async def test_engine_default_still_applies_without_a_loop(self, tools, tool_context) -> None:
        async def handler(step: PlanStep) -> str:
            await asyncio.sleep(0.13)
            return "ok"

        outcome = await ExecutionEngine(tools=tools, deadline_s=0.1).execute(
            _plan(_gen("s1"), _gen("s2", "s1")),
            context=tool_context, handlers={StepKind.GENERATE: handler},
        )
        assert outcome.blocked_reason == "deadline_exceeded"
