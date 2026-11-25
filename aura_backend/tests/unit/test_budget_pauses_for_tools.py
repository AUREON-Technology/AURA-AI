"""Tool time is not charged to a chat turn's generation budget (ticket problem 3).

With a 45 s budget and one 40 s tool, the planner call started the clock and the tool then ate it,
so the answer generation ended in ``turn_deadline``. The budget now pauses while tools run.
Durations are scaled 1/100 for the kernel test (0.45 s budget, 0.40 s tool).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.llm.deadline import (
    GenerationBudget,
    generation_budget,
    paused_generation_budget,
    time_left,
)


class TestPause:
    def test_paused_time_is_not_charged(self) -> None:
        b = GenerationBudget(45.0)
        assert b.remaining(0.0) == 45.0          # planner call starts the clock
        b.pause(5.0)
        assert b.remaining(30.0) == 40.0         # frozen at what was left when paused
        b.resume(45.0)                           # a 40 s tool ran
        assert b.remaining(45.0) == 40.0         # the answer still has its 40 s
        assert b.remaining(60.0) == 25.0         # and the clock runs again

    def test_pause_before_the_clock_started_is_harmless(self) -> None:
        b = GenerationBudget(10.0)
        b.pause(0.0)
        b.resume(100.0)
        assert b.remaining(100.0) == 10.0

    def test_pauses_nest(self) -> None:
        b = GenerationBudget(10.0)
        b.remaining(0.0)
        b.pause(1.0)
        b.pause(2.0)
        b.resume(3.0)
        assert b.remaining(50.0) == 9.0          # still paused by the outer scope
        b.resume(11.0)
        assert b.remaining(11.0) == 9.0

    def test_no_ambient_budget_is_a_no_op(self) -> None:
        with paused_generation_budget(time.monotonic):
            assert time_left(time.monotonic) is None

    def test_context_manager_resumes_on_error(self) -> None:
        with generation_budget(10.0) as b:
            assert b is not None
            clock = iter([0.0, 1.0, 2.0, 3.0, 4.0])
            b.remaining(0.0)
            with pytest.raises(RuntimeError):
                with paused_generation_budget(lambda: next(clock)):
                    raise RuntimeError("tool blew up")
            assert b._paused_at is None


class TestEnginePausesDuringToolDispatch:
    async def test_a_tool_longer_than_most_of_the_budget_leaves_the_answer_its_time(self) -> None:
        from types import SimpleNamespace

        from aura_core.domain.messages import ToolResult
        from aura_core.execution import ExecutionEngine
        from aura_core.ports.runtime import ToolContext

        class _Tools:
            def spec(self, name):
                raise KeyError(name)

            async def execute(self, call, context, **_kw):
                await asyncio.sleep(0.40)  # the "40 s" tool, scaled 1/100
                return ToolResult(call_id=call.id, name=call.name, content="done", ok=True), None

        plan = Plan(
            id=PlanId.new(), goal=Goal(description="g"),
            steps=(PlanStep(kind=StepKind.TOOL, tool="slow", id=StepId("s1"), description="t"),),
        )
        context = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)
        engine = ExecutionEngine(tools=_Tools())

        with generation_budget(0.45) as budget:  # the "45 s" budget
            assert budget is not None
            budget.remaining(time.monotonic())   # the planner call starts the clock
            outcome = await engine.execute(plan, context=context)
            left = budget.remaining(time.monotonic())

        assert outcome.succeeded
        assert left > 0.35, f"the tool was charged to the answer's budget (left={left:.3f}s)"

    async def test_two_overlapping_tools_are_one_pause(self) -> None:
        from aura_core.domain.messages import ToolResult
        from aura_core.execution import ExecutionEngine
        from aura_core.ports.runtime import ToolContext

        class _Tools:
            def spec(self, name):
                raise KeyError(name)

            async def execute(self, call, context, **_kw):
                await asyncio.sleep(0.2)
                return ToolResult(call_id=call.id, name=call.name, content="d", ok=True), None

        plan = Plan(
            id=PlanId.new(), goal=Goal(description="g"),
            steps=tuple(
                PlanStep(kind=StepKind.TOOL, tool="slow", id=StepId(f"s{i}"), description="t")
                for i in (1, 2)
            ),
        )
        context = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)
        with generation_budget(1.0) as budget:
            assert budget is not None
            budget.remaining(time.monotonic())
            await ExecutionEngine(tools=_Tools()).execute(plan, context=context)
            assert budget.remaining(time.monotonic()) > 0.95
