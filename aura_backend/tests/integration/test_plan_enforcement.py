"""SaaS P1-C: the gateway enforces a workspace's plan -- messages, the Engineer agent, storage.

Real app, real authenticator, real TenancyService/PlanService on the in-memory stack with the echo
provider. The catalogue is shrunk per test (2 messages, 300 bytes) so limits are reached quickly;
only the person identity boundary (``services.jwt_auth`` / ``services.rbac``) is stood in for.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.domain.errors import AllProvidersFailedError
from aura_core.domain.plans import DEFAULT_CATALOGUE
from aura_core.domain.tenancy import WorkspaceRole
from aura_service import _build_authenticator

ROLES = {"op": "Developer"}


@pytest.fixture(autouse=True)
def _people(monkeypatch):
    async def verify(request, credentials):
        person = credentials.credentials
        return {"uid": person, "email": f"{person}@example.com", "email_verified": True}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: ROLES.get(claims.get("uid"), "User"))


@pytest.fixture
def small_plans(container):
    free = replace(DEFAULT_CATALOGUE["free"], messages_per_month=2, storage_bytes=300)
    team = replace(DEFAULT_CATALOGUE["team"], messages_per_month=1)
    container.plans.catalogue = MappingProxyType({**DEFAULT_CATALOGUE, "free": free, "team": team})
    return container.plans


@pytest.fixture
def client(container, small_plans):
    from fastapi.testclient import TestClient

    from aura_core.api import build_app

    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys, tenancy=container.tenancy)
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(person: str, workspace: str | None = None) -> dict:
    headers = {"authorization": f"Bearer {person}"}
    if workspace:
        headers["x-aura-workspace"] = workspace
    return headers


def _chat(client, headers, agent: str | None = None):
    body = {"message": "hello there", "use_tools": False}
    if agent:
        body["agent"] = agent
    return client.post("/api/v3/chat", headers=headers, json=body)


async def _used(container, person: str) -> int:
    ws = (await container.tenancy.ensure_personal(person)).id
    return (await container.plans.usage(ws))["messages_used"]


async def test_free_workspace_refused_after_limit_402_body(client, container) -> None:
    assert _chat(client, _as("ann")).status_code == 200
    assert _chat(client, _as("ann")).status_code == 200
    refused = _chat(client, _as("ann"))
    assert refused.status_code == 402
    body = refused.json()
    assert body["error"] == "plan_limit"
    assert body["details"]["limit"] == "messages" and body["details"]["allowed"] == 2
    assert body["details"]["resets_at"]


async def test_failed_turn_not_counted(client, container, monkeypatch) -> None:
    async def broken(turn):
        raise AllProvidersFailedError("every provider is down")

    monkeypatch.setattr(container.kernel, "run", broken)
    assert _chat(client, _as("ben")).status_code >= 500
    assert await _used(container, "ben") == 0


async def test_stream_counts_once_and_releases_on_error(client, container, monkeypatch) -> None:
    with client.stream("POST", "/api/v3/chat/stream", headers=_as("cat"), json={"message": "hi", "use_tools": False}) as r:
        assert r.status_code == 200
        "".join(r.iter_text())
    assert await _used(container, "cat") == 1

    async def failing_stream(turn):
        yield {"type": "error", "message": "provider down"}

    monkeypatch.setattr(container.kernel, "stream", failing_stream)
    with client.stream("POST", "/api/v3/chat/stream", headers=_as("cat"), json={"message": "hi", "use_tools": False}) as r:
        "".join(r.iter_text())
    assert await _used(container, "cat") == 1


async def test_team_usage_pooled_across_members(client, container, small_plans) -> None:
    team = await container.tenancy.create_team("dan", "Dan Co")
    _invite, token = await container.tenancy.invite("dan", team.id, "eve@example.com", WorkspaceRole.MEMBER)
    await container.tenancy.accept("eve", token)
    await small_plans.set_plan("sa", team.id, "team", 2)   # 1 message per seat x 2 seats
    assert _chat(client, _as("dan", team.id)).status_code == 200
    assert _chat(client, _as("eve", team.id)).status_code == 200
    assert _chat(client, _as("eve", team.id)).status_code == 402


async def test_personal_and_team_metered_separately(client, container) -> None:
    team = await container.tenancy.create_team("fay", "Fay Co")
    for _ in range(2):
        assert _chat(client, _as("fay", team.id)).status_code == 200
    assert _chat(client, _as("fay", team.id)).status_code == 402
    assert _chat(client, _as("fay")).status_code == 200


async def test_engineer_turn_refused_on_free(client, container) -> None:
    refused = _chat(client, _as("gus"), agent="engineer")
    assert refused.status_code == 402 and refused.json()["details"]["limit"] == "engineer"
    assert await _used(container, "gus") == 0


async def test_ingest_storage_limit_and_duplicate_not_double_counted(client, container) -> None:
    text = "Q" * 200
    ok = client.post("/api/v3/knowledge/documents", headers=_as("hal"), json={"title": "a", "content": text})
    assert ok.status_code == 200
    again = client.post("/api/v3/knowledge/documents", headers=_as("hal"), json={"title": "a", "content": text})
    assert again.status_code == 200
    ws = (await container.tenancy.ensure_personal("hal")).id
    used = (await container.plans.usage(ws))["storage_used"]
    assert 0 < used <= 300
    full = client.post("/api/v3/knowledge/documents", headers=_as("hal"), json={"title": "b", "content": "Z" * 200})
    assert full.status_code == 402 and full.json()["details"]["limit"] == "storage"
    assert (await container.plans.usage(ws))["storage_used"] == used


async def test_delete_frees_storage(client, container) -> None:
    ok = client.post("/api/v3/knowledge/documents", headers=_as("ivy"), json={"title": "a", "content": "K" * 200})
    document_id = ok.json()["document_id"]
    ws = (await container.tenancy.ensure_personal("ivy")).id
    assert (await container.plans.usage(ws))["storage_used"] > 0
    assert client.delete(f"/api/v3/knowledge/documents/{document_id}", headers=_as("ivy")).status_code in (200, 204)
    assert (await container.plans.usage(ws))["storage_used"] == 0


async def test_operator_unmetered(client, container) -> None:
    for _ in range(4):
        assert _chat(client, _as("op")).status_code == 200
    assert await _used(container, "op") == 0
