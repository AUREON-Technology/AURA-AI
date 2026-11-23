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
import re
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Sequence

from ..domain.identifiers import UserId
from ..observability.logging import get_logger
from ..ports.coding import (
    TOOL_CALL_CONFIRMED,
    FileWriteOp,
    SandboxHandle,
    SandboxOutputEvent,
    SandboxResult,
    StreamingSandboxRunner,
)
from .docker_sandbox import SandboxError

#: What may name an isolated task's worktree directory on the user's machine: one plain path
#: segment. The daemon checks the same shape (aura_hostd task_handler.dart) and contains the
#: resolved path; this side refuses first so a free-form chat id never reaches it (G-04).
_TASK_KEY = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _exit_code(payload: Any) -> int:
    """The command's exit status from a daemon payload, ``-1`` when it is absent or malformed.

    ``int(payload.get("exit_code", -1))`` raised ``TypeError`` for an explicit ``null`` (and
    ``AttributeError`` for a payload that was not an object), turning a finished command into an
    opaque tool failure that hid its output.
    """
    value = payload.get("exit_code") if isinstance(payload, Mapping) else None
    try:
        return int(value) if value is not None and not isinstance(value, bool) else -1
    except (TypeError, ValueError):
        return -1


def _with_confirmation(params: dict[str, Any]) -> dict[str, Any]:
    """Add ``confirmed: true`` when the person approved this call (WP8, D-10c). Sent only when
    true, so a daemon that predates the field sees exactly the frame it always did. The daemon
    ignores it for a desktop task -- it never lets this side waive the desk's own consent."""
    if TOOL_CALL_CONFIRMED.get():
        params["confirmed"] = True
    return params

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

    async def _cancel(self, call_id: str) -> None:
        """Tell the daemon this side has given up on ``call_id`` (audit FSA-041). The daemon
        marks the call's pending approval cancelled, so a human answering later executes
        nothing. Best effort: the socket may already be gone."""
        try:
            await self._send({"type": "cancel", "id": call_id})
        except Exception:  # noqa: BLE001 -- nothing above can act on a failed courtesy frame
            logger.debug("could not send cancel frame", extra={"call_id": call_id})

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
            # No answer arrived -- this side timed out, or the tool registry's own timeout
            # cancelled us -- so the daemon must stop waiting for its human too (FSA-041).
            if future.cancelled() or not future.done():
                await self._cancel(call_id)

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
        finished = False
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
                    finished = True
                    raise SandboxError("desktop app disconnected mid-stream")
                ftype = frame.get("type")
                if ftype == "error":
                    finished = True
                    raise SandboxError(str(frame.get("message") or "host agent error"))
                if ftype != "stream":
                    finished = True
                yield frame
                if ftype != "stream":
                    return
        finally:
            self._streams.pop(call_id, None)
            if not finished:
                # Idle timeout, cancellation by the tool registry, or the consumer stopped
                # reading: the daemon must not keep waiting on (or run) an approval nobody is
                # listening for (FSA-041).
                await self._cancel(call_id)

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
        if frame.get("type") == "stream":
            # The daemon streams output for `run` whether or not this side asked for it
            # (aura_hostd/lib/src/rpc_client.dart), then sends the aggregated output in its
            # `result`. A single-shot call waits for that `result`: settling on the first
            # `stream` frame instead resolved the call with that frame's (absent) `result` --
            # empty stdout and exit -1 for every command that printed anything.
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
    machine", not a pool of workers).

    Sessions are held per process: the daemon's socket lives in exactly one gateway worker. With
    a ``relay`` (``adapters/host_agent_relay.py``), :meth:`resolve` also finds a session another
    worker holds and returns a proxy for it, so a deployment can run several workers (ADR-011,
    "Deployment constraint"). Without one, behaviour is exactly the single-process registry: only
    the worker holding the socket can see it.

    The relay never makes a local session unreachable. If the bus errors, resolution falls back
    to this process's own sessions.
    """

    def __init__(self, *, relay: Any = None) -> None:
        self._sessions: dict[str, HostAgentSession] = {}
        self._lock = asyncio.Lock()
        self._relay = relay
        if relay is not None:
            relay.bind(local_session=self._local, superseded=self._drop_superseded)

    async def _local(self, user_id: UserId) -> HostAgentSession | None:
        async with self._lock:
            return self._sessions.get(user_id.value)

    async def _drop_superseded(self, user_id: UserId) -> None:
        """Another worker now holds this user's session: drop ours, failing its in-flight calls
        the same way an in-process replacement does."""
        async with self._lock:
            previous = self._sessions.pop(user_id.value, None)
        if previous is not None:
            previous.fail_all_pending("replaced by a new desktop connection")

    async def register(self, session: HostAgentSession) -> None:
        async with self._lock:
            previous = self._sessions.get(session.user_id.value)
            self._sessions[session.user_id.value] = session
        if previous is not None and previous is not session:
            previous.fail_all_pending("replaced by a new desktop connection")
        if self._relay is not None:
            try:
                await self._relay.claim(session.user_id)
            except Exception:
                # Still reachable from this worker; other workers just cannot see it until the
                # heartbeat's refresh succeeds.
                logger.warning("host-agent relay claim failed; session is local-only", exc_info=True)

    async def unregister(self, session: HostAgentSession) -> None:
        async with self._lock:
            current = self._sessions.get(session.user_id.value)
            was_current = current is session
            if was_current:
                del self._sessions[session.user_id.value]
        session.fail_all_pending("desktop app disconnected")
        if was_current and self._relay is not None:
            await self._relay.release(session.user_id)

    async def resolve(self, user_id: UserId) -> Any:
        """This worker's session for ``user_id``, a relay proxy to another worker's, or ``None``.

        Returns an object with ``user_id``, ``call`` and ``call_streaming`` either way."""
        local = await self._local(user_id)
        if self._relay is None:
            return local
        try:
            owner = await self._relay.owner(user_id)
        except Exception:
            logger.warning("host-agent relay lookup failed; resolving locally", exc_info=True)
            return local
        if owner is None or owner == self._relay.worker_id:
            return local
        # Another worker holds the newest connection. A local session here is a stale one the
        # superseded notice has not reached yet; the owner is authoritative.
        return self._relay.remote_session(user_id, owner)

    @property
    def relay(self) -> Any:
        """The cross-worker relay, or ``None`` when this registry is single-process."""
        return self._relay


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
        workspace_root: str | None = None,
        origin: str | None = None,
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
            if not _TASK_KEY.fullmatch(session_id) or session_id in (".", ".."):
                raise SandboxError(
                    "this chat's session id cannot name an isolated worktree on the desktop; "
                    "start a new chat"
                )
            params["isolate"] = True
            params["task_key"] = session_id
        # FSA-006: the IDE's active root binds the task on the daemon (`task_handler.dart::
        # createTask`), so every RPC on this handle resolves against it. A key only when the
        # caller named one: the daemon's fallback for an absent key is "the first granted root",
        # and an explicit `null` is a wire shape no daemon version has been exercised against.
        # The daemon resolves and contains the path; this side never interprets it.
        if workspace_root:
            params["cwd"] = workspace_root
        # WP8: the daemon refuses a remote task unless remote control is on at the computer, and
        # runs its writes and commands only when this side says the person approved them.
        if origin in ("desktop", "remote"):
            params["origin"] = origin
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
            _with_confirmation(
                {"task_id": handle.task_id, "command": list(command), "timeout_s": timeout_s}
            ),
            # A little slack over the tool's own timeout so a daemon-side timeout response
            # (which the daemon is expected to enforce itself) has time to arrive before this
            # side gives up first and reports a less specific transport timeout instead. Also
            # floored at 330s: an untrusted-workspace command can queue for D5 interactive
            # approval on the daemon side (`approval_queue.dart`'s own 5-minute timeout), which
            # can take as long as a human takes to respond -- not just network latency. Sized the
            # same way `write_file`/`write_files` above are.
            timeout_s=max(timeout_s + 5.0, 330.0),
        )
        payload = result if isinstance(result, Mapping) else {}
        return SandboxResult(
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
            exit_code=_exit_code(payload),
        )

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        session = await self._session_for(handle)
        await session.call(
            "write_file",
            _with_confirmation({"task_id": handle.task_id, "path": path, "content": content}),
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
        content = result.get("content") if isinstance(result, Mapping) else None
        return "" if content is None else str(content)

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
            _with_confirmation({"task_id": handle.task_id, "operations": payload}),
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
            _with_confirmation(
                {"task_id": handle.task_id, "command": list(command), "timeout_s": timeout_s}
            ),
            # Idle timeout between frames, not an overall deadline -- see call_streaming's
            # docstring. The daemon is expected to send at least one frame (even just the
            # terminal result) well within this window once it has started the command -- except
            # while queued for D5 interactive approval (`approval_queue.dart`), where the daemon
            # sends zero frames for as long as the human takes to respond, up to its own 5-minute
            # approval timeout. `run_command` (`tools/coding.py`) prefers this streaming path
            # whenever the runner supports it, which `CompositeSandboxRunner` always does
            # structurally, so this is the path D5 approval actually goes through in practice --
            # floored the same way `run`'s non-streaming timeout above is.
            timeout_s=max(timeout_s + 5.0, 330.0),
        ):
            if frame.get("type") == "stream":
                yield SandboxOutputEvent(
                    kind=str(frame.get("stream") or "stdout"), data=str(frame.get("data") or "")
                )
            else:
                yield SandboxOutputEvent(kind="done", exit_code=_exit_code(frame.get("result")))

    async def destroy(self, handle: SandboxHandle) -> None:
        # Workspace-scoped on the daemon side -- see class docstring. Just forget the mapping so
        # a reused task_id (unlikely, but not impossible across daemon restarts) can't be routed
        # to a stale owner.
        self._handle_owners.pop(handle.task_id, None)

    async def workspace_path(self, handle: SandboxHandle) -> str:
        # A host-agent task runs on the user's own desktop machine, not this server -- there is no
        # server-side filesystem path for it to report. `git_push`/`clone_repository`
        # (`tools/coding.py`) only ever call this against a durable `WorkspaceManager` handle,
        # which `CompositeSandboxRunner._runner_for` always routes to the Docker fallback (see its
        # docstring), so this runner should never actually be asked -- this exists so the class
        # fails loudly with an actionable message instead of an `AttributeError` if that invariant
        # is ever violated.
        raise SandboxError(
            "this task runs on the user's own desktop machine, not a server-side workspace -- "
            "host-agent tasks have no workspace_path; host-side git operations need a durable "
            "server-side workspace (open_workspace)"
        )


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
        workspace_root: str | None = None,
        origin: str | None = None,
    ) -> SandboxHandle:
        if user_id is not None and await self._host.has_session(user_id):
            # `workspace_root` (FSA-006) is a path on the user's machine: meaningful to the paired
            # daemon, never to the server-side fallback below, which does not get it.
            extra: dict[str, Any] = {"workspace_root": workspace_root} if workspace_root else {}
            if origin:
                extra["origin"] = origin
            handle = await self._host.create_task(
                user_id=user_id, session_id=session_id, isolate=isolate, **extra
            )
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

    async def read_file_bytes(self, handle: SandboxHandle, path: str) -> bytes:
        """A file's exact bytes, when the runner serving this handle can give them.

        The server-side sandbox can; a paired desktop's wire protocol returns text only, so for
        that handle this raises and a caller (``cat file.pdf`` in ``run_command``) falls back to
        running the real command.
        """
        reader = getattr(self._runner_for(handle), "read_file_bytes", None)
        if reader is None:
            raise SandboxError("this sandbox cannot return a file's raw bytes")
        return await reader(handle, path)

    async def workspace_path(self, handle: SandboxHandle) -> str:
        return await self._runner_for(handle).workspace_path(handle)

    async def write_files(self, handle: SandboxHandle, operations: Sequence[FileWriteOp]) -> None:
        await self._runner_for(handle).write_files(handle, operations)

    async def destroy(self, handle: SandboxHandle) -> None:
        runner = self._handle_runner.pop(handle.task_id, None)
        if runner is not None:
            await runner.destroy(handle)
