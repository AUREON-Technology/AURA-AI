"""The host-agent relay over a real Redis (`adapters/redis_relay_bus.py`).

`tests/unit/test_host_agent_relay.py` proves the relay's behaviour on the in-memory bus. This
proves the Redis transport underneath it, which the in-memory bus cannot stand in for: the
`SET ... GET` claim, the compare-and-set Lua scripts for refresh and release, real pub/sub
delivery across separate connections, and a subscription that survives being idle for longer
than the command client's socket timeout.

Opt-in (``-m load``) and needs ``AURA_REDIS_URL``, e.g. a throwaway server::

    redis-server --port 6390 --save "" --appendonly no &
    AURA_REDIS_URL=redis://127.0.0.1:6390/0 pytest tests/load/test_host_agent_relay_redis.py -m load
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest

from aura_core.adapters.host_agent import HostAgentRegistry, HostAgentSandboxRunner, HostAgentSession
from aura_core.adapters.host_agent_relay import HostAgentRelay, RemoteHostAgentSession
from aura_core.adapters.redis_relay_bus import RedisRelayBus
from aura_core.domain.identifiers import UserId

pytestmark = [
    pytest.mark.load,
    pytest.mark.skipif(not os.environ.get("AURA_REDIS_URL"), reason="needs AURA_REDIS_URL"),
]

_SECRET = "jwt-secret-shared-by-every-worker"


def _worker(name: str, **kwargs: Any) -> HostAgentRegistry:
    bus = RedisRelayBus(os.environ["AURA_REDIS_URL"])
    return HostAgentRegistry(relay=HostAgentRelay(bus, _SECRET, worker_id=name, **kwargs))


def _daemon(user: UserId) -> HostAgentSession:
    async def send(frame: dict[str, Any]) -> None:
        call_id, method = frame["id"], frame["method"]
        if method == "run":
            session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "a\n"})
            session.resolve({"type": "result", "id": call_id, "result": {"stdout": "a\n", "exit_code": 0}})
        else:
            session.resolve({"type": "result", "id": call_id, "result": {"task_id": "task-1"}})

    session = HostAgentSession(user, send)
    return session


@pytest.fixture
def user() -> UserId:
    # A fresh user per test, so leftover keys from an earlier run cannot interfere.
    return UserId(f"relay-test-{uuid.uuid4().hex}")


@pytest.mark.anyio
async def test_a_call_and_a_stream_cross_workers(user: UserId) -> None:
    a, b = _worker(f"A-{uuid.uuid4().hex}"), _worker(f"B-{uuid.uuid4().hex}")
    try:
        await a.register(_daemon(user))
        runner = HostAgentSandboxRunner(b)

        handle = await runner.create_task(user_id=user)
        events = [e async for e in runner.run_streaming(handle, ["make"], timeout_s=5)]

        assert handle.task_id == "task-1"
        assert [(e.kind, e.data) for e in events] == [("stdout", "a\n"), ("done", "")]
    finally:
        await a.relay.close()
        await b.relay.close()


@pytest.mark.anyio
async def test_an_idle_subscription_outlives_the_command_timeout(user: UserId) -> None:
    a, b = _worker(f"A-{uuid.uuid4().hex}"), _worker(f"B-{uuid.uuid4().hex}")
    try:
        await a.register(_daemon(user))
        remote = await b.resolve(user)
        assert isinstance(remote, RemoteHostAgentSession)

        await asyncio.sleep(3.0)  # the command client's socket timeout is 2 s

        assert await remote.call("create_task", {}, timeout_s=5) == {"task_id": "task-1"}
    finally:
        await a.relay.close()
        await b.relay.close()


@pytest.mark.anyio
async def test_a_newer_pairing_takes_over_and_release_does_not_delete_it(user: UserId) -> None:
    a, b = _worker(f"A-{uuid.uuid4().hex}"), _worker(f"B-{uuid.uuid4().hex}")
    try:
        old = _daemon(user)
        await a.register(old)
        await b.register(_daemon(user))
        for _ in range(100):
            if await a._local(user) is None:
                break
            await asyncio.sleep(0.02)
        assert await a._local(user) is None, "A dropped its superseded session"

        # A's late disconnect must not delete B's ownership (compare-and-delete).
        await a.unregister(old)
        routed = await a.resolve(user)
        assert isinstance(routed, RemoteHostAgentSession) and routed.owner == b.relay.worker_id
    finally:
        await a.relay.close()
        await b.relay.close()


@pytest.mark.anyio
async def test_the_heartbeat_refreshes_and_close_releases(user: UserId) -> None:
    a = _worker(f"A-{uuid.uuid4().hex}", owner_ttl_s=0.6)
    b = _worker(f"B-{uuid.uuid4().hex}")
    try:
        await a.register(_daemon(user))
        await asyncio.sleep(1.5)  # more than two TTLs
        assert isinstance(await b.resolve(user), RemoteHostAgentSession)

        await a.relay.close()
        assert await b.resolve(user) is None
    finally:
        await b.relay.close()
