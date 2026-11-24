"""A step starts as soon as its own dependencies succeed, not when its batch finishes.

Durations are the ticket's (5 s / 1 s / 1 s) scaled by 1/10 so the suite stays fast; the engine
measures real wall-clock, so the relationships are what matter, not the units.
"""

from __future__ import annotations

import asyncio
import time

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


class TestRollingScheduler:
    async def test_dependent_starts_when_its_dependency_finishes_not_the_batch(
        self, tools, tool_context
    ) -> None:
        t0 = time.perf_counter()
        finished: dict[str, float] = {}
        durations = {"s1": 0.5, "s2": 0.1, "s3": 0.1}

        async def handler(step: PlanStep) -> str:
            await asyncio.sleep(durations[step.id.value])
            finished[step.id.value] = time.perf_counter() - t0
            return "ok"

        plan = _plan(_gen("s1"), _gen("s2"), _gen("s3", "s2"))
        outcome = await ExecutionEngine(tools=tools, max_parallel=4).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )

        assert outcome.succeeded
        # s3 (depends on s2 only) is done at ~0.2 -- well before s1 (0.5) -- and the whole run is
        # bounded by the slowest chain, not by batch-after-batch.
        assert finished["s3"] < finished["s1"]
        assert finished["s3"] < 0.35
        assert time.perf_counter() - t0 < 0.6

    async def test_max_parallel_still_bounds_concurrency(self, tools, tool_context) -> None:
        running = peak = 0

        async def handler(step: PlanStep) -> str:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            return "ok"

        plan = _plan(*(_gen(f"s{i}") for i in range(1, 8)))
        outcome = await ExecutionEngine(tools=tools, max_parallel=2).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        assert outcome.succeeded and outcome.steps_run == 7
        assert peak == 2

    async def test_failed_dependency_still_blocks_dependents(self, tools, tool_context) -> None:
        async def handler(step: PlanStep) -> str:
            if step.id.value == "s1":
                raise RuntimeError("boom")
            return "ok"

        plan = _plan(_gen("s1"), _gen("s2", "s1"), _gen("s3"))
        outcome = await ExecutionEngine(tools=tools).execute(
            plan, context=tool_context, handlers={StepKind.GENERATE: handler}
        )
        status = {s.id.value: s.status for s in outcome.plan.steps}
        assert status["s3"] is StepStatus.SUCCEEDED
        assert status["s1"] is StepStatus.FAILED
        assert status["s2"] is StepStatus.BLOCKED
