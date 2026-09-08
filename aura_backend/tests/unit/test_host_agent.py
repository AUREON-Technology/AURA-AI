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
