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


async def test_stream_error_after_a_token_still_counts(client, container, monkeypatch) -> None:
    """F6: the message counts once the first token frame is sent, even if the turn fails after."""
    async def token_then_error(turn):
        yield {"type": "token", "text": "part of an answer"}
        yield {"type": "error", "error": "provider_unavailable", "message": "provider down"}

    monkeypatch.setattr(container.kernel, "stream", token_then_error)
    with client.stream("POST", "/api/v3/chat/stream", headers=_as("cy"), json={"message": "hi", "use_tools": False}) as r:
        "".join(r.iter_text())
    assert await _used(container, "cy") == 1


def _drive_stream_then_disconnect(client, headers: dict, *, leave_after: bytes = b"event: token") -> list[bytes]:
    """Drive ``/chat/stream`` over raw ASGI on the test client's own loop: read until a frame
    containing ``leave_after`` (the first token by default), then report the client gone (``http.disconnect``) -- what a browser closing the
    tab after reading the answer looks like. The test client itself buffers a whole response, so
    it cannot stop part-way."""
    import asyncio
    import json

    body = json.dumps({"message": "hi", "use_tools": False}).encode()
    chunks: list[bytes] = []

    async def drive() -> None:
        seen = asyncio.Event()
        sent_request = False

        async def receive():
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": body, "more_body": False}
            await seen.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if leave_after in message.get("body", b""):
                    seen.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/api/v3/chat/stream", "raw_path": b"/api/v3/chat/stream",
            "query_string": b"", "root_path": "", "client": ("testclient", 50000),
            "server": ("testserver", 80), "state": {},
            "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())]
            + [(k.encode(), v.encode()) for k, v in headers.items()],
        }
        await client.app(scope, receive, send)
        # The producer task is cancelled, not awaited; let its `finally` run.
        for _ in range(20):
            await asyncio.sleep(0.01)

    client.portal.call(drive)
    return chunks


async def test_stream_counts_when_the_client_leaves_after_the_answer(client, container, monkeypatch) -> None:
    """F6: reading every token and disconnecting before `done` does not give the message back."""
    import asyncio

    async def answer_then_hang(turn):
        yield {"type": "token", "text": "the whole answer"}
        await asyncio.Event().wait()   # `done` never comes: the client leaves first

    monkeypatch.setattr(container.kernel, "stream", answer_then_hang)
    chunks = _drive_stream_then_disconnect(client, _as("cole"))
    assert any(b"event: token" in c for c in chunks)
    assert not any(b"event: done" in c for c in chunks)
    assert await _used(container, "cole") == 1


async def test_stream_released_when_the_client_leaves_before_any_token(client, container, monkeypatch) -> None:
    """F6: a client gone before anything was produced is not charged."""
    import asyncio

    async def citations_then_hang(turn):
        yield {"type": "citations", "citations": []}
        await asyncio.Event().wait()

    monkeypatch.setattr(container.kernel, "stream", citations_then_hang)
    _drive_stream_then_disconnect(client, _as("cass"), leave_after=b"event: citations")
    assert await _used(container, "cass") == 0


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


async def test_new_version_settles_against_the_previous_one(client, container) -> None:
    """Review C#1: editing a document charges only the difference, and deleting it frees all of it."""
    first = client.post("/api/v3/knowledge/documents", headers=_as("jo"), json={"title": "a", "content": "A" * 50, "document_id": "doc-jo"})
    assert first.status_code == 200
    second = client.post("/api/v3/knowledge/documents", headers=_as("jo"), json={"title": "a", "content": "B" * 60, "document_id": "doc-jo"})
    assert second.status_code == 200 and second.json()["version"] == 2
    ws = (await container.tenancy.ensure_personal("jo")).id
    assert (await container.plans.usage(ws))["storage_used"] == 60
    client.delete("/api/v3/knowledge/documents/doc-jo", headers=_as("jo"))
    assert (await container.plans.usage(ws))["storage_used"] == 0


async def test_engineer_capability_dropped_when_the_plan_lacks_it(client, container, monkeypatch) -> None:
    """Review C#2: the gate is the workspace's plan, not the persona name -- an orchestrator turn
    from a Free workspace must not carry the engineer_agent capability even if a role grants it."""
    seen = {}
    original = container.kernel.run

    async def spy(turn):
        seen["capabilities"] = tuple(turn.capabilities)
        return await original(turn)

    monkeypatch.setattr(container.kernel, "run", spy)
    from aura_core.api import build_app

    async def authenticator(request):
        from aura_core.api import Principal
        from aura_core.domain.identifiers import UserId

        return Principal(user_id=UserId("kim"), role="User", capabilities=("engineer_agent",), email_verified=True)

    from fastapi.testclient import TestClient

    with TestClient(build_app(container, authenticator=authenticator)) as app:
        assert app.post("/api/v3/chat", headers=_as("kim"), json={"message": "hi", "use_tools": False}).status_code == 200
    assert "engineer_agent" not in seen["capabilities"]
    ws = (await container.tenancy.ensure_personal("kim")).id
    await container.plans.set_plan("sa", ws, "pro", 1)
    with TestClient(build_app(container, authenticator=authenticator)) as app:
        app.post("/api/v3/chat", headers=_as("kim"), json={"message": "hi", "use_tools": False})
    assert "engineer_agent" in seen["capabilities"]


async def test_full_team_refused_as_plan_limit(client, container) -> None:
    """Review C#3: a full team is a plan limit (402, limit=seats), which the app words."""
    team = client.post("/api/v3/workspaces", headers=_as("lou"), json={"name": "Lou Co"}).json()["id"]
    for person in ("m1", "m2"):
        invite = client.post(f"/api/v3/workspaces/{team}/invites", headers=_as("lou"), json={"email": f"{person}@example.com"})
        assert invite.status_code == 201
    full = client.post(f"/api/v3/workspaces/{team}/invites", headers=_as("lou"), json={"email": "m3@example.com"})
    assert full.status_code == 402
    assert full.json()["error"] == "plan_limit" and full.json()["details"]["limit"] == "seats"


async def test_delete_all_frees_the_storage_every_document_counted(client, container) -> None:
    """Clearing the corpus in one call gives back exactly what each document counted, and the
    summary reports the same meter the plan enforces."""
    for name, filler in (("one", "M" * 100), ("two", "N" * 100)):
        assert client.post("/api/v3/knowledge/documents", headers=_as("kit"), json={"title": name, "content": filler}).status_code == 200
    ws = (await container.tenancy.ensure_personal("kit")).id
    assert (await container.plans.usage(ws))["storage_used"] == 200
    assert client.get("/api/v3/knowledge", headers=_as("kit")).json()["storage"]["used_bytes"] == 200

    done = client.delete("/api/v3/knowledge/documents", params={"confirm": "true"}, headers=_as("kit"))

    assert done.status_code == 200 and done.json()["deleted"] == 2
    assert (await container.plans.usage(ws))["storage_used"] == 0
    assert client.get("/api/v3/knowledge", headers=_as("kit")).json()["storage"]["used_bytes"] == 0
