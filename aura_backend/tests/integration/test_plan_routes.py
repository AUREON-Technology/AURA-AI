"""SaaS P1-C: reading a workspace's plan and usage; an operator setting it by hand."""

from __future__ import annotations

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_service import _build_authenticator

ROLES = {"sa": "SuperAdmin", "admin": "Admin"}


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": True}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: ROLES.get(claims.get("uid"), "User"))


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys, tenancy=container.tenancy)
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str) -> dict:
    return {"authorization": f"Bearer {person}"}


def _team(client, owner="ann") -> str:
    return client.post("/api/v3/workspaces", headers=_as(owner), json={"name": "Acme"}).json()["id"]


def test_member_reads_plan_and_usage(client) -> None:
    ws = _team(client)
    response = client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("ann"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"] == "free" and body["messages_used"] == 0
    assert body["messages_allowed"] == 50 and body["seat_limit"] == 3 and body["engineer"] is False
    assert body["resets_at"]


def test_stranger_403(client) -> None:
    ws = _team(client)
    assert client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("mallory")).status_code == 403


def test_superadmin_sets_plan(client) -> None:
    ws = _team(client)
    response = client.put(f"/api/v3/admin/workspaces/{ws}/plan", headers=_as("sa"), json={"plan": "team", "seats": 5})
    assert response.status_code == 200, response.text
    assert response.json()["entitlement"] == {"plan": "team", "seats": 5, "source": "manual"}
    plan = client.get(f"/api/v3/workspaces/{ws}/plan", headers=_as("ann")).json()
    assert plan["plan"] == "team" and plan["seat_limit"] == 5 and plan["engineer"] is True


def test_non_superadmin_403(client) -> None:
    ws = _team(client)
    for person in ("admin", "ann"):
        response = client.put(f"/api/v3/admin/workspaces/{ws}/plan", headers=_as(person), json={"plan": "team", "seats": 5})
        assert response.status_code == 403


def test_set_plan_rejects_wrong_kind_422(client) -> None:
    ws = _team(client)
    response = client.put(f"/api/v3/admin/workspaces/{ws}/plan", headers=_as("sa"), json={"plan": "pro", "seats": 1})
    assert response.status_code == 422
