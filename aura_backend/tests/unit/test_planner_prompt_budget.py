"""The planner prompt stays inside a character cap, however many tools exist (ticket problem 4),
and a replan sees short excerpts of what finished steps found (problem 5)."""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.kernel import TurnRequest
from aura_core.planning import PlanningEngine
from aura_core.planning.engine import select_tool_lines


def _engine(container) -> PlanningEngine:
    return container.planner


async def _declarations(container, agent: str, caps: tuple[str, ...]):
    await container.startup()
    request = TurnRequest(
        message="analyse the repository and summarise it", user_id=UserId("u"),
        agent=agent, capabilities=caps,
    )
    return await container.kernel._tool_declarations(request)


class TestPromptCap:
    async def test_plain_caller_prompt_is_at_most_6000_chars(self, container) -> None:
        tools = await _declarations(container, "orchestrator", ())
        assert len(tools) > 30, "the default registry is large enough for the cap to matter"
        rendered = _engine(container)._render_prompt("what is the leave policy?", tools, 12, 6000)
        assert len(rendered.text) <= 6000

    async def test_engineer_prompt_is_at_most_8000_chars(self, container) -> None:
        tools = await _declarations(container, "engineer", ("engineer_agent",))
        rendered = _engine(container)._render_prompt("run the tests", tools, 12, 8000)
        assert len(rendered.text) <= 8000

    async def test_a_huge_goal_cannot_blow_the_cap(self, container) -> None:
        tools = await _declarations(container, "orchestrator", ())
        rendered = _engine(container)._render_prompt("x " * 20000, tools, 12, 6000)
        assert len(rendered.text) <= 6000

    async def test_the_tool_the_goal_names_is_always_shown(self, container) -> None:
        tools = await _declarations(container, "orchestrator", ())
        last = tools[-1]["name"]
        rendered = _engine(container)._render_prompt(f"please use {last} now", tools, 12, 6000)
        assert f"- {last}" in rendered.text

    async def test_relevant_tools_outrank_the_registry_order(self, container) -> None:
        tools = await _declarations(container, "orchestrator", ())
        lines = select_tool_lines("add a task to buy milk", tools, 1500)
        assert any("task" in line.split(":")[0] for line in lines[:3])

    async def test_without_tools_there_is_no_tool_block(self, container) -> None:
        rendered = _engine(container)._render_prompt("hello", (), 12, 6000)
        assert "Tools available" not in rendered.text


class TestReplanSeesResults:
    async def test_replan_goal_carries_a_clipped_excerpt_of_a_finished_step(self, container) -> None:
        seen: dict[str, str] = {}
        engine = _engine(container)

        async def fake_plan(goal, **_kw):
            seen["goal"] = goal.description
            return Plan(id=PlanId.new(), goal=goal, steps=())

        engine.plan = fake_plan  # type: ignore[method-assign]
        done = PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="read_file", description="Read config",
        ).started().succeeded("line one\nline two " + "z" * 500, 1.0)
        bad = PlanStep(
            id=StepId("s2"), kind=StepKind.TOOL, tool="run_command", description="Run it",
        ).started().failed("timeout", 1.0)
        plan = Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(done, bad))

        await engine.replan(plan, reason="step_failed", available_tools=())

        goal = seen["goal"]
        assert "line one line two" in goal
        assert "untrusted data" in goal
        assert "z" * 400 not in goal  # clipped
        assert "Failed: Run it (timeout)" in goal
