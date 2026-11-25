"""MCP client failure handling (audit T-04).

Drives the real :class:`aura_core.adapters.mcp_client.McpClient` and its ``_ServerSession``
background task, substituting only the piece that opens the SDK connection (``session_factory``)
with an in-process fake. The fake dies the way the real SDK does: when the server process exits,
``ClientSession``'s task group cancels the body of the ``async with`` and its ``__aexit__``
re-raises the transport error -- so the session's own ``_run``/``_serve`` code sees exactly the
control flow production would.

Every call is wrapped in ``asyncio.wait_for(..., 1.0)``: before the fix these calls never
completed, so a hang shows up as a ``TimeoutError`` failure instead of a stuck suite.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from aura_core.adapters.mcp_client import McpClient, McpConnectionError, McpToolError
from aura_core.config.settings import McpServerConfig
from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_mcp_bridge_tools
from aura_core.tools.registry import ToolRegistry

FAST = 1.0


class _FakeServer:
    """One MCP server across reconnects: counts connections and can kill the live one."""

    def __init__(self) -> None:
        self.connections = 0
        self.current: _FakeConnection | None = None
        self.in_flight = asyncio.Event()
        self.error_text: str | None = None

    def factory(self, config: McpServerConfig) -> "_FakeConnection":
        return _FakeConnection(self)

    def kill(self) -> None:
        assert self.current is not None
        self.current.kill()


class _FakeConnection:
    def __init__(self, server: _FakeServer) -> None:
        self._server = server
        self._task: asyncio.Task | None = None
        self._killed = False

    async def __aenter__(self) -> "_FakeSession":
        self._task = asyncio.current_task()
        self._server.connections += 1
        self._server.current = self
        return _FakeSession(self._server, generation=self._server.connections)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._killed and exc_type is asyncio.CancelledError:
            assert self._task is not None
            self._task.uncancel()
            raise ConnectionResetError("MCP server process exited")
        return False

    def kill(self) -> None:
        self._killed = True
        assert self._task is not None
        self._task.cancel()


class _FakeSession:
    def __init__(self, server: _FakeServer, generation: int) -> None:
        self._server = server
        self._generation = generation

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[
            SimpleNamespace(
                name="read", description="Read a file.",
                inputSchema={"type": "object", "properties": {"path": {"type": "string"}},
                             "required": ["path"]},
                outputSchema=None,
            ),
        ])

    async def call_tool(self, name: str, arguments: dict) -> SimpleNamespace:
        if name == "slow":
            self._server.in_flight.set()
            await asyncio.Event().wait()  # only a dying server ends this
        if self._server.error_text is not None:
            return SimpleNamespace(
                isError=True, structuredContent=None,
                content=[SimpleNamespace(text=self._server.error_text)],
            )
        return SimpleNamespace(
            isError=False, structuredContent=None,
            content=[SimpleNamespace(text=f"gen{self._generation}:{arguments.get('path')}")],
        )


def _client(server: _FakeServer) -> McpClient:
    return McpClient(
        servers=[McpServerConfig(name="fs", command="unused")],
        session_factory=server.factory,
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_call_after_server_died_fails_fast_instead_of_hanging() -> None:
    server = _FakeServer()
    client = _client(server)
    try:
        assert await asyncio.wait_for(client.call_tool("fs", "read", {"path": "a"}), FAST) == "gen1:a"
        session = client._sessions["fs"]
        server.kill()
        await _settle()

        # The dead session itself refuses work promptly, carrying the cause.
        with pytest.raises(McpConnectionError) as info:
            await asyncio.wait_for(session.call_tool("read", {"path": "b"}), FAST)
        assert "ConnectionResetError" in str(info.value)
        with pytest.raises(McpConnectionError):
            await asyncio.wait_for(session.list_tools(), FAST)
    finally:
        await client.close()


async def test_request_in_flight_when_server_dies_is_failed_not_hung() -> None:
    server = _FakeServer()
    client = _client(server)
    try:
        pending = asyncio.create_task(client.call_tool("fs", "slow", {}))
        await asyncio.wait_for(server.in_flight.wait(), FAST)
        # A second request queued behind the in-flight one must be failed too.
        session = client._sessions["fs"]
        queued = asyncio.create_task(session.call_tool("read", {"path": "q"}))
        await _settle()
        server.kill()

        with pytest.raises(McpConnectionError, match="MCP server process exited"):
            await asyncio.wait_for(pending, FAST)
        with pytest.raises(McpConnectionError):
            await asyncio.wait_for(queued, FAST)
    finally:
        await client.close()


async def test_next_call_after_death_reconnects_and_succeeds() -> None:
    server = _FakeServer()
    client = _client(server)
    try:
        assert await asyncio.wait_for(client.call_tool("fs", "read", {"path": "a"}), FAST) == "gen1:a"
        server.kill()
        await _settle()

        result = await asyncio.wait_for(client.call_tool("fs", "read", {"path": "b"}), FAST)
        assert result == "gen2:b"
        assert server.connections == 2
        # Discovery recovers the same way.
        tools = await asyncio.wait_for(client.list_tools(), FAST)
        assert [t.name for t in tools] == ["read"]
        assert server.connections == 2
    finally:
        await client.close()


async def test_is_error_result_surfaces_as_failed_tool_call_with_server_text(user) -> None:
    server = _FakeServer()
    client = _client(server)
    try:
        with_error = "permission denied: /etc/shadow"
        server.error_text = with_error

        with pytest.raises(McpToolError, match="permission denied"):
            await asyncio.wait_for(client.call_tool("fs", "read", {"path": "x"}), FAST)

        registry = ToolRegistry()
        for tool in await build_mcp_bridge_tools(client, safe_tools=["mcp_fs_read"]):
            registry.register(tool)
        ctx = ToolContext(user_id=user, correlation_id="corr-mcp-err", deadline_s=30.0)
        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="mcp_fs_read", arguments={"path": "/etc/shadow"}),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert result.ok is False
        assert report.ok is False
        assert with_error in result.content
    finally:
        await client.close()


class TestSdkFieldSpellings:
    """``mcp==2.3.0`` (the locked version) spells these fields in snake_case; 1.x used camelCase."""

    def test_the_field_helper_reads_either_spelling(self) -> None:
        from types import SimpleNamespace

        from aura_core.adapters.mcp_client import _field

        assert _field(SimpleNamespace(input_schema={"a": 1}), "input_schema", "inputSchema") == {"a": 1}
        assert _field(SimpleNamespace(inputSchema={"b": 2}), "input_schema", "inputSchema") == {"b": 2}
        assert _field(SimpleNamespace(), "is_error", "isError", False) is False
        assert _field(SimpleNamespace(is_error=True), "is_error", "isError", False) is True

    async def test_a_snake_case_result_reports_its_error_instead_of_succeeding(self) -> None:
        from types import SimpleNamespace

        from aura_core.adapters.mcp_client import McpClient, McpToolError

        class Session:
            async def call_tool(self, name, arguments):
                return SimpleNamespace(is_error=True, structured_content=None, content=[SimpleNamespace(text="boom")])

        client = McpClient.__new__(McpClient)
        client._configs = {"s": object()}

        async def session_for(server):
            return Session()

        client._session_for = session_for
        with pytest.raises(McpToolError, match="boom"):
            await client.call_tool("s", "t", {})

    async def test_a_snake_case_structured_result_is_returned_as_json(self) -> None:
        from types import SimpleNamespace

        from aura_core.adapters.mcp_client import McpClient

        class Session:
            async def call_tool(self, name, arguments):
                return SimpleNamespace(is_error=False, structured_content={"n": 3}, content=[])

        client = McpClient.__new__(McpClient)
        client._configs = {"s": object()}

        async def session_for(server):
            return Session()

        client._session_for = session_for
        assert json.loads(await client.call_tool("s", "t", {})) == {"n": 3}
