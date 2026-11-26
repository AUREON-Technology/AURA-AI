"""``TOOLS_MAX_CALLS_PER_TURN`` bounds a turn's tool calls (audit FSA-112).

The setting was parsed, documented and never read: the only tool bound on a run was the persona's
own ``tool_budget``, which every persona but ``engineer`` leaves unset. The setting is now the
default ``tool_budget`` for a persona that declares none; a persona's own figure still wins, and
``0`` keeps the old "not bounded by this" meaning.
"""

from __future__ import annotations

from dataclasses import replace

from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest


def _request(user, **overrides):
    base = dict(message="hello", user_id=user)
    base.update(overrides)
    return TurnRequest(**base)


async def test_a_persona_without_its_own_tool_budget_gets_the_configured_per_turn_cap(container, user) -> None:
    await container.startup()
    kernel = container.kernel
    settings = kernel._deps.settings
    kernel._deps.settings = replace(settings, tools=replace(settings.tools, max_calls_per_turn=4))
    loop = kernel._loop_arguments(_request(user), "hello", [], False, None, None)
    assert loop is not None, "the standard agent loop must be wired in the test container"
    assert loop["budget"].tool_budget == 4


async def test_zero_means_unbounded_as_before(container, user) -> None:
    await container.startup()
    kernel = container.kernel
    settings = kernel._deps.settings
    kernel._deps.settings = replace(settings, tools=replace(settings.tools, max_calls_per_turn=0))
    loop = kernel._loop_arguments(_request(user), "hello", [], False, None, None)
    assert loop["budget"].tool_budget is None


async def test_a_persona_s_own_budget_is_not_overridden(container, user) -> None:
    """The engineer persona declares ``tool_budget=216``; the per-turn default must not shrink it."""
    await container.startup()
    kernel = container.kernel
    engineer = kernel._deps.agents.get("engineer")
    assert engineer.tool_budget is not None
    settings = kernel._deps.settings
    kernel._deps.settings = replace(settings, tools=replace(settings.tools, max_calls_per_turn=4))
    loop = kernel._loop_arguments(
        _request(user, agent="engineer", capabilities=("engineer_agent",)), "hello", [], False, None, None
    )
    assert loop["budget"].tool_budget == engineer.tool_budget


async def test_the_per_turn_cap_blocks_excess_tool_calls_in_a_real_turn(container, user, scripted) -> None:
    """A plan with 3 tool steps on a turn capped at 2 executes 2 and blocks the 3rd."""
    import json

    await container.startup()
    kernel = container.kernel
    settings = kernel._deps.settings
    kernel._deps.settings = replace(settings, tools=replace(settings.tools, max_calls_per_turn=2))
    echo = container.router.get("echo")
    echo.queue(scripted(text=json.dumps({
        "steps": [
            {"id": "s1", "kind": "tool", "description": "calc 1", "tool": "calculator", "arguments": {"expression": "1+1"}},
            {"id": "s2", "kind": "tool", "description": "calc 2", "tool": "calculator", "arguments": {"expression": "2+2"}},
            {"id": "s3", "kind": "tool", "description": "calc 3", "tool": "calculator", "arguments": {"expression": "3+3"}},
            {"id": "s4", "kind": "respond", "description": "answer", "depends_on": ["s1", "s2", "s3"]},
        ]
    })))
    echo.queue(scripted(text="Done."))

    result = await kernel.run(_request(user, plan=True))
    assert result.tool_calls == ("calculator", "calculator")
    statuses = {s["id"]: s["status"] for s in result.plan_summary}
    assert statuses["s1"] == "succeeded" and statuses["s2"] == "succeeded"
    assert statuses["s3"] == "blocked"


async def test_the_per_turn_cap_applies_on_the_direct_executor_fallback_too(container, user, scripted) -> None:
    """T-08: even when ``agentic_executor`` or ``agents`` is absent, ``_plan_and_execute`` and
    ``_iter_execution`` still enforce ``TOOLS_MAX_CALLS_PER_TURN`` through ``RunBudgetGate``."""
    import json

    await container.startup()
    kernel = container.kernel
    settings = kernel._deps.settings
    kernel._deps.settings = replace(settings, tools=replace(settings.tools, max_calls_per_turn=1))
    kernel._deps = replace(kernel._deps, agentic_executor=None)
    echo = container.router.get("echo")
    echo.queue(scripted(text=json.dumps({
        "steps": [
            {"id": "s1", "kind": "tool", "description": "calc 1", "tool": "calculator", "arguments": {"expression": "1+1"}},
            {"id": "s2", "kind": "tool", "description": "calc 2", "tool": "calculator", "arguments": {"expression": "2+2"}},
            {"id": "s3", "kind": "respond", "description": "answer", "depends_on": ["s1", "s2"]},
        ]
    })))
    echo.queue(scripted(text="Done."))

    result = await kernel.run(_request(user, plan=True))
    assert result.tool_calls == ("calculator",)
    statuses = {s["id"]: s["status"] for s in result.plan_summary}
    assert statuses["s1"] == "succeeded" and statuses["s2"] == "blocked"


async def test_delegation_shares_the_root_turns_tool_cap(container, user, scripted) -> None:
    """T-08: a delegated sub-turn cannot reset the root turn's ``TOOLS_MAX_CALLS_PER_TURN``.

    With ``max_calls_per_turn=2``, the parent's ``delegate_to_agent`` call is dispatch #1; inside
    the child turn, the 1st ``calculator`` call is dispatch #2 (admitted) and the 2nd
    ``calculator`` call is dispatch #3 (refused by the parent gate).
    """
    import json

    await container.startup()
    kernel = container.kernel
    settings = kernel._deps.settings
    kernel._deps.settings = replace(settings, tools=replace(settings.tools, max_calls_per_turn=2))
    echo = container.router.get("echo")
    # 1. Parent plan: delegate to finance_advisor (subtask has a multi-step marker so the child plans)
    echo.queue(scripted(text=json.dumps({
        "steps": [
            {
                "id": "p1", "kind": "tool", "description": "delegate",
                "tool": "delegate_to_agent",
                "arguments": {"target_agent_id": "finance_advisor", "subtask": "First calculate 1+1 and then calculate 2+2"},
            },
            {"id": "p2", "kind": "respond", "description": "answer", "depends_on": ["p1"]},
        ]
    })))
    # 2. Child plan: 2 calculator calls (only the 1st fits in the root turn's remaining budget of 1)
    echo.queue(scripted(text=json.dumps({
        "steps": [
            {"id": "c1", "kind": "tool", "description": "calc 1", "tool": "calculator", "arguments": {"expression": "1+1"}},
            {"id": "c2", "kind": "tool", "description": "calc 2", "tool": "calculator", "arguments": {"expression": "2+2"}},
            {"id": "c3", "kind": "respond", "description": "answer", "depends_on": ["c1", "c2"]},
        ]
    })))
    # 3. Child answer, 4. Parent answer
    echo.queue(scripted(text="Child answer: 2."))
    echo.queue(scripted(text="Parent answer."))

    result = await kernel.run(_request(user, plan=True))
    assert result.tool_calls == ("delegate_to_agent",)
    runs = {r.agent_id: r for r in await container.agent_runs.list_runs(user)}
    child_run = runs["finance_advisor"]
    assert child_run.tool_calls == 1
    assert child_run.stop_reason == "tool_budget_exceeded"
    assert child_run.exhausted_budget == "tools"
