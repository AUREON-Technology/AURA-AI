"""Load-test proof that McpClient bridges a real MCP server subprocess, not
InMemoryMcpClient's scripted handlers.

The server under test is `tests/load/_mcp_echo_server.py`: a Python MCP server built with the
same `mcp` SDK already pinned as a runtime dependency (`requirements.lock`: `mcp==2.1.1`),
launched exactly the way `McpClient`/`McpServerConfig(transport="stdio")` launches any real MCP
server in production. Deliberately does not depend on `npx`/Node or any external MCP server
package: `mcp>=1.0.0` is already installed, and `McpClient` doesn't care what language a server is
written in, only that it speaks real MCP over a real stdio subprocess -- a Python server built
from the SDK already in `requirements.lock` satisfies that exactly as validly, with nothing new
to install.

Gated on `AURA_MCP_LIVE_TEST=1` rather than probing anything -- the server here is self-contained
and always available once `mcp` is installed; the flag exists so a contributor without `mcp`
installed gets a clean skip instead of an import error.

    export AURA_MCP_LIVE_TEST=1
    pytest tests/load/test_mcp_live.py -m load -s
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aura_core.adapters.mcp_client import McpClient, McpConnectionError
from aura_core.config.settings import McpServerConfig

pytestmark = pytest.mark.load

_SERVER_SCRIPT = Path(__file__).parent / "_mcp_echo_server.py"


def _mcp_unavailable_reason() -> str | None:
    if os.environ.get("AURA_MCP_LIVE_TEST") != "1":
        return "AURA_MCP_LIVE_TEST=1 not set"
    try:
        import mcp  # noqa: F401
    except ImportError:
        return "the `mcp` package is not installed"
    return None


@pytest.fixture
async def mcp_client():
    reason = _mcp_unavailable_reason()
    if reason:
        pytest.skip(f"mcp live test not runnable: {reason}")

    config = McpServerConfig(
        name="echo", transport="stdio", command=sys.executable, args=(str(_SERVER_SCRIPT),),
    )
    client = McpClient(servers=[config])
    try:
        yield client
    finally:
        await client.close()


class TestMcpBridge:
    async def test_list_tools_discovers_the_real_server(self, mcp_client) -> None:
        tools = await mcp_client.list_tools()
        by_name = {t.name: t for t in tools}
        assert "echo" in by_name
        assert by_name["echo"].server == "echo"

    async def test_call_tool_round_trips_through_a_real_subprocess(self, mcp_client) -> None:
        result = await mcp_client.call_tool("echo", "echo", {"text": "hello from the live test"})
        assert result == "echo: hello from the live test"

    async def test_unknown_server_raises(self, mcp_client) -> None:
        with pytest.raises(McpConnectionError, match="unknown MCP server"):
            await mcp_client.call_tool("not-configured", "echo", {"text": "x"})

    async def test_unreachable_server_raises_connection_error(self) -> None:
        # list_tools() deliberately catches and logs per-server failures rather than raising (so
        # one bad server doesn't take every other server's tools away, mcp_client.py), so proving
        # a genuinely unreachable command fails has to go through call_tool(), which does not
        # swallow that exception -- asserting this via list_tools() instead would silently pass
        # for the wrong reason (an empty result, not a caught error).
        bad_config = McpServerConfig(
            name="bad", transport="stdio", command="/nonexistent/binary-xyz", args=(),
        )
        client = McpClient(servers=[bad_config])
        try:
            with pytest.raises(McpConnectionError):
                await client.call_tool("bad", "whatever", {})
        finally:
            await client.close()
