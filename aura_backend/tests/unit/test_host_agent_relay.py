"""Cross-worker host-agent relay (`adapters/host_agent_relay.py`).

The bug it fixes: a paired desktop daemon's WebSocket lives in one gateway worker, and
`HostAgentRegistry` was per process, so with `--workers 4` most Engineer turns found no desktop
(ADR-011, "Deployment constraint"). Here two registries share one `InMemoryRelayBus` and stand in
for two workers: "A" holds the daemon's socket, "B" serves the chat turn. Everything above the
registry -- `HostAgentSandboxRunner`, the tools -- runs unchanged on B.

The daemon is the same minimal real stand-in `test_host_agent.py` uses: a `send` callable that
answers each call by feeding frames back into the session.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from aura_core.adapters.docker_sandbox import SandboxError
from aura_core.adapters.host_agent import HostAgentRegistry, HostAgentSandboxRunner, HostAgentSession
from aura_core.adapters.host_agent_relay import HostAgentRelay, InMemoryRelayBus, RemoteHostAgentSession
from aura_core.domain.identifiers import UserId

_SECRET = "jwt-secret-shared-by-every-worker"
_USER = UserId("engineer-1")


def _worker(bus: InMemoryRelayBus, name: str, **kwargs: Any) -> HostAgentRegistry:
    return HostAgentRegistry(relay=HostAgentRelay(bus, _SECRET, worker_id=name, **kwargs))


class _Daemon:
    """A fake `aura_hostd`: answers `create_task`/`write_file`/`read_file`/`run` like the real
    one, streaming `run` output before its result. `silent=True` never answers anything."""

    def __init__(self, user: UserId = _USER, *, silent: bool = False) -> None:
        self.files: dict[str, str] = {}
        self.calls: list[str] = []
        self.silent = silent
        self.session = HostAgentSession(user, self._send)

    async def _send(self, frame: dict[str, Any]) -> None:
        method, call_id, params = frame["method"], frame["id"], frame["params"]
        self.calls.append(method)
        if self.silent:
            return
        if method == "create_task":
            self._result(call_id, {"task_id": "task-1"})
        elif method == "write_file":
            self.files[params["path"]] = params["content"]
            self._result(call_id, {})
        elif method == "read_file":
            if params["path"] not in self.files:
                self.session.resolve({"type": "error", "id": call_id, "message": "no such file"})
                return
            self._result(call_id, {"content": self.files[params["path"]]})
        elif method == "run":
            self.session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "one\n"})
            self.session.resolve({"type": "stream", "id": call_id, "stream": "stderr", "data": "warn\n"})
            self._result(call_id, {"stdout": "one\n", "stderr": "warn\n", "exit_code": 3})
        else:
            raise AssertionError(f"unexpected method {method!r}")

    def _result(self, call_id: str, result: dict[str, Any]) -> None:
        self.session.resolve({"type": "result", "id": call_id, "result": result})


@pytest.fixture
async def workers():
    bus = InMemoryRelayBus()
    a, b = _worker(bus, "A"), _worker(bus, "B")
    yield bus, a, b
    await a.relay.close()
    await b.relay.close()


class TestReachingASessionOnAnotherWorker:
    @pytest.mark.anyio
    async def test_the_worker_without_the_socket_gets_a_working_proxy(self, workers) -> None:
        _, a, b = workers
        daemon = _Daemon()
        await a.register(daemon.session)

        remote = await b.resolve(_USER)

        assert isinstance(remote, RemoteHostAgentSession)
        assert remote.owner == "A"
        assert await remote.call("create_task", {}, timeout_s=5) == {"task_id": "task-1"}

    @pytest.mark.anyio
    async def test_the_worker_holding_the_socket_still_uses_it_directly(self, workers) -> None:
        _, a, _ = workers
        daemon = _Daemon()
        await a.register(daemon.session)

        assert await a.resolve(_USER) is daemon.session

    @pytest.mark.anyio
    async def test_the_sandbox_runner_works_unchanged_through_the_relay(self, workers) -> None:
        """What the Engineer tools actually do on a turn served by B."""
        _, a, b = workers
        daemon = _Daemon()
        await a.register(daemon.session)
        runner = HostAgentSandboxRunner(b)

        assert await runner.has_session(_USER)
        handle = await runner.create_task(user_id=_USER)
        await runner.write_file(handle, "hello.txt", "hi")
        assert await runner.read_file(handle, "hello.txt") == "hi"
        assert daemon.files == {"hello.txt": "hi"}

        result = await runner.run(handle, ["make"], timeout_s=5)
        assert (result.stdout, result.stderr, result.exit_code) == ("one\n", "warn\n", 3)

    @pytest.mark.anyio
    async def test_streaming_frames_arrive_in_order_then_done(self, workers) -> None:
        _, a, b = workers
        daemon = _Daemon()
        await a.register(daemon.session)
        runner = HostAgentSandboxRunner(b)
        handle = await runner.create_task(user_id=_USER)

        events = [e async for e in runner.run_streaming(handle, ["make"], timeout_s=5)]

        assert [(e.kind, e.data) for e in events[:2]] == [("stdout", "one\n"), ("stderr", "warn\n")]
        assert events[2].kind == "done" and events[2].exit_code == 3

    @pytest.mark.anyio
    async def test_a_daemon_error_reaches_the_caller_as_a_sandbox_error(self, workers) -> None:
        _, a, b = workers
        await a.register(_Daemon().session)
        remote = await b.resolve(_USER)

        with pytest.raises(SandboxError, match="no such file"):
            await remote.call("read_file", {"path": "missing"}, timeout_s=5)

    @pytest.mark.anyio
    async def test_no_session_anywhere_resolves_to_none(self, workers) -> None:
        _, a, b = workers
        assert await a.resolve(_USER) is None
        assert await b.resolve(_USER) is None


class TestDisconnectAndReplacement:
    @pytest.mark.anyio
    async def test_after_disconnect_no_worker_resolves_the_session(self, workers) -> None:
        _, a, b = workers
        daemon = _Daemon()
        await a.register(daemon.session)
        await a.unregister(daemon.session)

        assert await b.resolve(_USER) is None
        assert await a.resolve(_USER) is None

    @pytest.mark.anyio
    async def test_a_disconnect_fails_a_relayed_call_promptly_not_at_its_timeout(self, workers) -> None:
        _, a, b = workers
        daemon = _Daemon(silent=True)
        await a.register(daemon.session)
        remote = await b.resolve(_USER)

        call = asyncio.create_task(remote.call("run", {"command": ["sleep"]}, timeout_s=60))
        for _ in range(100):
            if daemon.calls:
                break
            await asyncio.sleep(0.01)
        await a.unregister(daemon.session)

        with pytest.raises(SandboxError, match="disconnected"):
            await asyncio.wait_for(call, timeout=2)

    @pytest.mark.anyio
    async def test_a_newer_pairing_on_another_worker_replaces_the_old_one(self, workers) -> None:
        """Same rule as the single-process registry: the second pairing wins and the first one's
        in-flight calls fail. Here the two connections land on different workers."""
        _, a, b = workers
        old = _Daemon(silent=True)
        await a.register(old.session)
        in_flight = asyncio.create_task(old.session.call("run", {}, timeout_s=60))
        await asyncio.sleep(0)

        new = _Daemon()
        await b.register(new.session)

        with pytest.raises(SandboxError, match="replaced"):
            await asyncio.wait_for(in_flight, timeout=2)
        # A no longer serves the old session; it routes to B, the newest connection.
        routed = await a.resolve(_USER)
        assert isinstance(routed, RemoteHostAgentSession) and routed.owner == "B"
        assert await routed.call("create_task", {}, timeout_s=5) == {"task_id": "task-1"}
        assert await b.resolve(_USER) is new.session

    @pytest.mark.anyio
    async def test_an_owner_that_never_answers_times_out_like_a_silent_daemon(self) -> None:
        bus = InMemoryRelayBus()
        a, b = _worker(bus, "A"), _worker(bus, "B")
        await a.register(_Daemon().session)
        remote = await b.resolve(_USER)
        # Worker A dies without releasing: its listener is gone, its ownership record is not.
        await a.relay._bus.claim(f"aura:host-agent:owner:{_USER.value}", "A", 30)
        for task in (a.relay._listener, a.relay._heartbeat):
            task.cancel()

        with pytest.raises(SandboxError, match="did not respond"):
            await remote.call("create_task", {}, timeout_s=0.1)
        await b.relay.close()

    @pytest.mark.anyio
    async def test_ownership_lapses_when_its_worker_stops_heartbeating(self) -> None:
        now = [1000.0]
        bus = InMemoryRelayBus(clock=lambda: now[0])
        a, b = _worker(bus, "A"), _worker(bus, "B")
        await a.register(_Daemon().session)
        assert isinstance(await b.resolve(_USER), RemoteHostAgentSession)

        now[0] += 31  # past the 30 s TTL, and no heartbeat has run

        assert await b.resolve(_USER) is None
        await a.relay.close()
        await b.relay.close()

    @pytest.mark.anyio
    async def test_the_heartbeat_keeps_ownership_alive(self) -> None:
        bus = InMemoryRelayBus()
        a = _worker(bus, "A", owner_ttl_s=0.3)
        b = _worker(bus, "B", owner_ttl_s=0.3)
        await a.register(_Daemon().session)

        await asyncio.sleep(0.75)  # more than two TTLs

        assert isinstance(await b.resolve(_USER), RemoteHostAgentSession)
        await a.relay.close()
        assert await b.resolve(_USER) is None, "closing a worker releases what it owned"
        await b.relay.close()


class TestSignedMessages:
    """Bus access alone must not let anyone send commands to a user's machine."""

    async def _call_envelope(self, sender: HostAgentRelay, owner: str) -> bytes:
        captured: list[bytes] = []

        class _Capture:
            async def publish(self, channel: str, message: bytes) -> None:
                captured.append(message)

        real_bus, sender._bus = sender._bus, _Capture()
        await sender._publish(
            owner,
            {
                "op": "call", "call_id": "c-1", "user_id": _USER.value, "method": "run",
                "params": {"command": ["rm", "-rf", "/"]}, "timeout_s": 5, "stream": False,
            },
        )
        sender._bus = real_bus
        return captured[0]

    async def _deliver(self, bus: InMemoryRelayBus, envelope: bytes) -> None:
        await bus.publish("aura:host-agent:worker:A", envelope)
        for _ in range(5):
            await asyncio.sleep(0.01)

    @pytest.mark.anyio
    async def test_a_message_signed_with_another_key_is_dropped(self, workers) -> None:
        bus, a, _ = workers
        daemon = _Daemon()
        await a.register(daemon.session)
        attacker = HostAgentRelay(bus, "not-the-deployment-secret", worker_id="X")

        await self._deliver(bus, await self._call_envelope(attacker, "A"))

        assert daemon.calls == []

    @pytest.mark.anyio
    async def test_an_unsigned_message_is_dropped(self, workers) -> None:
        bus, a, _ = workers
        daemon = _Daemon()
        await a.register(daemon.session)
        body = json.dumps({"op": "call", "call_id": "c-1", "user_id": _USER.value, "method": "run",
                           "params": {}, "timeout_s": 5, "stream": False, "from": "X", "ts": 0})

        await self._deliver(bus, json.dumps({"v": 1, "body": body, "sig": "0" * 64}).encode())

        assert daemon.calls == []

    @pytest.mark.anyio
    async def test_a_stale_message_is_dropped(self, workers) -> None:
        bus, a, _ = workers
        daemon = _Daemon()
        await a.register(daemon.session)
        stale = HostAgentRelay(bus, _SECRET, worker_id="X", clock=lambda: time.time() - 120)

        await self._deliver(bus, await self._call_envelope(stale, "A"))

        assert daemon.calls == []

    @pytest.mark.anyio
    async def test_a_replayed_call_runs_once(self, workers) -> None:
        bus, a, _ = workers
        daemon = _Daemon()
        await a.register(daemon.session)
        sender = HostAgentRelay(bus, _SECRET, worker_id="X")
        envelope = await self._call_envelope(sender, "A")

        await self._deliver(bus, envelope)
        await self._deliver(bus, envelope)

        assert daemon.calls == ["run"]
        await sender.close()


class TestBusFailures:
    class _BrokenBus(InMemoryRelayBus):
        async def owner(self, key: str) -> str | None:
            raise ConnectionError("redis is down")

        async def claim(self, key: str, value: str, ttl_s: float) -> str | None:
            raise ConnectionError("redis is down")

    @pytest.mark.anyio
    async def test_a_bus_outage_never_hides_the_local_session(self) -> None:
        registry = HostAgentRegistry(relay=HostAgentRelay(self._BrokenBus(), _SECRET, worker_id="A"))
        daemon = _Daemon()

        await registry.register(daemon.session)  # claim fails; registration must not

        assert await registry.resolve(_USER) is daemon.session
        await registry.relay.close()


class TestWithoutARelay:
    @pytest.mark.anyio
    async def test_a_registry_without_a_relay_is_single_process_as_before(self) -> None:
        registry = HostAgentRegistry()
        daemon = _Daemon()
        await registry.register(daemon.session)

        assert registry.relay is None
        assert await registry.resolve(_USER) is daemon.session


class TestContainerWiring:
    """`Container._build_host_agent_relay`: on with Redis and a secret, off otherwise."""

    def _container(self, monkeypatch, **env: str):
        from aura_core.config import load_settings
        from aura_core.container import Container

        for key in ("AURA_REDIS_URL", "AURA_JWT_SECRET", "JWT_SECRET", "AURA_HOST_AGENT_RELAY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Container(load_settings(refresh=True, validate=False))

    def test_on_with_redis_and_a_secret(self, monkeypatch) -> None:
        container = self._container(
            monkeypatch, AURA_REDIS_URL="redis://127.0.0.1:9/0", AURA_JWT_SECRET="s"
        )
        assert isinstance(container.host_agent_registry.relay, HostAgentRelay)

    def test_off_without_redis(self, monkeypatch) -> None:
        container = self._container(monkeypatch, AURA_JWT_SECRET="s")
        assert container.host_agent_registry.relay is None

    def test_off_without_a_secret_to_sign_with(self, monkeypatch) -> None:
        container = self._container(monkeypatch, AURA_REDIS_URL="redis://127.0.0.1:9/0")
        assert container.host_agent_registry.relay is None

    def test_the_kill_switch_turns_it_off(self, monkeypatch) -> None:
        container = self._container(
            monkeypatch, AURA_REDIS_URL="redis://127.0.0.1:9/0", AURA_JWT_SECRET="s",
            AURA_HOST_AGENT_RELAY="false",
        )
        assert container.host_agent_registry.relay is None

    def test_a_celery_worker_process_never_gets_a_relay(self, monkeypatch) -> None:
        """Background jobs have no live chat turn behind them, so a worker process gets no route
        to anyone's desktop even with Redis and a secret configured (`tasks._worker_settings`)."""
        import tasks
        from aura_core.container import Container

        monkeypatch.setenv("AURA_REDIS_URL", "redis://127.0.0.1:9/0")
        monkeypatch.setenv("AURA_JWT_SECRET", "s")
        monkeypatch.delenv("AURA_HOST_AGENT_RELAY", raising=False)

        settings = tasks._worker_settings()
        monkeypatch.delenv("AURA_HOST_AGENT_RELAY", raising=False)

        assert settings.infrastructure.host_agent_relay is False
        assert Container(settings).host_agent_registry.relay is None
