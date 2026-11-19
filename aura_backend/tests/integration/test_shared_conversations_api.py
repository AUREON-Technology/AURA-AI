"""SaaS P1-F1: shared team chats over HTTP."""

from __future__ import annotations

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_service import _build_authenticator

MSGS = [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "hi"}]


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": True}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys, tenancy=container.tenancy)
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str) -> dict:
    return {"authorization": f"Bearer {person}"}


def test_share_list_read_unshare(client, container) -> None:
    import asyncio

    ws = client.post("/api/v3/workspaces", headers=_as("own"), json={"name": "Acme"}).json()["id"]
    _i, token = asyncio.run(container.tenancy.invite("own", ws, "amy@example.com", "member"))
    asyncio.run(container.tenancy.accept("amy", token))
    base = f"/api/v3/workspaces/{ws}/shared-conversations"
    created = client.post(base, headers=_as("amy"), json={"source_id": "chat-1", "title": "Deploys", "messages": MSGS})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    listed = client.get(base, headers=_as("own")).json()["conversations"]
    assert [c["id"] for c in listed] == [sid] and "messages" not in listed[0] and listed[0]["shared_by"] == "amy"
    assert client.get(f"{base}/{sid}", headers=_as("own")).json()["messages"] == MSGS
    assert client.get(base, headers=_as("eve")).status_code == 403
    assert client.post(base, headers=_as("amy"), json={"source_id": "c", "title": "t",
                                                       "messages": [{"role": "system", "text": "x"}]}).status_code == 422
    assert client.delete(f"{base}/{sid}", headers=_as("amy")).status_code == 204
    assert client.get(f"{base}/{sid}", headers=_as("own")).status_code == 404
