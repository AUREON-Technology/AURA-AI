"""`workspace_local` is only an exemption where the workspace is actually disposable.

ADR-014 exempts `workspace_local` side effects (write_file, run_command, git_commit, ...) from
the taint gate because they act on a scratch checkout that is destroyed on expiry. Routed to a
paired desktop (`aura_hostd`, ADR-011), the same calls act on the user's real files as their OS
account. A tainted turn -- one that read a web page or a cloned README -- must not be able to run
them there unasked.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.host_agent import HostAgentSession
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ToolSpec
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import FunctionTool
from aura_core.tools.policy import ToolPolicy

USER = UserId("desk-user")
SPEC = ToolSpec(
    name="host_run", description="Run a command in the workspace.", side_effecting=True,
    workspace_local=True,
)


def _ctx(*, tainted: bool) -> ToolContext:
    return ToolContext(user_id=USER, correlation_id="c1", deadline_s=5.0, tainted=tainted)


class TestPolicy:
    def test_tainted_and_on_the_users_machine_needs_confirmation(self) -> None:
        decision = ToolPolicy().evaluate(SPEC, context=_ctx(tainted=True), host_routed=True)
        assert decision.requires_confirmation
        assert decision.reason == "side_effect_on_user_machine_in_tainted_context"

    def test_even_when_taint_blocking_is_configured_off(self) -> None:
        policy = ToolPolicy(block_side_effects_when_tainted=False)
        decision = policy.evaluate(SPEC, context=_ctx(tainted=True), host_routed=True)
        assert decision.requires_confirmation

    def test_tainted_in_the_sandbox_keeps_the_exemption(self) -> None:
        decision = ToolPolicy().evaluate(SPEC, context=_ctx(tainted=True), host_routed=False)
        assert decision.allowed and not decision.requires_confirmation

    def test_untainted_on_the_users_machine_keeps_the_exemption(self) -> None:
        decision = ToolPolicy().evaluate(SPEC, context=_ctx(tainted=False), host_routed=True)
        assert decision.allowed and not decision.requires_confirmation

    def test_a_confirmed_call_runs(self) -> None:
        decision = ToolPolicy().evaluate(
            SPEC, context=_ctx(tainted=True), host_routed=True, confirmed=True
        )
        assert decision.allowed and not decision.requires_confirmation


class TestWiring:
    """Through the container's real registry and host-agent registry."""

    @pytest.fixture
    def ran(self, container) -> list:
        calls: list = []

        async def handler(**_):
            calls.append(1)
            return "ok"

        container.tools.register(FunctionTool(SPEC, handler))
        return calls

    async def _call(self, container, *, tainted: bool):
        return await container.tools.execute(
            ToolCall(id=ToolCallId("t1"), name="host_run", arguments={}), _ctx(tainted=tainted)
        )

    async def test_a_paired_desktop_holds_a_tainted_call(self, container, ran) -> None:
        async def send(_frame) -> None:
            return None

        await container.host_agent_registry.register(HostAgentSession(USER, send))

        _, report = await self._call(container, tainted=True)

        assert report.needs_confirmation
        assert ran == []

    async def test_without_a_desktop_the_sandbox_exemption_holds(self, container, ran) -> None:
        _, report = await self._call(container, tainted=True)

        assert not report.needs_confirmation
        assert ran == [1]
