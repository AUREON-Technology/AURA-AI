"""SaaS P1-E: the Engineer for entitled customers -- entitlement, turns, and the daemon socket."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from starlette.websockets import WebSocketDisconnect

from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import UserId
from aura_core.domain.tenancy import WorkspaceRole


@pytest.fixture
def switch(container):
    def set_to(on: bool) -> None:
        container.settings = replace(container.settings, security=replace(container.settings.security,
                                                                          engineer_for_customers=on))
    set_to(True)
    return set_to


def _customer_app(container):
    """A User-role customer with no role capability: whatever Engineer they get comes from their plan."""
    async def authenticator(request):
        header = request.headers.get("authorization", "")
        return Principal(user_id=UserId(header[7:]), email="", role="User", capabilities=(), email_verified=True)

    return build_app(container, authenticator=authenticator)


async def _pro(container, user: str) -> str:
    ws = (await container.tenancy.ensure_personal(user)).id
    await container.plans.set_plan("sa", ws, "pro", 1)
    return ws


async def test_entitled_by_personal_pro_or_team_membership(container) -> None:
    plans = container.plans
    assert await plans.engineer_entitled("amy") is False
    await _pro(container, "amy")
    assert await plans.engineer_entitled("amy") is True
    team = await container.tenancy.create_team("own", "Acme")
    await plans.set_plan("sa", team.id, "team", 3)
    assert await plans.engineer_entitled("bea") is False
    _invite, token = await container.tenancy.invite("own", team.id, "bea@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept("bea", token)
    assert await plans.engineer_entitled("bea") is True
    await container.tenancy.remove("own", team.id, "bea")
    assert await plans.engineer_entitled("bea") is False


async def test_admit_turn_grants_engineer_to_a_pro_customer_only_when_switched_on(container, switch, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    seen: list[tuple] = []
    original = container.kernel.run

    async def spy(turn):
        seen.append(tuple(turn.capabilities))
        return await original(turn)

    monkeypatch.setattr(container.kernel, "run", spy)

    def chat(user):
        with TestClient(_customer_app(container)) as client:
            r = client.post("/api/v3/chat", headers={"authorization": f"Bearer {user}"},
                            json={"message": "hi", "use_tools": False})
            assert r.status_code == 200, r.text

    chat("cal")  # Free
    await _pro(container, "dee")
    chat("dee")
    switch(False)
    chat("dee")
    assert ["engineer_agent" in caps for caps in seen] == [False, True, False]


async def _connect_and_wait(client, user, registry):
    ws_cm = client.websocket_connect("/api/v3/host/agent")
    ws = ws_cm.__enter__()
    ws.send_json({"type": "auth", "token": user})
    for _ in range(100):
        if await registry.resolve(UserId(user)) is not None:
            break
        await asyncio.sleep(0.01)
    return ws_cm, ws


@pytest.mark.anyio
async def test_customer_daemon_connects_when_entitled_and_is_refused_on_free(container, switch) -> None:
    from fastapi.testclient import TestClient

    await container.startup()
    await _pro(container, "eve")
    with TestClient(_customer_app(container)) as client:
        cm, _ws = await _connect_and_wait(client, "eve", container.host_agent_registry)
        assert await container.host_agent_registry.resolve(UserId("eve")) is not None
        cm.__exit__(None, None, None)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "fred"})  # Free
                ws.receive_json()
        assert exc.value.code == 4401
        switch(False)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "eve"})  # Pro, but the switch is off
                ws.receive_json()
        assert exc.value.code == 4401


@pytest.mark.anyio
async def test_downgrade_closes_the_daemon_socket_at_recheck(container, switch, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from aura_core.api import ws_auth

    monkeypatch.setattr(ws_auth, "DEFAULT_RECHECK_S", 0.05)
    await container.startup()
    ws_id = await _pro(container, "gil")
    registry = container.host_agent_registry
    with TestClient(_customer_app(container)) as client:
        cm, _ws = await _connect_and_wait(client, "gil", registry)
        assert await registry.resolve(UserId("gil")) is not None
        await container.plans.set_plan("sa", ws_id, "free", 1)
        for _ in range(300):
            if await registry.resolve(UserId("gil")) is None:
                break
            await asyncio.sleep(0.01)
        assert await registry.resolve(UserId("gil")) is None
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass


@pytest.mark.anyio
async def test_a_lookup_error_at_recheck_keeps_a_connected_daemon(container, switch, monkeypatch) -> None:
    """Plan E review #1: a database blip must not drop a paying customer's daemon; admission
    itself still fails closed."""
    from fastapi.testclient import TestClient

    from aura_core.api import ws_auth

    monkeypatch.setattr(ws_auth, "DEFAULT_RECHECK_S", 0.05)
    await container.startup()
    await _pro(container, "hal")
    registry = container.host_agent_registry
    with TestClient(_customer_app(container)) as client:
        cm, _ws = await _connect_and_wait(client, "hal", registry)

        async def broken(subject):
            raise RuntimeError("db down")

        monkeypatch.setattr(container.plans, "engineer_entitled", broken)
        await asyncio.sleep(0.4)
        assert await registry.resolve(UserId("hal")) is not None
        cm.__exit__(None, None, None)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "hal"})
                ws.receive_json()
