"""Call-id-precise confirmation (vs. the older tool-name-only matching).

``ChatRequest.confirmed_call_ids`` lets a caller approve one specific pending tool call
(``"step_<plan-step-id>"``, ``ExecutionEngine._run_step``'s deterministic ``ToolCallId``) without
also approving every other pending call to the same tool name in the same turn -- the gap
``confirmed_tools: list[str]`` alone could not close.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanId, PlanStep, StepKind
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from aura_core.execution.engine import ExecutionEngine
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import FunctionTool
from aura_core.tools.registry import ToolPolicy, ToolRegistry

pytestmark = pytest.mark.asyncio


def _confirm_required_tool() -> FunctionTool:
    async def handler(text: str, context: ToolContext) -> str:
        return f"did: {text}"

    spec = ToolSpec(
        name="side_effecting_confirm_tool",
        description="A side-effecting, confidential tool -- always requires confirmation.",
        parameters=(ParameterSpec(name="text", type="string", description="text"),),
        category=ToolCategory.UTILITY,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
    )
    return FunctionTool(spec, handler)


@pytest.fixture
def confirm_tools() -> ToolRegistry:
    registry = ToolRegistry(policy=ToolPolicy())
    registry.register_all([_confirm_required_tool()])
    return registry


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0)


def _plan_with_two_calls() -> Plan:
    steps = (
        PlanStep(
            id=StepId("s1"), kind=StepKind.TOOL, tool="side_effecting_confirm_tool",
            description="first call", arguments={"text": "first"},
        ),
        PlanStep(
            id=StepId("s2"), kind=StepKind.TOOL, tool="side_effecting_confirm_tool",
            description="second call", arguments={"text": "second"},
        ),
    )
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)


class TestConfirmedCallIds:
    async def test_neither_call_runs_with_no_confirmation(self, confirm_tools, ctx) -> None:
        outcome = await ExecutionEngine(tools=confirm_tools).execute(
            _plan_with_two_calls(), context=ctx,
        )
        assert len(outcome.tool_reports) == 2
        assert all(r.needs_confirmation for r in outcome.tool_reports)
        assert all(not r.ok for r in outcome.tool_reports)

    async def test_call_id_confirms_only_the_matching_call(self, confirm_tools, ctx) -> None:
        """Two pending calls to the same tool name; only one call_id is confirmed."""
        outcome = await ExecutionEngine(tools=confirm_tools).execute(
            _plan_with_two_calls(), context=ctx, confirmed_call_ids=["step_s1"],
        )
        by_step = {r.call_id: r for r in outcome.tool_reports}
        assert by_step["step_s1"].ok is True
        assert by_step["step_s1"].needs_confirmation is False
        assert by_step["step_s2"].ok is False
        assert by_step["step_s2"].needs_confirmation is True

    async def test_name_based_confirmation_still_works(self, confirm_tools, ctx) -> None:
        """Back-compat: an old caller using only `confirmed_tools` still confirms every call."""
        outcome = await ExecutionEngine(tools=confirm_tools).execute(
            _plan_with_two_calls(), context=ctx,
            confirmed_tools=["side_effecting_confirm_tool"],
        )
        assert all(r.ok for r in outcome.tool_reports)
        assert all(not r.needs_confirmation for r in outcome.tool_reports)

    async def test_call_id_and_name_confirmation_compose(self, confirm_tools, ctx) -> None:
        """Either match confirms a call -- call_id is the precise path, name is the fallback."""
        outcome = await ExecutionEngine(tools=confirm_tools).execute(
            _plan_with_two_calls(), context=ctx,
            confirmed_tools=[], confirmed_call_ids=["step_s1", "step_s2"],
        )
        assert all(r.ok for r in outcome.tool_reports)
