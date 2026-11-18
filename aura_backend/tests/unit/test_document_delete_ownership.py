"""INT-111: ``DELETE /api/v3/knowledge/documents/{id}`` refuses a document the caller does not own.

The route answered 200 ``{"deleted": true}`` for someone else's (or a global) document: the engine
removed nothing, but the client was told it had. Ownership is checked against the stored owner
before deleting -- not inferred from "0 chunks removed", which a fully quarantined document of
the caller's own legitimately reports.
"""

from __future__ import annotations

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.domain.tenancy import WorkspaceContext, WorkspaceKind, WorkspaceRole


@pytest.fixture
def people():
    return {}


@pytest.fixture
def client(container, people):
    from fastapi.testclient import TestClient

    async def authenticator(request):
        who = request.headers.get("authorization", "")[7:]
        return people.get(who) or Principal(user_id=UserId(who), role="User")

    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str) -> dict:
    return {"authorization": f"Bearer {person}"}


def _ingest(client, person: str, doc: str) -> None:
    response = client.post("/api/v3/knowledge/documents", headers=_as(person),
                           json={"title": doc, "content": f"{doc} body text", "document_id": doc})
    assert response.status_code == 200, response.text


async def test_another_owners_document_is_404_and_survives(client, container) -> None:
    _ingest(client, "ann", "doc-ann")
    refused = client.delete("/api/v3/knowledge/documents/doc-ann", headers=_as("bob"))
    assert refused.status_code == 404
    assert refused.json()["error"] == "not_found"
    assert await container.documents.get(DocumentId("doc-ann")) is not None


async def test_a_global_document_is_404_and_survives(client, container) -> None:
    await container.knowledge.ingest(content="shared handbook", title="handbook",
                                     document_id=DocumentId("doc-global"), owner_id=None)
    refused = client.delete("/api/v3/knowledge/documents/doc-global", headers=_as("bob"))
    assert refused.status_code == 404
    assert await container.documents.get(DocumentId("doc-global")) is not None


async def test_an_unknown_document_is_404(client) -> None:
    assert client.delete("/api/v3/knowledge/documents/nope", headers=_as("bob")).status_code == 404


async def test_the_owner_still_deletes_their_document(client, container) -> None:
    _ingest(client, "cat", "doc-cat")
    response = client.delete("/api/v3/knowledge/documents/doc-cat", headers=_as("cat"))
    assert response.status_code == 200
    assert response.json()["deleted"] is True


async def test_a_team_owner_deletes_the_teams_document(client, container, people) -> None:
    team = await container.tenancy.create_team("dee", "Dee Co")
    people["dee"] = Principal(
        user_id=UserId("dee"), role="User",
        workspace=WorkspaceContext(team.id, WorkspaceKind.TEAM, WorkspaceRole.OWNER),
    )
    _ingest(client, "dee", "doc-team")
    stored = await container.documents.get(DocumentId("doc-team"))
    assert stored.owner_id == people["dee"].knowledge_owner != UserId("dee")
    response = client.delete("/api/v3/knowledge/documents/doc-team", headers=_as("dee"))
    assert response.status_code == 200
