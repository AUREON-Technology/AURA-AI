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
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, AsyncIterator, Callable, Mapping, Sequence

from ..config.settings import McpServerConfig
from ..observability.logging import get_logger
from ..ports.coding import McpToolDescriptor

__all__ = ["McpClient", "McpConnectionError", "McpToolError"]

logger = get_logger("adapters.mcp_client")


class McpConnectionError(RuntimeError):
    """Raised when a configured MCP server cannot be reached or fails during the handshake."""


class McpToolError(RuntimeError):
    """Raised when an MCP tool ran but reported failure (``isError=True``)."""


SessionFactory = Callable[[McpServerConfig], AsyncContextManager[Any]]


@asynccontextmanager
async def _open_sdk_session(config: McpServerConfig) -> AsyncIterator[Any]:
    """Open and initialise one SDK ``ClientSession`` for ``config``; it lives until exit."""
    from mcp import ClientSession

    if config.transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        transport = stdio_client(
            StdioServerParameters(command=config.command, args=list(config.args))
        )
    else:
        from mcp.client.sse import sse_client

        transport = sse_client(config.url)
    async with transport as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


class _ServerSession:
    """Owns one MCP server's connection in a background task for as long as the adapter lives.

    Once ``_run`` exits -- the server process died, the connection dropped, or :meth:`stop` was
    called -- the session is dead for good: every request still queued or in flight is failed
    with :class:`McpConnectionError` carrying the cause, and every later request raises it at
    once instead of enqueueing work nobody will read (audit T-04). :class:`McpClient` notices a
    dead session (:attr:`alive`) and replaces it on the next call.
    """

    def __init__(
        self, config: McpServerConfig, session_factory: SessionFactory = _open_sdk_session
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._requests: asyncio.Queue = asyncio.Queue()
        self._failed: BaseException | None = None
        self._closed = False
        self._in_flight: asyncio.Future | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._closed and not self._task.done()

    async def _run(self) -> None:
        try:
            async with self._session_factory(self._config) as session:
                await self._serve(session)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - recorded and relayed to every waiter
            self._failed = exc
            logger.warning(
                "MCP server session failed",
                extra={"server": self._config.name, "error": type(exc).__name__},
            )
        finally:
            # Synchronous from here on: no request can slip into the queue between marking the
            # session closed and draining it, because the submit path checks ``_closed`` and
            # enqueues without yielding.
            self._closed = True
            self._fail_pending()
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
            self._in_flight = future
            try:
                if kind == "list_tools":
                    result = await session.list_tools()
                else:
                    name, arguments = payload
                    result = await session.call_tool(name, arguments)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:  # noqa: BLE001 - relayed to the waiting caller, not swallowed
                if not future.done():
                    future.set_exception(exc)
            # Deliberately not in a ``finally``: when the connection dies mid-call the SDK cancels
            # this body, and ``_run`` must still find the in-flight future to fail it.
            self._in_flight = None

    def _connection_error(self) -> McpConnectionError:
        if self._failed is not None:
            error = McpConnectionError(
                f"MCP server {self._config.name!r} connection is gone: "
                f"{type(self._failed).__name__}: {self._failed}"
            )
            error.__cause__ = self._failed
            return error
        return McpConnectionError(f"MCP server {self._config.name!r} session is closed")

    def _fail_pending(self) -> None:
        error = self._connection_error()
        pending = [self._in_flight] if self._in_flight is not None else []
        while not self._requests.empty():
            request = self._requests.get_nowait()
            if request is not None:
                pending.append(request[2])
        for future in pending:
            if not future.done():
                future.set_exception(error)
        self._in_flight = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._failed is not None:
            raise McpConnectionError(
                f"MCP server {self._config.name!r} failed to start: "
                f"{type(self._failed).__name__}: {self._failed}"
            ) from self._failed

    async def _submit(self, kind: str, payload: Any) -> Any:
        if not self.alive:
            raise self._connection_error()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._requests.put_nowait((kind, payload, future))
        return await future

    async def list_tools(self) -> Any:
        return await self._submit("list_tools", None)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        return await self._submit("call_tool", (name, dict(arguments)))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._requests.put_nowait(None)
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None
        self._closed = True
        self._fail_pending()


class McpClient:
    """Bridges configured MCP servers to :class:`~aura_core.ports.coding.McpClient`.

    Each server connects lazily on first use (:meth:`_session_for`, behind an ``asyncio.Lock``
    with a re-check inside it, the same double-checked-locking shape as
    ``PostgresPool._get_pool()``) rather than at construction, so building this adapter never
    requires a reachable server.
    """

    def __init__(
        self,
        *,
        servers: Sequence[McpServerConfig],
        session_factory: SessionFactory = _open_sdk_session,
    ) -> None:
        self._configs = {s.name: s for s in servers}
        self._session_factory = session_factory
        self._sessions: dict[str, _ServerSession] = {}
        self._lock = asyncio.Lock()

    async def _session_for(self, name: str) -> _ServerSession:
        session = self._sessions.get(name)
        if session is not None and session.alive:
            return session
        async with self._lock:
            session = self._sessions.get(name)
            if session is None or not session.alive:
                if session is not None:
                    # The server died since its last use: evict it and connect afresh. One
                    # attempt per call -- a server that is still down fails this call with the
                    # start error and is not cached, so the next call simply tries again.
                    del self._sessions[name]
                    logger.info("reconnecting to MCP server", extra={"server": name})
                    await session.stop()
                session = _ServerSession(self._configs[name], self._session_factory)
                await session.start()
                self._sessions[name] = session
        return session

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
                        output_schema=dict(getattr(tool, "outputSchema", None) or {}) or None,
                    )
                )
        return descriptors

    async def call_tool(self, server: str, name: str, arguments: Mapping[str, Any]) -> str:
        if server not in self._configs:
            raise McpConnectionError(f"unknown MCP server {server!r}")
        session = await self._session_for(server)
        result = await session.call_tool(name, arguments)
        structured = getattr(result, "structuredContent", None)
        if structured is not None and not getattr(result, "isError", False):
            # A tool with an outputSchema returns structuredContent (MCP 2025-06-18); returning it
            # as JSON is what lets ToolRegistry.execute check it against that schema (ADR-025).
            return json.dumps(structured, ensure_ascii=False, default=str)
        blocks = getattr(result, "content", None) or []
        parts = [text for block in blocks if (text := getattr(block, "text", None))]
        if getattr(result, "isError", False):
            # The tool ran and failed. Raising (rather than returning the error text as if it
            # were output) is what makes ToolRegistry report ok=False; FunctionTool carries this
            # message into the result the model sees.
            detail = "\n".join(parts) or "no error details"
            raise McpToolError(f"MCP tool {name!r} on server {server!r} reported an error: {detail}")
        return "\n".join(parts) if parts else str(result)

    async def close(self) -> None:
        for session in self._sessions.values():
            await session.stop()
        self._sessions.clear()
