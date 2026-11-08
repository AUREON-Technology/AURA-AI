"""Desktop host-agent bridge: `HostAgentSession`/`HostAgentRegistry`/`HostAgentSandboxRunner`/
`CompositeSandboxRunner` (`adapters/host_agent.py`) and the `/api/v3/host/agent` WebSocket route
(`api/host_agent.py`).

Mirrors `test_voice_live_endpoint.py`'s idiom for the route tests (a stub `authenticator`, a real
`TestClient` websocket) and `test_engineer_tools.py`'s (real fakes, not mocks) for the adapter
tests -- `HostAgentSession`/`HostAgentRegistry` are exercised for real, just with an in-process
`send` callable standing in for the websocket.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from aura_core.adapters.docker_sandbox import SandboxError
from aura_core.adapters.host_agent import (
    CompositeSandboxRunner,
    HostAgentRegistry,
    HostAgentSandboxRunner,
    HostAgentSession,
)
from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import UserId
from aura_core.ports.coding import SandboxHandle, SandboxOutputEvent


def _app(container, *, engineer_users: frozenset[str] = frozenset({"engineer-1"})):
    async def authenticator(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            from aura_core.domain.errors import AuthenticationError

            raise AuthenticationError("missing")
        token = header[7:]
        if token == "bad":
            from aura_core.domain.errors import AuthenticationError

            raise AuthenticationError("bad")
        capabilities = ("engineer_agent",) if token in engineer_users else ()
        return Principal(user_id=UserId(token), email="", role="User", capabilities=capabilities)

    return build_app(container, authenticator=authenticator)


class TestHostAgentRoute:
    @pytest.mark.anyio
    async def test_auth_required(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        with TestClient(_app(container)) as client:
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/api/v3/host/agent") as ws:
                    ws.send_json({"type": "auth", "token": "bad"})
                    ws.receive_json()
            assert exc.value.code in (4401, 1008)

    @pytest.mark.anyio
    async def test_without_engineer_capability_is_refused(self, container) -> None:
        """A daemon paired to an account with no `engineer_agent` grant must not be registered --
        nothing would ever route a call to it, so accepting the connection would just be a
        session that silently does nothing."""
        from fastapi.testclient import TestClient

        await container.startup()
        with TestClient(_app(container, engineer_users=frozenset())) as client:
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/api/v3/host/agent") as ws:
                    ws.send_json({"type": "auth", "token": "engineer-1"})
                    ws.receive_json()
            assert exc.value.code == 4401

    @pytest.mark.anyio
    async def test_connecting_registers_and_disconnecting_unregisters(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        registry = container.host_agent_registry
        with TestClient(_app(container)) as client:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "engineer-1"})
                # Give the route's accept loop a moment to register before we assert on it --
                # the client-side context manager returns as soon as the handshake completes,
                # not after the server has run its first receive.
                for _ in range(50):
                    if await registry.resolve(UserId("engineer-1")) is not None:
                        break
                    await asyncio.sleep(0.01)
                session = await registry.resolve(UserId("engineer-1"))
                assert session is not None
                assert session.user_id == UserId("engineer-1")

        for _ in range(50):
            if await registry.resolve(UserId("engineer-1")) is None:
                break
            await asyncio.sleep(0.01)
        assert await registry.resolve(UserId("engineer-1")) is None


class TestHostAgentSession:
    @pytest.mark.anyio
    async def test_call_resolves_on_matching_result_frame(self) -> None:
        sent = []

        async def send(frame):
            sent.append(frame)

        session = HostAgentSession(UserId("u1"), send)
        call_task = asyncio.ensure_future(session.call("run", {"command": ["echo"]}, timeout_s=5))
        await asyncio.sleep(0)  # let the call register itself and send
        assert sent and sent[0]["method"] == "run"
        session.resolve({"type": "result", "id": sent[0]["id"], "result": {"exit_code": 0}})
        result = await call_task
        assert result == {"exit_code": 0}

    @pytest.mark.anyio
    async def test_error_frame_raises_sandbox_error(self) -> None:
        async def send(frame):
            session.resolve({"type": "error", "id": frame["id"], "message": "boom"})

        session = HostAgentSession(UserId("u1"), send)
        with pytest.raises(SandboxError, match="boom"):
            await session.call("run", {}, timeout_s=5)

    @pytest.mark.anyio
    async def test_call_times_out_if_never_answered(self) -> None:
        async def send(frame):
            return None

        session = HostAgentSession(UserId("u1"), send)
        with pytest.raises(SandboxError, match="did not respond"):
            await session.call("run", {}, timeout_s=0.05)

    @pytest.mark.anyio
    async def test_fail_all_pending_unblocks_in_flight_calls(self) -> None:
        """A dropped connection must not leave a tool call hanging until its own timeout."""

        async def send(frame):
            return None

        session = HostAgentSession(UserId("u1"), send)
        call_task = asyncio.ensure_future(session.call("run", {}, timeout_s=30))
        await asyncio.sleep(0)
        session.fail_all_pending("disconnected")
        with pytest.raises(SandboxError, match="disconnected"):
            await call_task


class TestHostAgentSessionStreaming:
    @pytest.mark.anyio
    async def test_call_streaming_yields_stream_frames_then_the_result_and_stops(self) -> None:
        sent = []

        async def send(frame):
            sent.append(frame)

        session = HostAgentSession(UserId("u1"), send)
        collected = []

        async def drive():
            async for frame in session.call_streaming("run", {"command": ["echo"]}, timeout_s=5):
                collected.append(frame)

        task = asyncio.ensure_future(drive())
        await asyncio.sleep(0)  # let it register and send
        call_id = sent[0]["id"]

        session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "a"})
        session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "b"})
        await asyncio.sleep(0)
        session.resolve({"type": "result", "id": call_id, "result": {"exit_code": 0}})
        await task

        assert [f["type"] for f in collected] == ["stream", "stream", "result"]
        assert [f["data"] for f in collected[:2]] == ["a", "b"]
        assert collected[-1]["result"] == {"exit_code": 0}

    @pytest.mark.anyio
    async def test_call_streaming_raises_on_an_error_frame(self) -> None:
        sent = []

        async def send(frame):
            sent.append(frame)

        session = HostAgentSession(UserId("u1"), send)

        async def drive():
            async for _ in session.call_streaming("run", {}, timeout_s=5):
                pass

        task = asyncio.ensure_future(drive())
        await asyncio.sleep(0)
        session.resolve({"type": "error", "id": sent[0]["id"], "message": "boom"})
        with pytest.raises(SandboxError, match="boom"):
            await task

    @pytest.mark.anyio
    async def test_call_streaming_idle_timeout_resets_on_each_frame(self) -> None:
        """The wait between frames is an idle timeout, not one fixed deadline -- a call that keeps
        receiving frames must not time out just because the *total* elapsed time exceeds
        `timeout_s`, as long as no single gap between frames does."""
        sent = []

        async def send(frame):
            sent.append(frame)

        session = HostAgentSession(UserId("u1"), send)
        collected = []

        async def drive():
            async for frame in session.call_streaming("run", {}, timeout_s=0.05):
                collected.append(frame)

        task = asyncio.ensure_future(drive())
        await asyncio.sleep(0)
        call_id = sent[0]["id"]

        # Three frames, each well inside the 0.05s idle window, spanning more than 0.05s total.
        for _ in range(3):
            await asyncio.sleep(0.02)
            session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "x"})
        session.resolve({"type": "result", "id": call_id, "result": {"exit_code": 0}})
        await task
        assert len(collected) == 4

    @pytest.mark.anyio
    async def test_call_streaming_times_out_on_true_silence(self) -> None:
        async def send(frame):
            return None

        session = HostAgentSession(UserId("u1"), send)

        async def drive():
            async for _ in session.call_streaming("run", {}, timeout_s=0.05):
                pass

        with pytest.raises(SandboxError, match="did not respond"):
            await drive()

    @pytest.mark.anyio
    async def test_fail_all_pending_unblocks_an_in_flight_streaming_call(self) -> None:
        async def send(frame):
            return None

        session = HostAgentSession(UserId("u1"), send)

        async def drive():
            async for _ in session.call_streaming("run", {}, timeout_s=30):
                pass

        task = asyncio.ensure_future(drive())
        await asyncio.sleep(0)
        session.fail_all_pending("disconnected")
        with pytest.raises(SandboxError, match="disconnected"):
            await task


class TestHostAgentRegistry:
    @pytest.mark.anyio
    async def test_register_then_resolve(self) -> None:
        registry = HostAgentRegistry()
        session = HostAgentSession(UserId("u1"), lambda frame: asyncio.sleep(0))
        await registry.register(session)
        assert await registry.resolve(UserId("u1")) is session
        assert await registry.resolve(UserId("u2")) is None

    @pytest.mark.anyio
    async def test_a_second_pairing_replaces_the_first_and_fails_its_pending_calls(self) -> None:
        registry = HostAgentRegistry()
        first = HostAgentSession(UserId("u1"), lambda frame: asyncio.sleep(0))
        await registry.register(first)
        pending = asyncio.ensure_future(first.call("run", {}, timeout_s=30))
        await asyncio.sleep(0)

        second = HostAgentSession(UserId("u1"), lambda frame: asyncio.sleep(0))
        await registry.register(second)

        assert await registry.resolve(UserId("u1")) is second
        with pytest.raises(SandboxError, match="replaced"):
            await pending

    @pytest.mark.anyio
    async def test_unregister_only_removes_the_current_session(self) -> None:
        """A stale disconnect handler for a session that was already replaced must not evict the
        newer one -- otherwise a slow-to-notice old connection could unregister a live daemon."""
        registry = HostAgentRegistry()
        first = HostAgentSession(UserId("u1"), lambda frame: asyncio.sleep(0))
        await registry.register(first)
        second = HostAgentSession(UserId("u1"), lambda frame: asyncio.sleep(0))
        await registry.register(second)

        await registry.unregister(first)
        assert await registry.resolve(UserId("u1")) is second


class TestHostAgentSandboxRunner:
    @pytest.mark.anyio
    async def test_create_task_without_a_connected_session_raises(self) -> None:
        runner = HostAgentSandboxRunner(HostAgentRegistry())
        with pytest.raises(SandboxError, match="no desktop app"):
            await runner.create_task(user_id=UserId("nobody"))

    @pytest.mark.anyio
    async def test_create_task_without_a_user_id_raises(self) -> None:
        runner = HostAgentSandboxRunner(HostAgentRegistry())
        with pytest.raises(SandboxError, match="caller identity"):
            await runner.create_task()

    @pytest.mark.anyio
    async def test_non_streaming_run_waits_for_the_result_past_stream_frames(self) -> None:
        """The real daemon streams every `run`'s output (aura_hostd/lib/src/rpc_client.dart) and
        then sends the aggregated output in `result`. A single-shot `run` used to settle on the
        first `stream` frame: empty stdout and exit -1 for every command that printed anything,
        which broke git diff/commit/checkout on a paired desktop."""
        registry = HostAgentRegistry()

        async def send(frame):
            call_id = frame["id"]
            if frame["method"] == "create_task":
                session.resolve({"type": "result", "id": call_id, "result": {"task_id": "t1"}})
                return
            for chunk in ("diff --git a/x b/x\n", "+added\n"):
                session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": chunk})
            session.resolve({
                "type": "result", "id": call_id,
                "result": {"stdout": "diff --git a/x b/x\n+added\n", "stderr": "", "exit_code": 0},
            })

        session = HostAgentSession(UserId("u1"), send)
        await registry.register(session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        result = await runner.run(handle, ["git", "diff"], timeout_s=5)

        assert result.exit_code == 0
        assert result.stdout == "diff --git a/x b/x\n+added\n"

    @pytest.mark.anyio
    async def test_full_roundtrip_against_a_fake_daemon(self) -> None:
        """Drives create_task/write_file/read_file/run through a session whose `send` answers
        each call itself -- a minimal, real (not mocked) stand-in daemon."""
        registry = HostAgentRegistry()
        files: dict[str, str] = {}

        async def send(frame):
            method, call_id, params = frame["method"], frame["id"], frame["params"]
            if method == "create_task":
                result = {"task_id": "task-1"}
            elif method == "write_file":
                files[params["path"]] = params["content"]
                result = {}
            elif method == "read_file":
                result = {"content": files[params["path"]]}
            elif method == "run":
                result = {"stdout": "ok\n", "stderr": "", "exit_code": 0}
            else:
                raise AssertionError(f"unexpected method {method!r}")
            session.resolve({"type": "result", "id": call_id, "result": result})

        session = HostAgentSession(UserId("u1"), send)
        await registry.register(session)

        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))
        await runner.write_file(handle, "hello.txt", "hi")
        assert await runner.read_file(handle, "hello.txt") == "hi"
        result = await runner.run(handle, ["echo", "ok"], timeout_s=5)
        assert result.exit_code == 0 and "ok" in result.stdout

        # destroy is a documented no-op -- the daemon, not the kernel, owns the workspace's
        # lifetime -- so it must not raise and must not stop further calls on the same handle.
        await runner.destroy(handle)

    @pytest.mark.anyio
    async def test_run_streaming_yields_stdout_stderr_events_then_done(self) -> None:
        """Drives `run_streaming` against a fake daemon that answers with several `stream` frames
        before its final `result` -- the shape a real `aura_hostd` sends for a long-running
        command."""
        registry = HostAgentRegistry()

        async def send(frame):
            method, call_id, params = frame["method"], frame["id"], frame["params"]
            if method == "create_task":
                session.resolve({"type": "result", "id": call_id, "result": {"task_id": "task-1"}})
                return
            assert method == "run"
            session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "line 1\n"})
            session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "line 2\n"})
            session.resolve({"type": "stream", "id": call_id, "stream": "stderr", "data": "warn\n"})
            session.resolve({"type": "result", "id": call_id, "result": {"exit_code": 0}})

        session = HostAgentSession(UserId("u1"), send)
        await registry.register(session)

        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        events = [
            e async for e in runner.run_streaming(handle, ["sh", "-c", "..."], timeout_s=5)
        ]
        assert [(e.kind, e.data) for e in events[:3]] == [
            ("stdout", "line 1\n"), ("stdout", "line 2\n"), ("stderr", "warn\n"),
        ]
        assert events[-1].kind == "done" and events[-1].exit_code == 0

    @pytest.mark.anyio
    async def test_run_streaming_propagates_an_error_frame(self) -> None:
        registry = HostAgentRegistry()

        async def send(frame):
            method, call_id = frame["method"], frame["id"]
            if method == "create_task":
                session.resolve({"type": "result", "id": call_id, "result": {"task_id": "task-1"}})
                return
            session.resolve({"type": "error", "id": call_id, "message": "daemon exploded"})

        session = HostAgentSession(UserId("u1"), send)
        await registry.register(session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        with pytest.raises(SandboxError, match="daemon exploded"):
            async for _ in runner.run_streaming(handle, ["sh", "-c", "x"], timeout_s=5):
                pass


class TestCompositeSandboxRunner:
    @pytest.mark.anyio
    async def test_prefers_a_connected_host_session_over_the_fallback(self) -> None:
        registry = HostAgentRegistry()
        calls = []

        async def send(frame):
            calls.append(frame["method"])
            session.resolve({"type": "result", "id": frame["id"], "result": {"task_id": "t1"}})

        session = HostAgentSession(UserId("u1"), send)
        await registry.register(session)

        class _NeverCalledFallback:
            async def create_task(self):
                raise AssertionError("fallback should not be used when a host session exists")

        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(registry), fallback=_NeverCalledFallback()
        )
        handle = await composite.create_task(user_id=UserId("u1"))
        assert handle.task_id == "t1"
        assert calls == ["create_task"]

    @pytest.mark.anyio
    async def test_falls_back_when_no_host_session_is_connected(self) -> None:
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        fallback = InMemorySandboxRunner()
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=fallback
        )
        handle = await composite.create_task(user_id=UserId("u1"))
        await composite.write_file(handle, "a.txt", "hi")
        assert await composite.read_file(handle, "a.txt") == "hi"

    @pytest.mark.anyio
    async def test_raises_with_neither_a_session_nor_a_fallback(self) -> None:
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=None
        )
        with pytest.raises(SandboxError, match="no desktop app is connected"):
            await composite.create_task(user_id=UserId("u1"))

    @pytest.mark.anyio
    async def test_routing_is_fixed_per_handle_not_reevaluated(self) -> None:
        """Once a handle is created against the fallback, a desktop app pairing mid-task must not
        migrate that handle's later calls onto the host runner -- routing is per-task, not
        per-call (class docstring)."""
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        registry = HostAgentRegistry()
        fallback = InMemorySandboxRunner()
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(registry), fallback=fallback
        )
        handle = await composite.create_task(user_id=UserId("u1"))
        await composite.write_file(handle, "a.txt", "before pairing")

        async def send(frame):
            raise AssertionError("the host runner must not be called for this handle")

        await registry.register(HostAgentSession(UserId("u1"), send))
        assert await composite.read_file(handle, "a.txt") == "before pairing"

    @pytest.mark.anyio
    async def test_run_streaming_delegates_to_a_streaming_host_runner(self) -> None:
        registry = HostAgentRegistry()

        async def send(frame):
            method, call_id = frame["method"], frame["id"]
            if method == "create_task":
                session.resolve({"type": "result", "id": call_id, "result": {"task_id": "t1"}})
                return
            session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "hi\n"})
            session.resolve({"type": "result", "id": call_id, "result": {"exit_code": 0}})

        session = HostAgentSession(UserId("u1"), send)
        await registry.register(session)
        composite = CompositeSandboxRunner(host_runner=HostAgentSandboxRunner(registry), fallback=None)
        handle = await composite.create_task(user_id=UserId("u1"))

        events = [e async for e in composite.run_streaming(handle, ["sh", "-c", "x"], timeout_s=5)]
        assert [(e.kind, e.data) for e in events[:1]] == [("stdout", "hi\n")]
        assert events[-1].kind == "done" and events[-1].exit_code == 0

    @pytest.mark.anyio
    async def test_run_streaming_replays_a_non_streaming_fallback_as_one_chunk_plus_done(self) -> None:
        """The Docker sandbox (and the in-memory test fake) do not implement
        `StreamingSandboxRunner` -- `run_streaming` on a handle routed there must still work,
        replaying the ordinary `run` result as stdout/stderr chunks followed by `done`, so a
        caller that always uses the streaming interface does not need two code paths."""
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        fallback = InMemorySandboxRunner()
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=fallback
        )
        handle = await composite.create_task(user_id=UserId("u1"))

        events = [
            e async for e in composite.run_streaming(handle, ["sh", "-c", "echo hi"], timeout_s=5)
        ]
        kinds = [e.kind for e in events]
        assert kinds == ["stdout", "done"]
        assert "hi" in events[0].data
        assert events[-1].exit_code == 0

    @pytest.mark.anyio
    async def test_a_durable_workspace_handle_routes_to_the_fallback_runner(self) -> None:
        """Production incident: `open_workspace`'s tool body (tools/coding.py) opens a durable,
        per-repo workspace through `WorkspaceManager` -- which `Container._build_workspace_manager`
        always backs with the Docker runner, never this composite's `create_task` -- and then
        calls `sandbox_runner.run(...)` on *this* composite to list the workspace's contents. That
        handle was never registered by `create_task`, so `_runner_for` raised "unknown sandbox
        task" unconditionally: the Docker container was created and running the whole time, but
        every durable-workspace tool call still failed 100% of the time on its very next line.
        Confirmed live: `open_workspace` recorded 0 "ok" and 8 "error" outcomes in production
        while its own workspace container stayed up and healthy throughout. A handle `create_task`
        never minted can only have come from `WorkspaceManager.open()`, which is always backed by
        the fallback runner -- so it must resolve there instead of raising."""
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        fallback = InMemorySandboxRunner()
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=fallback
        )
        # Exactly what `WorkspaceManager.open()` does: calls `open_workspace` on the *fallback*
        # runner directly, never touching `composite.create_task` or its `_handle_runner` map.
        handle = await fallback.open_workspace("workspace-key-for-velvet")

        result = await composite.run(handle, ["sh", "-c", "ls -A"], timeout_s=5)
        assert result.exit_code == 0

        await composite.write_file(handle, "probe.txt", "hi")
        assert await composite.read_file(handle, "probe.txt") == "hi"

    @pytest.mark.anyio
    async def test_an_unregistered_handle_still_raises_with_no_fallback_configured(self) -> None:
        """Without a fallback there is nothing a durable-workspace handle could legitimately
        resolve to either, so this stays a hard failure rather than silently doing nothing."""
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=None
        )
        with pytest.raises(SandboxError, match="unknown sandbox task"):
            await composite.run(SandboxHandle(task_id="ghost"), ["sh", "-c", "x"], timeout_s=5)

    @pytest.mark.anyio
    async def test_workspace_path_delegates_to_the_fallback_for_a_durable_handle(self) -> None:
        """Production bug: `git_push`/`clone_repository` (`tools/coding.py`) call
        `sandbox_runner.workspace_path(...)` on this composite, which had no such method at all --
        an unconditional `AttributeError` on every call, regardless of which underlying runner a
        durable handle resolves to. `_runner_for` guarantees a `WorkspaceManager`-opened handle
        (exactly what `git_push`/`clone_repository` use) always resolves to the fallback, so
        delegating there must return the same path the fallback itself reports."""
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        fallback = InMemorySandboxRunner()
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=fallback
        )
        handle = await fallback.open_workspace("workspace-key-for-velvet")

        assert await composite.workspace_path(handle) == await fallback.workspace_path(handle)


class TestHostAgentSandboxRunnerWorkspacePath:
    @pytest.mark.anyio
    async def test_raises_a_clear_sandbox_error_instead_of_an_attribute_error(self) -> None:
        """A host-agent task runs on the user's own machine, not this server, so it has no
        server-side workspace path to report. `_runner_for`'s invariant means production code
        should never actually reach this, but the method must exist and fail loudly rather than
        let a stray call surface an opaque `AttributeError`."""
        runner = HostAgentSandboxRunner(HostAgentRegistry())
        with pytest.raises(SandboxError, match="workspace_path"):
            await runner.workspace_path(SandboxHandle(task_id="t1"))


class TestHostAgentApprovalTimeout:
    """`run`/`run_streaming`'s RPC timeout must reserve enough time for a D5 interactive-approval
    wait on the daemon side (`approval_queue.dart`'s 5-minute timeout), not just network slack --
    `write_file`/`write_files` already got this fix; `run`/`run_streaming` had not."""

    @pytest.mark.anyio
    async def test_run_floors_its_call_timeout_at_330s(self) -> None:
        captured: dict[str, float] = {}

        class _RecordingSession:
            async def call(self, method, params, *, timeout_s):
                captured["timeout_s"] = timeout_s
                return {"stdout": "", "stderr": "", "exit_code": 0}

        runner = HostAgentSandboxRunner(HostAgentRegistry())

        async def _fake_session_for(handle):
            return _RecordingSession()

        runner._session_for = _fake_session_for  # type: ignore[method-assign]

        await runner.run(SandboxHandle(task_id="t1"), ["sh", "-c", "true"], timeout_s=60.0)
        assert captured["timeout_s"] == 330.0

        await runner.run(SandboxHandle(task_id="t1"), ["sh", "-c", "true"], timeout_s=400.0)
        assert captured["timeout_s"] == 405.0

    @pytest.mark.anyio
    async def test_run_streaming_floors_its_call_timeout_at_330s(self) -> None:
        captured: dict[str, float] = {}

        class _RecordingSession:
            async def call_streaming(self, method, params, *, timeout_s):
                captured["timeout_s"] = timeout_s
                if False:
                    yield None  # pragma: no cover - makes this an async generator

        runner = HostAgentSandboxRunner(HostAgentRegistry())

        async def _fake_session_for(handle):
            return _RecordingSession()

        runner._session_for = _fake_session_for  # type: ignore[method-assign]

        async for _ in runner.run_streaming(SandboxHandle(task_id="t1"), ["sh", "-c", "true"], timeout_s=60.0):
            pass
        assert captured["timeout_s"] == 330.0

        async for _ in runner.run_streaming(SandboxHandle(task_id="t1"), ["sh", "-c", "true"], timeout_s=400.0):
            pass
        assert captured["timeout_s"] == 405.0


class TestWorkspaceRootAsCwd:
    """FSA-006, second half: the IDE's active root travels to the daemon as `run`'s `cwd`.

    The daemon has tolerated a `cwd` param on `run` since D5 and refuses one outside every granted
    root; until now nothing sent it, so `run` always executed in the first granted root.
    """

    _ROOT = "/home/me/projects/aura"

    @staticmethod
    def _recording_daemon(registry):
        seen: list[dict] = []

        async def send(frame):
            method, call_id, params = frame["method"], frame["id"], frame["params"]
            if method == "create_task":
                session.resolve({"type": "result", "id": call_id, "result": {"task_id": "task-1"}})
                return
            seen.append(params)
            session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "ok\n"})
            session.resolve({
                "type": "result", "id": call_id,
                "result": {"stdout": "ok\n", "stderr": "", "exit_code": 0},
            })

        session = HostAgentSession(UserId("u1"), send)
        return session, seen

    @pytest.mark.anyio
    async def test_run_forwards_the_root_as_cwd(self) -> None:
        registry = HostAgentRegistry()
        session, seen = self._recording_daemon(registry)
        await registry.register(session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        await runner.run(handle, ["sh", "-c", "pwd"], timeout_s=5, cwd=self._ROOT)

        assert seen[0]["cwd"] == self._ROOT

    @pytest.mark.anyio
    async def test_run_streaming_forwards_the_root_as_cwd(self) -> None:
        registry = HostAgentRegistry()
        session, seen = self._recording_daemon(registry)
        await registry.register(session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        _ = [e async for e in runner.run_streaming(handle, ["sh", "-c", "pwd"], timeout_s=5, cwd=self._ROOT)]

        assert seen[0]["cwd"] == self._ROOT

    @pytest.mark.anyio
    async def test_no_root_sends_no_cwd_key_so_the_daemon_keeps_its_fallback(self) -> None:
        """An absent `cwd` is the daemon's "first granted root" path; sending `null` would be a
        different wire shape that an older daemon has never been exercised against."""
        registry = HostAgentRegistry()
        session, seen = self._recording_daemon(registry)
        await registry.register(session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        await runner.run(handle, ["sh", "-c", "pwd"], timeout_s=5)

        assert "cwd" not in seen[0]

    @pytest.mark.anyio
    async def test_the_composite_passes_it_through_to_the_routed_runner(self) -> None:
        registry = HostAgentRegistry()
        session, seen = self._recording_daemon(registry)
        await registry.register(session)
        composite = CompositeSandboxRunner(host_runner=HostAgentSandboxRunner(registry), fallback=None)
        handle = await composite.create_task(user_id=UserId("u1"))

        await composite.run(handle, ["sh", "-c", "pwd"], timeout_s=5, cwd=self._ROOT)

        assert seen[0]["cwd"] == self._ROOT
