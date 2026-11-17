"""Engineer tool calls get the time their own specs promise (tool audit T-01, 2026-11-16).

Every tool call's server-side timeout was ``min(spec.timeout_s, context.deadline_s)``, and the
kernel gave every persona that cannot delegate a flat 60 s ``deadline_s``. ``run_command`` tells
the model it may run for up to 600 s and declares 605 s, so in the server sandbox it was killed at
60 s. On a paired desktop the call got ``max(60, approval window)`` = 330 s *in total*, although
the daemon may spend up to the approval window waiting for a human and only *then* start the
command (see ``ReasoningSettings.engineer_loop_deadline_s``, sized for exactly that sum).

The tests that already existed built ``ToolContext(deadline_s=5.0)`` by hand and never saw the
kernel's real context, which is how the cap went unnoticed. These use the kernel's own
``_tool_context`` and the registered ``run_command`` spec.
"""

from __future__ import annotations

import asyncio

from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ToolCategory, ToolSensitivity
from aura_core.kernel import TurnRequest
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import tool
from aura_core.tools.registry import HOST_APPROVAL_WINDOW_S, ToolRegistry


def _engineer_context(container, user) -> ToolContext:
    request = TurnRequest(
        message="run the tests", user_id=user, agent="engineer", capabilities=("engineer_agent",)
    )
    return container.kernel._tool_context(request, False)


def _run_command_spec(container):
    assert "run_command" in container.tools, "the sandbox tools register in the default container"
    return container.tools._tools["run_command"].spec


class TestTheKernelsRealContext:
    async def test_run_command_gets_its_whole_budget_in_the_server_sandbox(self, container, user) -> None:
        await container.startup()
        spec = _run_command_spec(container)
        context = _engineer_context(container, user)
        assert container.tools.call_timeout(spec, context, host_routed=False) == spec.timeout_s

    async def test_on_a_desktop_the_approval_wait_comes_on_top_of_the_run(self, container, user) -> None:
        await container.startup()
        spec = _run_command_spec(container)
        context = _engineer_context(container, user)
        assert (
            container.tools.call_timeout(spec, context, host_routed=True)
            == spec.timeout_s + HOST_APPROVAL_WINDOW_S
        )

    async def test_the_engineer_loop_still_outlasts_its_longest_call(self, container, user) -> None:
        """Raising the per-call ceiling must not let one call outlive the loop that made it."""
        await container.startup()
        spec = _run_command_spec(container)
        context = _engineer_context(container, user)
        loop_deadline = container.settings.reasoning.engineer_loop_deadline_s
        assert context.deadline_s <= loop_deadline
        assert container.tools.call_timeout(spec, context, host_routed=True) <= loop_deadline

    async def test_a_conversational_persona_keeps_the_short_deadline(self, container, user) -> None:
        await container.startup()
        context = container.kernel._tool_context(
            TurnRequest(message="hi", user_id=user, agent="task_planner"), False
        )
        assert context.deadline_s == 60.0


class TestTheRegistryAtSmallScale:
    """Real sleeps, scaled down: spec 0.2 s, approval window 0.3 s."""

    @staticmethod
    def _registry(*, on_desktop: bool, work_s: float) -> ToolRegistry:
        async def routed(context) -> bool:
            return on_desktop

        registry = ToolRegistry(host_routed=routed)
        registry.host_approval_window_s = 0.3

        @tool(
            "slow_command", "Runs a command.", category=ToolCategory.DEVELOPMENT,
            sensitivity=ToolSensitivity.PERSONAL, side_effecting=True, workspace_local=True,
            timeout_s=0.2,
        )
        async def slow_command(context: ToolContext) -> str:
            await asyncio.sleep(work_s)
            return "done"

        registry.register(slow_command)
        return registry

    @staticmethod
    async def _execute(registry: ToolRegistry, user, *, confirmed: bool = False,
                       tainted: bool = False):
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=10.0, tainted=tainted)
        return await registry.execute(
            ToolCall(id=ToolCallId("1"), name="slow_command", arguments={}), context,
            confirmed=confirmed,
        )

    @classmethod
    async def _call(cls, registry: ToolRegistry, user, *, confirmed: bool = False):
        result, _ = await cls._execute(registry, user, confirmed=confirmed)
        return result

    async def test_a_desktop_call_may_wait_for_approval_and_then_run_its_full_time(self, user) -> None:
        # 0.25 s of approval wait + 0.15 s of running: past either budget alone, within their sum.
        result = await self._call(self._registry(on_desktop=True, work_s=0.4), user)
        assert result.ok, result.content

    async def test_a_desktop_call_past_both_budgets_still_times_out(self, user) -> None:
        result = await self._call(self._registry(on_desktop=True, work_s=0.7), user)
        assert not result.ok and result.error_kind == "tool_timeout"

    async def test_a_sandbox_call_has_no_approval_wait_added(self, user) -> None:
        result = await self._call(self._registry(on_desktop=False, work_s=0.4), user)
        assert not result.ok and result.error_kind == "tool_timeout"

    async def test_a_confirmed_desktop_call_still_gets_the_approval_window(self, user) -> None:
        """Tool audit T-02. Server-side confirmation does not skip the daemon's own approval: it
        still applies its command policy and may hold the call for a local human. Without the
        window the server gave up at the spec's budget while the daemon kept waiting, and a late
        Approve then ran with nothing upstream expecting it -- FSA-041 for confirmed calls."""
        result = await self._call(self._registry(on_desktop=True, work_s=0.4), user, confirmed=True)
        assert result.ok, result.content

    async def test_a_confirmed_sandbox_call_has_no_approval_wait_added(self, user) -> None:
        result = await self._call(self._registry(on_desktop=False, work_s=0.4), user, confirmed=True)
        assert not result.ok and result.error_kind == "tool_timeout"

    async def test_confirmation_still_decides_policy_for_a_tainted_desktop_call(self, user) -> None:
        """Routing feeds the timeout; the policy decision for confirmed calls is unchanged."""
        registry = self._registry(on_desktop=True, work_s=0.0)

        _, unconfirmed = await self._execute(registry, user, tainted=True)
        assert unconfirmed.needs_confirmation
        assert unconfirmed.policy_reason == "side_effect_on_user_machine_in_tainted_context"

        result, confirmed = await self._execute(registry, user, tainted=True, confirmed=True)
        assert result.ok, result.content
        assert confirmed.allowed and not confirmed.needs_confirmation
        assert confirmed.policy_reason == "user_confirmed"
