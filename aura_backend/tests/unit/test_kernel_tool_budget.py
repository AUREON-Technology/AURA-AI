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
