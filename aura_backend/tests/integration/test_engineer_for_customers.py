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
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "hal"})
                ws.receive_json()
        # Admission still fails closed (no session is registered), but as "retry", not "re-pair":
        # 4401 makes the daemon stop for good, so a plans-lookup blip on the reconnect after a
        # deploy would silently unpair a paying customer's computer (audit AUD-03).
        assert exc.value.code == 1011
        assert await registry.resolve(UserId("hal")) is None


# --- WP11 gap check: Plan E meets the stage-1 Engineer routes (module readiness, 2026-11-18) ---
# admit_turn grants a customer's Engineer per turn; these routes are not turns, so they must ask
# the plan themselves or a paying customer sees the Engineer greyed out and their phone refused.


def _get(container, user: str, path: str):
    from fastapi.testclient import TestClient

    with TestClient(_customer_app(container)) as client:
        return client.get(path, headers={"authorization": f"Bearer {user}"})


def _engineer_usable(response) -> bool:
    assert response.status_code == 200, response.text
    return next(a for a in response.json()["agents"] if a["id"] == "engineer")["usable"]


async def test_agents_lists_the_engineer_usable_for_a_pro_customer_only(container, switch) -> None:
    await _pro(container, "hal")
    assert _engineer_usable(_get(container, "hal", "/api/v3/agents")) is True
    assert _engineer_usable(_get(container, "ida", "/api/v3/agents")) is False  # Free
    switch(False)
    assert _engineer_usable(_get(container, "hal", "/api/v3/agents")) is False


async def test_host_folders_serves_a_pro_customer_and_refuses_free(container, switch) -> None:
    await _pro(container, "jo")
    served = _get(container, "jo", "/api/v3/host/folders")
    assert served.status_code == 200, served.text
    assert served.json()["connected"] is False  # no daemon paired in this test
    assert _get(container, "kai", "/api/v3/host/folders").status_code == 403  # Free
    switch(False)
    assert _get(container, "jo", "/api/v3/host/folders").status_code == 403


async def test_git_init_reaches_the_desktop_check_for_a_pro_customer(container, switch) -> None:
    from fastapi.testclient import TestClient

    await _pro(container, "lee")
    with TestClient(_customer_app(container)) as client:
        def init(user):
            return client.post("/api/v3/host/folders/git-init", json={"path": "/tmp/x"},
                               headers={"authorization": f"Bearer {user}"})
        # Past the entitlement gate: refused only because no computer is connected.
        assert init("lee").status_code == 503
        assert init("max").status_code == 403  # Free


async def test_a_plan_lookup_failure_is_503_not_a_refusal(container, switch, monkeypatch) -> None:
    """Not "not entitled": a database blip must not tell a paying customer they lack the Engineer."""
    await _pro(container, "ned")

    async def broken(_workspace_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(container.plans, "entitlement", broken)
    response = _get(container, "ned", "/api/v3/host/folders")
    assert response.status_code == 503, response.text


# --- The active workspace decides a turn; tell a team member where their Engineer is ---
# Sign-in and pairing accept any workspace with the Engineer, but a turn (and /agents) uses the
# active one. A team member whose Free personal workspace is active would otherwise see the
# Engineer locked with no pointer to the team that has it.


async def _team_member(container, owner: str, member: str, name: str = "Acme") -> str:
    team = await container.tenancy.create_team(owner, name)
    await container.plans.set_plan("sa", team.id, "team", 3)
    _invite, token = await container.tenancy.invite(owner, team.id, f"{member}@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept(member, token)
    return team.id


def _workspace_aware_app(container):
    """Like ``_customer_app``, but resolves ``X-Aura-Workspace`` the way the gateway's own
    authenticator does (``aura_service._build_authenticator``): membership checked, then the
    caller carries that workspace."""
    from aura_core.domain.tenancy import WorkspaceContext

    async def authenticator(request):
        caller = Principal(user_id=UserId(request.headers["authorization"][7:]), email="", role="User",
                           capabilities=(), email_verified=True)
        requested = (request.headers.get("X-Aura-Workspace") or "").strip()
        if requested:
            workspace, membership = await container.tenancy.resolve(str(caller.user_id), requested)
            caller.workspace = WorkspaceContext(
                workspace_id=workspace.id, kind=workspace.kind, role=membership.role)
        return caller

    return build_app(container, authenticator=authenticator)


def _get_in(container, user: str, path: str, workspace: str | None = None):
    from fastapi.testclient import TestClient

    headers = {"authorization": f"Bearer {user}"}
    if workspace:
        headers["X-Aura-Workspace"] = workspace
    with TestClient(_workspace_aware_app(container)) as client:
        return client.get(path, headers=headers)


async def test_agents_names_the_workspaces_that_include_the_engineer(container, switch) -> None:
    team_id = await _team_member(container, "own", "tam")
    personal = _get_in(container, "tam", "/api/v3/agents")
    assert _engineer_usable(personal) is False
    assert personal.json()["engineer_workspaces"] == [
        {"id": team_id, "kind": "team", "name": "Acme", "role": "member"},
    ]
    # With the team active the Engineer is usable, and there is nothing to point at.
    in_team = _get_in(container, "tam", "/api/v3/agents", workspace=team_id)
    assert _engineer_usable(in_team) is True
    assert in_team.json().get("engineer_workspaces", []) == []


async def test_no_pointer_without_an_entitled_workspace_or_with_customers_off(container, switch) -> None:
    free = _get_in(container, "uma", "/api/v3/agents")
    assert free.json().get("engineer_workspaces", []) == []
    await _team_member(container, "own2", "vic", name="Beta")
    switch(False)
    off = _get_in(container, "vic", "/api/v3/agents")
    assert off.json().get("engineer_workspaces", []) == []
