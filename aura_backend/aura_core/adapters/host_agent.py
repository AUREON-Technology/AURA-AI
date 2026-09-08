"""Desktop host-agent bridge: routes the engineer agent's sandbox tools to the caller's own
machine instead of a server-side container.

``api/host_agent.py`` holds one live :class:`HostAgentSession` per connected desktop daemon
(``aura_hostd``) in a :class:`HostAgentRegistry`; :class:`HostAgentSandboxRunner` implements the
same :class:`~aura_core.ports.coding.SandboxRunner` protocol
:class:`~aura_core.adapters.docker_sandbox.DockerSandboxRunner` does, but every call is a framed
RPC to the caller's own session instead of a local Docker call. :class:`CompositeSandboxRunner`
is what actually gets wired into ``container.sandbox_runner``: it prefers a connected desktop
session for the calling user and falls back to the server-side sandbox (or refuses) when none is
connected -- see ``Container._build_sandbox_runner``.

None of this weakens ADR-006. Server-side ``ToolPolicy`` evaluates exactly as it does today
before any of this is reached; what changes is only *where* an already-authorized
``run_command``/``write_file`` actually executes. Containment of "which paths and commands are
actually allowed" is enforced by the daemon itself, never trusted from here -- this module has no
opinion on that; it is a transport, not a policy.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..domain.identifiers import UserId
from ..observability.logging import get_logger
from ..ports.coding import SandboxHandle, SandboxResult
from .docker_sandbox import SandboxError

__all__ = ["HostAgentSession", "HostAgentRegistry", "HostAgentSandboxRunner", "CompositeSandboxRunner"]

logger = get_logger("adapters.host_agent")

#: How long a single RPC to a desktop daemon may take before this side gives up and reports a
#: timeout to the tool caller. Generous relative to the analogous sandbox `timeout_s` values in
#: `tools/coding.py` -- a real machine can be slower than a warm container, and the daemon itself
#: enforces the tighter per-command budget the tool call actually asked for.
_DEFAULT_CALL_TIMEOUT_S = 60.0


class HostAgentSession:
    """One live connection to a single user's ``aura_hostd`` daemon.

    Owns request/response correlation for the duplex RPC channel `api/host_agent.py` runs over
    the websocket: :meth:`call` sends a framed request and awaits the matching response;
    :meth:`resolve` is fed every inbound ``result``/``error`` frame by the route's receive loop
    and wakes whichever :meth:`call` is waiting on that id.
    """

    def __init__(self, user_id: UserId, send: Callable[[Mapping[str, Any]], Awaitable[None]]) -> None:
        self.user_id = user_id
        self._send = send
        self._pending: dict[str, "asyncio.Future[Any]"] = {}

    async def call(self, method: str, params: Mapping[str, Any], *, timeout_s: float) -> Any:
        call_id = uuid.uuid4().hex
        future: "asyncio.Future[Any]" = asyncio.get_event_loop().create_future()
        self._pending[call_id] = future
        try:
            await self._send({"type": "call", "id": call_id, "method": method, "params": dict(params)})
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise SandboxError(
                f"desktop app did not respond to {method!r} within {timeout_s:.0f}s"
            ) from exc
        finally:
            self._pending.pop(call_id, None)

    def resolve(self, frame: Mapping[str, Any]) -> None:
        """Feed one inbound `result`/`error` frame. Unknown or already-resolved ids are ignored --
        a slow response to a call this side already timed out on is not an error, just late."""
        call_id = str(frame.get("id") or "")
        future = self._pending.get(call_id)
        if future is None or future.done():
            return
        if frame.get("type") == "error":
            future.set_exception(SandboxError(str(frame.get("message") or "host agent error")))
        else:
            future.set_result(frame.get("result"))

    def fail_all_pending(self, reason: str) -> None:
        """Called when the connection drops -- every in-flight call must be unblocked rather than
        hanging until its own timeout, which would otherwise stall a tool call for up to
        `_DEFAULT_CALL_TIMEOUT_S` after the user already closed the desktop app."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(SandboxError(reason))


class HostAgentRegistry:
    """Live host-agent sessions, keyed by user. One connection per user at a time -- a second
    desktop pairing the same account replaces the first (matching the intent of "your own
    machine", not a pool of workers)."""

    def __init__(self) -> None:
        self._sessions: dict[str, HostAgentSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, session: HostAgentSession) -> None:
        async with self._lock:
            previous = self._sessions.get(session.user_id.value)
            self._sessions[session.user_id.value] = session
        if previous is not None and previous is not session:
            previous.fail_all_pending("replaced by a new desktop connection")

    async def unregister(self, session: HostAgentSession) -> None:
        async with self._lock:
            current = self._sessions.get(session.user_id.value)
            if current is session:
                del self._sessions[session.user_id.value]
        session.fail_all_pending("desktop app disconnected")

    async def resolve(self, user_id: UserId) -> HostAgentSession | None:
        async with self._lock:
            return self._sessions.get(user_id.value)


class HostAgentSandboxRunner:
    """`SandboxRunner` over a live per-user `aura_hostd` connection.

    Unlike `DockerSandboxRunner`'s per-turn container, a host task is scoped to the workspace the
    user granted on their own machine, not to one chat turn -- so `destroy` here is deliberately a
    no-op. The user's desktop app owns that lifecycle (closing a workspace), not the kernel.
    """

    def __init__(self, registry: HostAgentRegistry, *, call_timeout_s: float = _DEFAULT_CALL_TIMEOUT_S) -> None:
        self._registry = registry
        self._call_timeout_s = call_timeout_s
        self._handle_owners: dict[str, UserId] = {}

    async def has_session(self, user_id: UserId) -> bool:
        return await self._registry.resolve(user_id) is not None

    async def create_task(self, *, user_id: UserId | None = None) -> SandboxHandle:
        if user_id is None:
            raise SandboxError("the host-agent sandbox requires a caller identity")
        session = await self._registry.resolve(user_id)
        if session is None:
            raise SandboxError(
                "no desktop app is connected for this account -- open AURA Desktop and pair it"
            )
        result = await session.call("create_task", {}, timeout_s=self._call_timeout_s)
        task_id = str((result or {}).get("task_id") or uuid.uuid4().hex)
        self._handle_owners[task_id] = user_id
        return SandboxHandle(task_id=task_id)

    async def _session_for(self, handle: SandboxHandle) -> HostAgentSession:
        user_id = self._handle_owners.get(handle.task_id)
        if user_id is None:
            raise SandboxError(f"unknown host-agent task {handle.task_id!r}")
        session = await self._registry.resolve(user_id)
        if session is None:
            raise SandboxError("the desktop app disconnected -- reconnect and try again")
        return session

    async def run(self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float) -> SandboxResult:
        session = await self._session_for(handle)
        result = await session.call(
            "run",
            {"task_id": handle.task_id, "command": list(command), "timeout_s": timeout_s},
            # A little slack over the tool's own timeout so a daemon-side timeout response
            # (which the daemon is expected to enforce itself) has time to arrive before this
            # side gives up first and reports a less specific transport timeout instead.
            timeout_s=timeout_s + 5.0,
        )
        payload = result or {}
        return SandboxResult(
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            exit_code=int(payload.get("exit_code", -1)),
        )

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        session = await self._session_for(handle)
        await session.call(
            "write_file",
            {"task_id": handle.task_id, "path": path, "content": content},
            timeout_s=self._call_timeout_s,
        )

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        session = await self._session_for(handle)
        result = await session.call(
            "read_file",
            {"task_id": handle.task_id, "path": path},
            timeout_s=self._call_timeout_s,
        )
        return str((result or {}).get("content", ""))

    async def destroy(self, handle: SandboxHandle) -> None:
        # Workspace-scoped on the daemon side -- see class docstring. Just forget the mapping so
        # a reused task_id (unlikely, but not impossible across daemon restarts) can't be routed
        # to a stale owner.
        self._handle_owners.pop(handle.task_id, None)


class CompositeSandboxRunner:
    """Prefers the caller's connected desktop session; falls back to a server-side sandbox (or
    refuses) when none is connected.

    The choice is made once, at `create_task`, and fixed for that handle's lifetime -- a
    `SandboxHandle` is "opaque identity of one ephemeral per-task sandbox" (ports/coding.py); it
    does not migrate between runners mid-task.
    """

    def __init__(self, *, host_runner: HostAgentSandboxRunner, fallback: Any | None) -> None:
        self._host = host_runner
        self._fallback = fallback
        self._handle_runner: dict[str, Any] = {}

    async def create_task(self, *, user_id: UserId | None = None) -> SandboxHandle:
        if user_id is not None and await self._host.has_session(user_id):
            handle = await self._host.create_task(user_id=user_id)
            self._handle_runner[handle.task_id] = self._host
            return handle
        if self._fallback is None:
            raise SandboxError(
                "no desktop app is connected and no server-side sandbox is configured -- "
                "pair AURA Desktop, or enable sandbox.enabled for the shared sandbox"
            )
        handle = await self._fallback.create_task()
        self._handle_runner[handle.task_id] = self._fallback
        return handle

    def _runner_for(self, handle: SandboxHandle) -> Any:
        runner = self._handle_runner.get(handle.task_id)
        if runner is None:
            raise SandboxError(f"unknown sandbox task {handle.task_id!r}")
        return runner

    async def run(self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float) -> SandboxResult:
        return await self._runner_for(handle).run(handle, command, timeout_s=timeout_s)

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        await self._runner_for(handle).write_file(handle, path, content)

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        return await self._runner_for(handle).read_file(handle, path)

    async def destroy(self, handle: SandboxHandle) -> None:
        runner = self._handle_runner.pop(handle.task_id, None)
        if runner is not None:
            await runner.destroy(handle)
