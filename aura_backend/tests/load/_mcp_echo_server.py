"""Standalone stdio MCP server used only by test_mcp_live.py.

Launched as a subprocess (`McpServerConfig(transport="stdio", command=sys.executable,
args=(str(__file__),))`) -- never imported directly, and its filename deliberately does not match
`pytest.ini`'s `python_files = test_*.py bench_*.py`, so it is never collected as a test module
itself.

Targets `mcp==2.1.1` (this repo's locked version): `MCPServer` is the renamed successor to the
1.x `FastMCP` API (`mcp.server.fastmcp.FastMCP` no longer exists). If a future `requirements.lock`
bump changes this SDK surface again, this file is the one place that needs updating for
test_mcp_live.py to keep working -- the same kind of SDK-version risk
`aura_core/adapters/mcp_client.py`'s own client-side imports already depend on being current.
"""

from __future__ import annotations

from mcp.server import MCPServer

server = MCPServer(name="aura-test-echo")


@server.tool()
def echo(text: str) -> str:
    """Return `text` unchanged, prefixed -- proves a real round trip happened."""
    return f"echo: {text}"


if __name__ == "__main__":
    server.run("stdio")
