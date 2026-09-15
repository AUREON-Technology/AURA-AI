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
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Sequence

from ..domain.identifiers import UserId
from ..observability.logging import get_logger
from ..ports.coding import FileWriteOp, SandboxHandle, SandboxOutputEvent, SandboxResult, StreamingSandboxRunner
from .docker_sandbox import SandboxError

__all__ = ["HostAgentSession", "HostAgentRegistry", "HostAgentSandboxRunner", "CompositeSandboxRunner"]

logger = get_logger("adapters.host_agent")

#: How long a single RPC to a desktop daemon may take before this side gives up and reports a
#: timeout to the tool caller. Generous relative to the analogous sandbox `timeout_s` values in
#: `tools/coding.py` -- a real machine can be slower than a warm container, and the daemon itself
#: enforces the tighter per-command budget the tool call actually asked for.
_DEFAULT_CALL_TIMEOUT_S = 60.0

#: Sentinel pushed onto every open streaming call's queue when the connection drops, so
#: `call_streaming` can distinguish "the daemon disconnected mid-stream" from an ordinary frame
#: that happens to carry no recognizable `type`.
_STREAM_DISCONNECTED = object()


class HostAgentSession:
    """One live connection to a single user's ``aura_hostd`` daemon.

    Owns request/response correlation for the duplex RPC channel `api/host_agent.py` runs over
    the websocket. Two calling conventions share the same wire shape:

    * :meth:`call` sends a framed request and awaits exactly one ``result``/``error`` frame back
      -- used by `create_task`/`write_file`/`read_file`, which only ever have one outcome.
    * :meth:`call_streaming` sends the same kind of request but yields every ``stream`` frame the
      daemon sends for that call *as it arrives*, then yields the terminal ``result`` frame and
      stops (or raises on ``error``) -- used by `run_streaming` (`tools/coding.py::run_command`),
      where the whole point is not waiting for the command to finish before seeing anything.

    :meth:`resolve` is fed every inbound frame by the route's receive loop and routes each one to
    whichever of the two bookkeeping structures (`_pending`/`_streams`) is waiting on that id.
    """

    def __init__(self, user_id: UserId, send: Callable[[Mapping[str, Any]], Awaitable[None]]) -> None:
        self.user_id = user_id
        self._send = send
        self._pending: dict[str, "asyncio.Future[Any]"] = {}
        self._streams: dict[str, "asyncio.Queue[Any]"] = {}

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

    async def call_streaming(
        self, method: str, params: Mapping[str, Any], *, timeout_s: float
    ) -> AsyncIterator[Mapping[str, Any]]:
        """Like :meth:`call`, but yields every ``stream`` frame for this call as it arrives, then
        the terminal ``result`` frame, and stops. Raises :class:`SandboxError` on an ``error``
        frame or if no frame at all (of any kind) arrives within ``timeout_s`` of the last one.

        That last point matters: the timeout here is *idle* time between frames, not a fixed
        overall deadline. A long-running command that is actively streaming output is making
        progress by definition -- the daemon's own per-command timeout (`timeout_s` in the
        `params` sent) is what actually bounds how long the command may run; this side only needs
        to give up if the daemon itself stops talking.
        """
        call_id = uuid.uuid4().hex
        queue: "asyncio.Queue[Any]" = asyncio.Queue()
        self._streams[call_id] = queue
        try:
            await self._send({"type": "call", "id": call_id, "method": method, "params": dict(params)})
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=timeout_s)
                except asyncio.TimeoutError as exc:
                    raise SandboxError(
                        f"desktop app did not respond to {method!r} within {timeout_s:.0f}s"
                    ) from exc
                if frame is _STREAM_DISCONNECTED:
                    raise SandboxError("desktop app disconnected mid-stream")
                ftype = frame.get("type")
                if ftype == "error":
                    raise SandboxError(str(frame.get("message") or "host agent error"))
                yield frame
                if ftype != "stream":
                    return
        finally:
            self._streams.pop(call_id, None)

    def resolve(self, frame: Mapping[str, Any]) -> None:
        """Feed one inbound `stream`/`result`/`error` frame. Unknown or already-resolved ids are
        ignored -- a slow response to a call this side already gave up on is not an error, just
        late."""
        call_id = str(frame.get("id") or "")
        queue = self._streams.get(call_id)
        if queue is not None:
            queue.put_nowait(dict(frame))
            return
        future = self._pending.get(call_id)
        if future is None or future.done():
            return
        if frame.get("type") == "error":
            future.set_exception(SandboxError(str(frame.get("message") or "host agent error")))
        else:
            future.set_result(frame.get("result"))

    def fail_all_pending(self, reason: str) -> None:
        """Called when the connection drops -- every in-flight call (single-shot or streaming)
        must be unblocked rather than hanging until its own timeout, which would otherwise stall a
        tool call for up to `_DEFAULT_CALL_TIMEOUT_S` after the user already closed the desktop
        app."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(SandboxError(reason))
        for queue in self._streams.values():
            queue.put_nowait(_STREAM_DISCONNECTED)


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

    async def create_task(
        self,
        *,
        user_id: UserId | None = None,
        session_id: str | None = None,
        isolate: bool = False,
    ) -> SandboxHandle:
        if user_id is None:
            raise SandboxError("the host-agent sandbox requires a caller identity")
        session = await self._registry.resolve(user_id)
        if session is None:
            raise SandboxError(
                "no desktop app is connected for this account -- open AURA Desktop and pair it"
            )
        # D8: `isolate`/`task_key` ask the daemon (`task_handler.dart::createTask`) to carve out a
        # dedicated git worktree for this task rather than writing into the user's live checkout.
        # `task_key` is `session_id`, not the `task_id` this call mints below -- the backend mints
        # a fresh `task_id` every turn (`_SandboxSessionCache` keys by `correlation_id`, which is
        # also fresh every turn), so only `session_id` (stable across a chat session's many turns)
        # lets the daemon recognize "this is the same task, reuse the worktree" on turn 2+.
        params: dict[str, Any] = {}
        if isolate and session_id:
            params["isolate"] = True
            params["task_key"] = session_id
        result = await session.call("create_task", params, timeout_s=self._call_timeout_s)
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
            # D6: an untrusted-workspace write now queues for interactive approval on the daemon
            # side (`task_handler.dart`'s gating retrofit), which can take as long as a human
            # takes to respond -- up to the daemon's own 5-minute approval timeout -- not just
            # network latency. Sized the same way `write_files` below is, so a slow human doesn't
            # make this side time out and report failure while the daemon is still legitimately
            # waiting for an answer.
            timeout_s=max(self._call_timeout_s, 330.0),
        )

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        session = await self._session_for(handle)
        result = await session.call(
            "read_file",
            {"task_id": handle.task_id, "path": path},
            timeout_s=self._call_timeout_s,
        )
        return str((result or {}).get("content", ""))

    async def write_files(self, handle: SandboxHandle, operations: Sequence[FileWriteOp]) -> None:
        """D6: a single `write_files` RPC to the daemon (`aura_hostd/lib/src/task_handler.dart`),
        which does the pre-validate-all-then-write-all pass itself -- see the port's own docstring
        for why this must be one RPC rather than a loop of single-file calls over this transport.
        Also where an untrusted-workspace write queues for interactive approval on the daemon
        side, so this call may take as long as a human takes to respond (bounded by the daemon's
        own approval timeout), not just network latency -- hence the generous call timeout below,
        matching `run`'s own `timeout_s + 5.0` margin discipline but sized for a human, not a
        command.
        """
        session = await self._session_for(handle)
        payload = []
        for op in operations:
            entry: dict[str, Any] = {"path": op.path}
            if op.content is not None:
                entry["content"] = op.content
            else:
                entry["find"] = op.find
                entry["replace"] = op.replace
            payload.append(entry)
        await session.call(
            "write_files",
            {"task_id": handle.task_id, "operations": payload},
            timeout_s=max(self._call_timeout_s, 330.0),  # daemon-side approval can wait up to 5m
        )

    async def run_streaming(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> AsyncIterator[SandboxOutputEvent]:
        """`StreamingSandboxRunner` capability -- see that Protocol's docstring. The wire shape a
        `stream` frame carries is ``{"type": "stream", "id": ..., "stream": "stdout"|"stderr",
        "data": "..."}``; the terminal ``result`` frame's ``result`` payload carries
        ``exit_code`` exactly like the non-streaming `run` RPC's does.
        """
        session = await self._session_for(handle)
        async for frame in session.call_streaming(
            "run",
            {"task_id": handle.task_id, "command": list(command), "timeout_s": timeout_s},
            # Idle timeout between frames, not an overall deadline -- see call_streaming's
            # docstring. The daemon is expected to send at least one frame (even just the
            # terminal result) well within this window once it has started the command.
            timeout_s=timeout_s + 5.0,
        ):
            if frame.get("type") == "stream":
                yield SandboxOutputEvent(
                    kind=str(frame.get("stream") or "stdout"), data=str(frame.get("data") or "")
                )
            else:
                payload = frame.get("result") or {}
                yield SandboxOutputEvent(kind="done", exit_code=int(payload.get("exit_code", -1)))

    async def destroy(self, handle: SandboxHandle) -> None:
        # Workspace-scoped on the daemon side -- see class docstring. Just forget the mapping so
        # a reused task_id (unlikely, but not impossible across daemon restarts) can't be routed
        # to a stale owner.
        self._handle_owners.pop(handle.task_id, None)


class CompositeSandboxRunner:
    """Prefers the caller's connected desktop session; falls back to a server-side sandbox (or
    refuses) when none is connected.

    For a `create_task` handle, the choice is made once and fixed for that handle's lifetime -- a
    `SandboxHandle` is "opaque identity of one ephemeral per-task sandbox" (ports/coding.py); it
    does not migrate between runners mid-task. A *durable* workspace handle (`tools/coding.py`'s
    `open_workspace`/`run_command`/etc. against a repo, via `WorkspaceManager.open()`) never goes
    through `create_task` at all -- see `_runner_for`.
    """

    def __init__(self, *, host_runner: HostAgentSandboxRunner, fallback: Any | None) -> None:
        self._host = host_runner
        self._fallback = fallback
        self._handle_runner: dict[str, Any] = {}

    async def create_task(
        self,
        *,
        user_id: UserId | None = None,
        session_id: str | None = None,
        isolate: bool = False,
    ) -> SandboxHandle:
        if user_id is not None and await self._host.has_session(user_id):
            handle = await self._host.create_task(user_id=user_id, session_id=session_id, isolate=isolate)
            self._handle_runner[handle.task_id] = self._host
            return handle
        if self._fallback is None:
            raise SandboxError(
                "no desktop app is connected and no server-side sandbox is configured -- "
                "pair AURA Desktop, or enable sandbox.enabled for the shared sandbox"
            )
        # `isolate` is a Desktop-host-agent-only capability (D8) -- the Docker fallback has its
        # own, unrelated durable-workspace story, so it isn't threaded through here.
        handle = await self._fallback.create_task()
        self._handle_runner[handle.task_id] = self._fallback
        return handle

    def _runner_for(self, handle: SandboxHandle) -> Any:
        runner = self._handle_runner.get(handle.task_id)
        if runner is not None:
            return runner
        # Never registered by `create_task` -- the only other source of a `SandboxHandle` is
        # `WorkspaceManager.open()` (tools/workspaces.py), whose durable per-repo containers are,
        # by `Container._build_workspace_manager`'s own invariant, always backed by exactly
        # `self._fallback` (the Docker runner is the one that can pause/reap them; see that
        # method's docstring). Production incident: `open_workspace`'s tool body opens the
        # workspace through the manager and then calls `sandbox_runner.run(...)` on *this*
        # composite to list its contents -- before this fallback, that unconditionally raised
        # "unknown sandbox task", because only `create_task` ever populated `_handle_runner`. The
        # Docker container was created successfully every time; every durable-workspace tool call
        # still failed 100% of the time, the moment it tried to do anything past opening the
        # workspace -- confirmed live (open_workspace: 0 "ok", 8 "error" outcomes) even though the
        # container it opened was running and healthy the whole time. Caching the fallback here
        # matches `create_task`'s own registration behaviour for later calls on the same handle.
        if self._fallback is not None:
            self._handle_runner[handle.task_id] = self._fallback
            return self._fallback
        raise SandboxError(f"unknown sandbox task {handle.task_id!r}")

    async def run(self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float) -> SandboxResult:
        return await self._runner_for(handle).run(handle, command, timeout_s=timeout_s)

    async def run_streaming(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> AsyncIterator[SandboxOutputEvent]:
        """Always present structurally (so `isinstance(composite, StreamingSandboxRunner)` is
        always true for a `CompositeSandboxRunner`), but the routing decision is per-handle, not
        per-object: whichever runner `create_task` actually chose for this handle may or may not
        itself support streaming. When it does not (the Docker sandbox, the in-memory fake), this
        replays the ordinary `run` result as a single stdout/stderr chunk followed by `done` --
        callers that always use the streaming interface (`tools/coding.py::run_command`) do not
        need two code paths, they just see one chunk instead of many.
        """
        runner = self._runner_for(handle)
        if isinstance(runner, StreamingSandboxRunner):
            async for event in runner.run_streaming(handle, command, timeout_s=timeout_s):
                yield event
            return
        result = await runner.run(handle, command, timeout_s=timeout_s)
        if result.stdout:
            yield SandboxOutputEvent(kind="stdout", data=result.stdout)
        if result.stderr:
            yield SandboxOutputEvent(kind="stderr", data=result.stderr)
        yield SandboxOutputEvent(kind="done", exit_code=result.exit_code)

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        await self._runner_for(handle).write_file(handle, path, content)

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        return await self._runner_for(handle).read_file(handle, path)

    async def write_files(self, handle: SandboxHandle, operations: Sequence[FileWriteOp]) -> None:
        await self._runner_for(handle).write_files(handle, operations)

    async def destroy(self, handle: SandboxHandle) -> None:
        runner = self._handle_runner.pop(handle.task_id, None)
        if runner is not None:
            await runner.destroy(handle)
