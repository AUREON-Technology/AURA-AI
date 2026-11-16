"""SaaS P1-A: what one workspace's members can see of another's (ADR-034, spec §9).

Driven through the real HTTP app, authenticator and TenancyService on the in-memory stack. Two
people: alice owns team T1 and bob is a member of it; bob alone owns team T2. Only the person
identity boundary (``services.jwt_auth`` / ``services.rbac``) is stood in for.
"""

from __future__ import annotations

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.domain.tenancy import WorkspaceRole
from aura_service import _build_authenticator

TEAM_DOC = "The Zephyrine procurement threshold is 4200 credits per quarter for the platform team."
PERSONAL_DOC = "Bob's private Quillfeather journal notes about his allotment and tomato seedlings."


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        return {"uid": credentials.credentials, "email": f"{credentials.credentials}@example.com"}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


@pytest.fixture
async def teams(container):
    await container.startup()
    t1 = await container.tenancy.create_team("alice", "T1")
    _invite, token = await container.tenancy.invite("alice", t1.id, "bob@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept("bob", token)
    t2 = await container.tenancy.create_team("bob", "T2")
    return t1.id, t2.id


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    authenticator = _build_authenticator(
        container.settings, service_keys=container.service_keys, tenancy=container.tenancy
    )
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str, workspace: str | None = None) -> dict:
    headers = {"authorization": f"Bearer {person}"}
    if workspace:
        headers["x-aura-workspace"] = workspace
    return headers


def _ingest(client, headers, title, content):
    return client.post("/api/v3/knowledge/documents", headers=headers, json={"title": title, "content": content})


def _found(client, headers, query) -> bool:
    response = client.post("/api/v3/knowledge/search", headers=headers, json={"query": query, "limit": 5})
    assert response.status_code == 200, response.text
    return any("Zephyrine" in r["content"] or "Quillfeather" in r["content"] for r in response.json()["results"])


def test_team_doc_visible_to_team_member(client, teams) -> None:
    t1, _t2 = teams
    assert _ingest(client, _as("alice", t1), "Procurement", TEAM_DOC).status_code == 200
    assert _found(client, _as("bob", t1), "Zephyrine procurement threshold")


def test_team_doc_invisible_in_personal_and_other_team(client, teams) -> None:
    t1, t2 = teams
    assert _ingest(client, _as("alice", t1), "Procurement", TEAM_DOC).status_code == 200
    assert not _found(client, _as("bob"), "Zephyrine procurement threshold")
    assert not _found(client, _as("bob", t2), "Zephyrine procurement threshold")
    assert not _found(client, _as("alice"), "Zephyrine procurement threshold")


def test_personal_doc_invisible_in_team(client, teams) -> None:
    t1, _t2 = teams
    assert _ingest(client, _as("bob"), "Journal", PERSONAL_DOC).status_code == 200
    assert _found(client, _as("bob"), "Quillfeather journal allotment")
    assert not _found(client, _as("bob", t1), "Quillfeather journal allotment")
    assert not _found(client, _as("alice", t1), "Quillfeather journal allotment")


def test_member_cannot_ingest_into_team(client, teams) -> None:
    t1, _t2 = teams
    response = _ingest(client, _as("bob", t1), "Sneaky", TEAM_DOC)
    assert response.status_code == 403


def test_team_document_list_is_the_teams(client, teams) -> None:
    t1, _t2 = teams
    _ingest(client, _as("alice", t1), "Procurement", TEAM_DOC)
    _ingest(client, _as("bob"), "Journal", PERSONAL_DOC)
    titles = lambda h: {d["title"] for d in client.get("/api/v3/knowledge/documents", headers=h).json()["documents"]}
    assert titles(_as("bob", t1)) == {"Procurement"}
    assert "Procurement" not in titles(_as("bob"))


async def test_removed_member_loses_team_docs_immediately(client, container, teams) -> None:
    t1, _t2 = teams
    _ingest(client, _as("alice", t1), "Procurement", TEAM_DOC)
    assert _found(client, _as("bob", t1), "Zephyrine procurement threshold")
    await container.tenancy.remove("alice", t1, "bob")
    after = client.post(
        "/api/v3/knowledge/search", headers=_as("bob", t1), json={"query": "Zephyrine", "limit": 5}
    )
    assert after.status_code == 403


def test_chat_turn_retrieves_team_scope_but_user_memory(client, container, teams) -> None:
    t1, _t2 = teams
    seen = {}
    original_retrieve = container.knowledge.retrieve
    original_recall = container.memory.recall

    async def retrieve(query, *, owner_id=None, **kwargs):
        seen["knowledge"] = owner_id
        return await original_retrieve(query, owner_id=owner_id, **kwargs)

    async def recall(query):
        seen["memory"] = query.owner_id
        return await original_recall(query)

    container.knowledge.retrieve = retrieve
    container.memory.recall = recall
    response = client.post(
        "/api/v3/chat", headers=_as("bob", t1), json={"message": "What is the Zephyrine procurement threshold?"}
    )
    assert response.status_code == 200, response.text
    assert str(seen["knowledge"]) == f"workspace:{t1}"
    assert str(seen["memory"]) == "bob"
