"""A host-routed tool call the server gives up on is cancelled on the daemon, and the server
waits long enough for a human to answer (audit FSA-041 / E3-02).

The daemon queues a side-effecting call for interactive approval and waits up to five minutes
for the human. The server's tool timeouts were 15 s for a write and 60 s for the Engineer loop,
so the server told the model the write had timed out (and the model retried), while the daemon
kept waiting -- and a late Approve wrote or ran anyway, with nothing upstream expecting it. Now:

* when a call times out or is cancelled on the server, the session sends the daemon a
  ``cancel`` frame for that call id, and the daemon marks the pending approval cancelled;
* a host-routed call's server-side budget is at least the approval window, so an approval
  answered within the daemon's five minutes is still a live call here.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.adapters.host_agent import HostAgentSession
from aura_core.adapters.docker_sandbox import SandboxError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ToolCategory, ToolSensitivity
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import tool


class _Wire:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def __call__(self, frame) -> None:
        self.frames.append(dict(frame))

    def call_id(self) -> str:
        return next(f["id"] for f in self.frames if f["type"] == "call")

    def cancelled(self, call_id: str) -> bool:
        return {"type": "cancel", "id": call_id} in self.frames


class TestTheDaemonIsToldWhenTheServerGivesUp:
    async def test_a_timed_out_call_sends_a_cancel_frame_and_ignores_a_late_answer(self) -> None:
        wire = _Wire()
        session = HostAgentSession(UserId("u1"), wire)
        with pytest.raises(SandboxError):
            await session.call("write_file", {"path": "a.txt"}, timeout_s=0.05)
        call_id = wire.call_id()
        assert wire.cancelled(call_id), wire.frames
        session.resolve({"type": "result", "id": call_id, "result": {"written": True}})  # late; ignored

    async def test_a_call_cancelled_by_the_tool_timeout_sends_a_cancel_frame(self) -> None:
        """The registry's own ``wait_for`` cancels the coroutine rather than letting it time out."""
        wire = _Wire()
        session = HostAgentSession(UserId("u1"), wire)
        task = asyncio.create_task(session.call("run", {"command": ["make"]}, timeout_s=30.0))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wire.cancelled(wire.call_id()), wire.frames

    async def test_a_silent_stream_is_cancelled_too(self) -> None:
        wire = _Wire()
        session = HostAgentSession(UserId("u1"), wire)
        with pytest.raises(SandboxError):
            async for _ in session.call_streaming("run", {"command": ["sleep"]}, timeout_s=0.05):
                pass
        assert wire.cancelled(wire.call_id()), wire.frames

    async def test_an_answered_call_sends_no_cancel(self) -> None:
        wire = _Wire()
        session = HostAgentSession(UserId("u1"), wire)
        pending = asyncio.create_task(session.call("read_file", {"path": "a"}, timeout_s=1.0))
        await asyncio.sleep(0)
        session.resolve({"type": "result", "id": wire.call_id(), "result": "hi"})
        assert await pending == "hi"
        assert not any(f["type"] == "cancel" for f in wire.frames)


async def _on_the_users_machine(context) -> bool:
    return True


class TestTheServerWaitsForTheApprovalWindow:
    async def test_a_host_routed_tool_outlives_its_sandbox_timeout(self, tools, user) -> None:
        """Spec timeout 50 ms, work takes 200 ms, approval window 500 ms: on the user's machine
        the call must still complete -- the daemon may be waiting on a human."""
        tools._host_routed = _on_the_users_machine
        tools.host_approval_window_s = 0.5

        @tool(
            "slow_host_write", "Writes on the user's machine after a human approves.",
            category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PERSONAL,
            side_effecting=True, workspace_local=True, timeout_s=0.05,
        )
        async def slow_host_write(context: ToolContext) -> str:
            await asyncio.sleep(0.2)
            return "written"

        tools.register(slow_host_write)
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5.0)
        result, _ = await tools.execute(ToolCall(id=ToolCallId("1"), name="slow_host_write", arguments={}), context)
        assert result.ok, result.content

    async def test_the_same_tool_in_the_sandbox_keeps_its_own_timeout(self, tools, user) -> None:
        async def never(context) -> bool:
            return False

        tools._host_routed = never
        tools.host_approval_window_s = 0.5

        @tool(
            "slow_sandbox_write", "Writes in the sandbox.",
            category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PERSONAL,
            side_effecting=True, workspace_local=True, timeout_s=0.05,
        )
        async def slow_sandbox_write(context: ToolContext) -> str:
            await asyncio.sleep(0.2)
            return "written"

        tools.register(slow_sandbox_write)
        context = ToolContext(user_id=user, correlation_id="c", deadline_s=5.0)
        result, _ = await tools.execute(ToolCall(id=ToolCallId("1"), name="slow_sandbox_write", arguments={}), context)
        assert not result.ok and result.error_kind == "tool_timeout"

    def test_the_engineer_loop_deadline_covers_the_longest_host_routed_tool(self) -> None:
        """run_command may run for 605 s *after* a human has taken up to the approval window to
        approve it; the loop's deadline must not cut that off."""
        from aura_core.config.settings import ReasoningSettings
        from aura_core.tools.registry import HOST_APPROVAL_WINDOW_S

        assert HOST_APPROVAL_WINDOW_S >= 330.0
        assert ReasoningSettings().engineer_loop_deadline_s >= 605.0 + HOST_APPROVAL_WINDOW_S
