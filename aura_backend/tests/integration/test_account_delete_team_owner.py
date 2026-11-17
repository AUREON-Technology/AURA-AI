"""SaaS P1-F1: deleting your account while you own a team is refused until the team is handed
over or deleted (spec §5.1). Nothing is erased by a refused call."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.jwt_auth as jwt_auth
from api.account import router
from aura_core.domain.identifiers import UserId
from aura_core.domain.tenancy import WorkspaceRole


@pytest.fixture(autouse=True)
def _local_sessions(monkeypatch):
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)
    yield
    jwt_auth._local_refresh_store.clear()
    jwt_auth._local_erased_accounts.clear()


@pytest.fixture
def client(container):
    app = FastAPI()
    app.include_router(router)
    app.state.aura = container
    with TestClient(app) as c:
        yield c


def _delete(client, uid: str):
    token = jwt_auth.issue_token_pair({"uid": uid, "email": f"{uid}@example.com"})["access_token"]
    return client.post("/api/v3/account/delete", json={"confirm": "DELETE"}, headers={"Authorization": f"Bearer {token}"})


async def _team(container, owner="own", member="amy"):
    ws = await container.tenancy.create_team(owner, "Acme")
    await container.plans.set_plan("sa", ws.id, "team", 5)
    _i, token = await container.tenancy.invite(owner, ws.id, f"{member}@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept(member, token)
    return ws


async def test_owner_of_a_team_gets_409_naming_it(client, container) -> None:
    ws = await _team(container)
    await container.memory.remember(owner_id=UserId("own"), content="keep me", subject="s")
    response = _delete(client, "own")
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "owns_team_workspaces" and body["workspaces"] == [{"id": ws.id, "name": "Acme"}]
    assert len(await container.memory._store.list_for_owner(UserId("own"), include_superseded=True)) == 1


async def test_after_transfer_deletion_proceeds(client, container) -> None:
    ws = await _team(container)
    await container.tenancy.transfer("own", ws.id, "amy")
    response = _delete(client, "own")
    assert response.status_code == 200 and response.json()["forgotten"] is True


async def test_after_deleting_the_team_deletion_proceeds(client, container) -> None:
    ws = await _team(container)
    await container.tenancy.delete_team("own", ws.id)
    assert _delete(client, "own").status_code == 200


async def test_members_and_personal_only_users_unaffected(client, container) -> None:
    await _team(container)
    assert _delete(client, "amy").status_code == 200
    await container.tenancy.ensure_personal("solo")
    assert _delete(client, "solo").status_code == 200
