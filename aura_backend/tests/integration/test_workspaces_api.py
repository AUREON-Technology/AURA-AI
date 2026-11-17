"""SaaS P1-A: the ``/api/v3/workspaces`` routes, through the real app and authenticator."""

from __future__ import annotations

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_service import _build_authenticator


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        # Plan B: a token text starting "unverified-" stands for an email/password account whose
        # address is not verified yet; everyone else signed in with a verified address.
        return {"uid": person, "email": f"{person}@example.com",
                "email_verified": not person.startswith("unverified-")}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    authenticator = _build_authenticator(
        container.settings, service_keys=container.service_keys, tenancy=container.tenancy
    )
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str) -> dict:
    return {"authorization": f"Bearer {person}"}


def _team(client, owner="alice", name="Acme") -> str:
    response = client.post("/api/v3/workspaces", headers=_as(owner), json={"name": name})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "team" and body["role"] == "owner" and body["name"] == name
    return body["id"]


def _join(client, ws: str, person: str, role="member", by="alice") -> None:
    invited = client.post(
        f"/api/v3/workspaces/{ws}/invites", headers=_as(by), json={"email": f"{person}@example.com", "role": role}
    )
    assert invited.status_code == 201, invited.text
    accepted = client.post("/api/v3/workspaces/invites/accept", headers=_as(person), json={"token": invited.json()["token"]})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["workspace"]["id"] == ws and accepted.json()["role"] == role


def _roles(client, ws, person="alice") -> dict:
    response = client.get(f"/api/v3/workspaces/{ws}/members", headers=_as(person))
    assert response.status_code == 200, response.text
    return {m["user_id"]: m["role"] for m in response.json()["members"]}


def test_list_includes_personal_and_teams(client) -> None:
    listed = client.get("/api/v3/workspaces", headers=_as("alice")).json()["workspaces"]
    assert [w["kind"] for w in listed] == ["personal"] and listed[0]["role"] == "owner"
    ws = _team(client)
    kinds = {w["id"]: w["kind"] for w in client.get("/api/v3/workspaces", headers=_as("alice")).json()["workspaces"]}
    assert kinds[ws] == "team" and sorted(kinds.values()) == ["personal", "team"]


def test_invite_accept_members_roles_and_transfer(client) -> None:
    ws = _team(client)
    _join(client, ws, "bob")
    assert _roles(client, ws) == {"alice": "owner", "bob": "member"}
    assert client.patch(f"/api/v3/workspaces/{ws}/members/bob", headers=_as("alice"), json={"role": "admin"}).status_code == 204
    assert _roles(client, ws, "bob")["bob"] == "admin"
    assert client.post(f"/api/v3/workspaces/{ws}/transfer", headers=_as("alice"), json={"user_id": "bob"}).status_code == 204
    assert _roles(client, ws) == {"alice": "admin", "bob": "owner"}


def test_remove_and_leave(client) -> None:
    ws = _team(client)
    _join(client, ws, "bob")
    _join(client, ws, "carol")
    assert client.delete(f"/api/v3/workspaces/{ws}/members/bob", headers=_as("alice")).status_code == 204
    assert client.delete(f"/api/v3/workspaces/{ws}/members/carol", headers=_as("carol")).status_code == 204
    assert _roles(client, ws) == {"alice": "owner"}
    assert client.delete(f"/api/v3/workspaces/{ws}/members/alice", headers=_as("alice")).status_code == 409


def test_list_and_revoke_invites(client) -> None:
    ws = _team(client)
    invited = client.post(f"/api/v3/workspaces/{ws}/invites", headers=_as("alice"), json={"email": "x@example.com", "role": "member"})
    invite_id = invited.json()["invite"]["invite_id"]
    listed = client.get(f"/api/v3/workspaces/{ws}/invites", headers=_as("alice")).json()["invites"]
    assert [i["invite_id"] for i in listed] == [invite_id] and listed[0]["email"] == "x@example.com"
    assert client.delete(f"/api/v3/workspaces/{ws}/invites/{invite_id}", headers=_as("alice")).status_code == 204
    assert client.get(f"/api/v3/workspaces/{ws}/invites", headers=_as("alice")).json()["invites"] == []
    accepted = client.post("/api/v3/workspaces/invites/accept", headers=_as("x"), json={"token": invited.json()["token"]})
    assert accepted.status_code == 409


def test_delete_team(client) -> None:
    ws = _team(client)
    _join(client, ws, "bob", role="admin")
    assert client.delete(f"/api/v3/workspaces/{ws}", headers=_as("bob")).status_code == 403
    assert client.delete(f"/api/v3/workspaces/{ws}", headers=_as("alice")).status_code == 204
    assert client.get(f"/api/v3/workspaces/{ws}/members", headers=_as("alice")).status_code == 403


def test_invite_response_never_contains_digest(client) -> None:
    ws = _team(client)
    invited = client.post(f"/api/v3/workspaces/{ws}/invites", headers=_as("alice"), json={"email": "x@example.com", "role": "member"})
    assert invited.status_code == 201
    assert "digest" not in invited.json()["invite"]
    assert invited.headers.get("cache-control") == "no-store"
    listed = client.get(f"/api/v3/workspaces/{ws}/invites", headers=_as("alice")).json()["invites"]
    assert all("digest" not in i for i in listed)
    assert invited.json()["token"] not in str(listed)


def test_member_cannot_list_invites(client) -> None:
    ws = _team(client)
    _join(client, ws, "bob")
    assert client.get(f"/api/v3/workspaces/{ws}/invites", headers=_as("bob")).status_code == 403
    assert client.post(f"/api/v3/workspaces/{ws}/invites", headers=_as("bob"), json={"email": "y@example.com", "role": "member"}).status_code == 403


def test_stranger_gets_same_403_for_real_and_fake_workspace(client) -> None:
    ws = _team(client)
    real = client.get(f"/api/v3/workspaces/{ws}/members", headers=_as("mallory"))
    fake = client.get(f"/api/v3/workspaces/{'0' * 32}/members", headers=_as("mallory"))
    assert real.status_code == fake.status_code == 403
    assert real.json() == fake.json()


def test_bad_input_is_422(client) -> None:
    ws = _team(client)
    assert client.post("/api/v3/workspaces", headers=_as("alice"), json={"name": ""}).status_code == 422
    assert client.post(f"/api/v3/workspaces/{ws}/invites", headers=_as("alice"), json={"email": "not-an-email", "role": "member"}).status_code == 422
    assert client.post(f"/api/v3/workspaces/{ws}/invites", headers=_as("alice"), json={"email": "a@example.com", "role": "owner"}).status_code == 422
    assert client.post("/api/v3/workspaces/invites/accept", headers=_as("bob"), json={"token": "nope"}).status_code == 422


def test_accept_rate_limited(client) -> None:
    statuses = [
        client.post("/api/v3/workspaces/invites/accept", headers=_as("guesser"), json={"token": f"auraiv_{'0' * 16}_{'A' * 43}"}).status_code
        for _ in range(25)
    ]
    assert statuses[0] == 409 and 429 in statuses


async def test_service_key_refused_on_workspace_routes(client, container) -> None:
    _key, token = await container.service_keys.issue(name="svc-test", by="sa@example.com")
    response = client.get("/api/v3/workspaces", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_unverified_cannot_create_team(client) -> None:
    response = client.post("/api/v3/workspaces", headers=_as("unverified-dana"), json={"name": "Dana Co"})
    assert response.status_code == 403
    assert "verify your email" in response.text


def test_unverified_cannot_accept_invite(client) -> None:
    ws = _team(client)
    invited = client.post(
        f"/api/v3/workspaces/{ws}/invites", headers=_as("alice"), json={"email": "dana@example.com", "role": "member"}
    )
    accepted = client.post(
        "/api/v3/workspaces/invites/accept", headers=_as("unverified-dana"), json={"token": invited.json()["token"]}
    )
    assert accepted.status_code == 403 and "verify your email" in accepted.text
    # The invite stays usable once the address is verified.
    assert client.post("/api/v3/workspaces/invites/accept", headers=_as("dana"), json={"token": invited.json()["token"]}).status_code == 200


def test_unverified_can_list_personal_workspace(client) -> None:
    response = client.get("/api/v3/workspaces", headers=_as("unverified-dana"))
    assert response.status_code == 200
    assert [w["kind"] for w in response.json()["workspaces"]] == ["personal"]
