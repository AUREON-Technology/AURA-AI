"""MCP (Model Context Protocol) client adapter.

The official ``mcp`` SDK's ``ClientSession`` is an async context manager whose lifetime must span
every call made through it -- there is no "open a handle, call it later" API. So each configured
server gets one background task that owns that context manager for the adapter's lifetime, and
every public method bridges through a queue/future pair into that task. This is the same shape
every lazy adapter in this package uses to defer a real connection to first use
(``PostgresPool._get_pool()`` is the simpler, non-context-manager version of the same idea) --
here the "connection" happens to require an ``async with`` that never exits until shutdown.

``mcp`` is imported lazily inside :meth:`_ServerSession._run`, never at module scope, matching
every other adapter's rule that importing this file must never require the dependency, let alone
a reachable process, to be present.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from ..config.settings import McpServerConfig
from ..observability.logging import get_logger
from ..ports.coding import McpToolDescriptor

__all__ = ["McpClient", "McpConnectionError"]

logger = get_logger("adapters.mcp_client")


class McpConnectionError(RuntimeError):
    """Raised when a configured MCP server cannot be reached or fails during the handshake."""


class _ServerSession:
    """Owns one MCP server's connection in a background task for as long as the adapter lives."""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._requests: asyncio.Queue = asyncio.Queue()
        self._failed: BaseException | None = None

    async def _run(self) -> None:
        try:
            if self._config.transport == "stdio":
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client

                params = StdioServerParameters(
                    command=self._config.command, args=list(self._config.args)
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await self._serve(session)
            else:
                from mcp import ClientSession
                from mcp.client.sse import sse_client

                async with sse_client(self._config.url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await self._serve(session)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # pragma: no cover - depends on an external process/server
            self._failed = exc
            logger.warning(
                "MCP server session failed",
                extra={"server": self._config.name, "error": type(exc).__name__},
            )
        finally:
            self._ready.set()

    async def _serve(self, session: Any) -> None:
        self._ready.set()
        while True:
            request = await self._requests.get()
            if request is None:
                return
            kind, payload, future = request
            if future.cancelled():
                continue
            try:
                if kind == "list_tools":
                    future.set_result(await session.list_tools())
                else:
                    name, arguments = payload
                    future.set_result(await session.call_tool(name, arguments))
            except Exception as exc:  # noqa: BLE001 - relayed to the waiting caller, not swallowed
                future.set_exception(exc)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._failed is not None:
            raise McpConnectionError(
                f"MCP server {self._config.name!r} failed to start: "
                f"{type(self._failed).__name__}: {self._failed}"
            ) from self._failed

    async def list_tools(self) -> Any:
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._requests.put(("list_tools", None, future))
        return await future

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._requests.put(("call_tool", (name, dict(arguments)), future))
        return await future

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._requests.put(None)
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None


class McpClient:
    """Bridges configured MCP servers to :class:`~aura_core.ports.coding.McpClient`.

    Each server connects lazily on first use (:meth:`_session_for`, behind an ``asyncio.Lock``
    with a re-check inside it, the same double-checked-locking shape as
    ``PostgresPool._get_pool()``) rather than at construction, so building this adapter never
    requires a reachable server.
    """

    def __init__(self, *, servers: Sequence[McpServerConfig]) -> None:
        self._configs = {s.name: s for s in servers}
        self._sessions: dict[str, _ServerSession] = {}
        self._lock = asyncio.Lock()

    async def _session_for(self, name: str) -> _ServerSession:
        if name not in self._sessions:
            async with self._lock:
                if name not in self._sessions:
                    session = _ServerSession(self._configs[name])
                    await session.start()
                    self._sessions[name] = session
        return self._sessions[name]

    async def list_tools(self) -> Sequence[McpToolDescriptor]:
        """Every tool every configured server currently advertises.

        A server that cannot be reached is logged and skipped rather than failing the whole
        discovery pass -- one misconfigured server should not take every other server's tools
        away too.
        """
        descriptors: list[McpToolDescriptor] = []
        for name in self._configs:
            try:
                session = await self._session_for(name)
                result = await session.list_tools()
            except Exception as exc:
                logger.warning(
                    "could not list tools from MCP server",
                    extra={"server": name, "error": type(exc).__name__},
                )
                continue
            for tool in getattr(result, "tools", None) or []:
                descriptors.append(
                    McpToolDescriptor(
                        server=name,
                        name=getattr(tool, "name", ""),
                        description=getattr(tool, "description", "") or "",
                        input_schema=dict(getattr(tool, "inputSchema", None) or {}),
                    )
                )
        return descriptors

    async def call_tool(self, server: str, name: str, arguments: Mapping[str, Any]) -> str:
        if server not in self._configs:
            raise McpConnectionError(f"unknown MCP server {server!r}")
        session = await self._session_for(server)
        result = await session.call_tool(name, arguments)
        blocks = getattr(result, "content", None) or []
        parts = [text for block in blocks if (text := getattr(block, "text", None))]
        return "\n".join(parts) if parts else str(result)

    async def close(self) -> None:
        for session in self._sessions.values():
            await session.stop()
        self._sessions.clear()
