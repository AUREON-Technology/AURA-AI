"""AR-4: service keys -- a non-human caller limited to named routes, its own rate, and no state.

Runs the gateway's real authenticator (``aura_service._build_authenticator``) over the container's
real ``ServiceKeyService`` and in-memory store. Only the *person* identity boundary is stood in
for (``services.jwt_auth`` / ``services.rbac``), exactly as ``test_authenticator_capabilities``
does, so a Super Admin can issue keys through the HTTP API.
"""

from __future__ import annotations

import logging

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.adapters.service_keys import InMemoryServiceKeyStore
from aura_core.api import build_app
from aura_core.domain.errors import AuthenticationError, ValidationError
from aura_core.domain.service_keys import ServiceKey, new_token, parse_token, token_digest
from aura_core.security.service_keys import ServiceKeyService
from aura_service import _build_authenticator

ADMIN = {"authorization": "Bearer person-jwt"}


@pytest.fixture(autouse=True)
def _a_super_admin_person(monkeypatch):
    async def verify(request, credentials):
        if credentials.credentials.startswith("aurask_"):
            raise AssertionError("a service token must never reach the JWT path")
        return {"uid": "sa-1", "email": "sa@example.com"}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "SuperAdmin")


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys)
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _issue(client, **body) -> tuple[dict, dict]:
    response = client.post("/api/v3/admin/service-keys", headers=ADMIN, json={"name": "svc-flame", **body})
    assert response.status_code == 201, response.text
    data = response.json()
    return data["key"], {"authorization": f"Bearer {data['token']}"}


# ---------------------------------------------------------------------------------------- domain


class TestDomain:
    def test_only_a_digest_of_the_token_is_kept(self, clock) -> None:
        key_id, token = new_token()
        assert parse_token(token) == key_id and token.startswith(f"aurask_{key_id}_")
        key = ServiceKey(key_id=key_id, name="svc-flame", digest=token_digest(token), created_at=clock.now(),
                         created_by="sa@example.com")
        assert token not in repr(key) and key.principal_id == "service:svc-flame"
        assert key.routes == frozenset({"POST /api/v3/chat"})  # the default is chat alone

    @pytest.mark.parametrize("overrides", [
        {"routes": frozenset({"DELETE /api/v3/memory/{memory_id}"})},
        {"routes": frozenset()},
        {"name": "Flame Service"},
        {"rate_per_minute": 0},
        {"digest": "plain-token"},
    ])
    def test_shape_rules(self, clock, overrides) -> None:
        fields = dict(key_id="0" * 16, name="svc-flame", digest=token_digest("x"), created_at=clock.now(),
                      created_by="sa@example.com")
        with pytest.raises(ValidationError):
            ServiceKey(**{**fields, **overrides})

    @pytest.mark.parametrize("token", ["aurask_", "aurask_zz_x", "aurask_" + "0" * 16 + "_short"])
    def test_malformed_tokens_name_no_key(self, token) -> None:
        assert parse_token(token) is None

    async def test_a_forged_secret_for_a_real_key_id_is_refused(self, clock, audit) -> None:
        service = ServiceKeyService(store=InMemoryServiceKeyStore(), clock=clock, audit=audit)
        key, token = await service.issue(name="svc-flame", by="sa@example.com")
        assert (await service.authenticate(token)).key_id == key.key_id
        forged = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        with pytest.raises(AuthenticationError):
            await service.authenticate(forged)


# ------------------------------------------------------------------------------------- over HTTP


class TestOverHttp:
    def test_the_token_is_shown_once_and_never_listed(self, client) -> None:
        key, auth = _issue(client)
        token = auth["authorization"][7:]
        listed = client.get("/api/v3/admin/service-keys", headers=ADMIN)
        assert listed.status_code == 200 and [k["key_id"] for k in listed.json()["keys"]] == [key["key_id"]]
        assert token not in listed.text and "sha256:" not in listed.text and "digest" not in listed.text

    def test_a_key_reaches_chat_and_nothing_else(self, client, container) -> None:
        _, auth = _issue(client)
        answered = client.post("/api/v3/chat", headers=auth, json={"message": "Summarise test FLEX-042."})
        assert answered.status_code == 200, answered.text
        for method, path, body in [
            ("POST", "/api/v3/chat/stream", {"message": "hi"}),
            ("GET", "/api/v3/memory", None),
            ("POST", "/api/v3/memory", {"content": "remember me"}),
            ("POST", "/api/v3/knowledge/search", {"query": "x"}),
            ("GET", "/api/v3/admin/service-keys", None),
            ("POST", "/api/v3/admin/service-keys", {"name": "svc-other"}),
            ("GET", "/api/v3/admin/audit", None),
        ]:
            response = client.request(method, path, headers=auth, json=body)
            assert response.status_code == 403, (method, path, response.status_code, response.text)

    async def test_a_service_turn_writes_no_memory_and_runs_no_tools(self, client, container) -> None:
        from aura_core.domain.identifiers import UserId
        from aura_core.domain.memory import MemoryQuery

        echo = container.router.get("echo")
        _, auth = _issue(client)
        response = client.post("/api/v3/chat", headers=auth, json={
            "message": "Remember that I prefer heptane.", "use_memory": True, "use_tools": True, "plan": True,
            "approval_ids": ["apr.forged.call"],
        })
        assert response.status_code == 200, response.text
        assert response.json()["tools_used"] == [] and response.json()["needs_confirmation"] == []
        assert echo.calls and all(not call.tools for call in echo.calls)
        recalled = await container.memory.recall(MemoryQuery(owner_id=UserId("service:svc-flame"), text="heptane", limit=10))
        assert list(recalled) == []
        refused = client.post("/api/v3/chat", headers=auth, json={"message": "hi", "session_id": "s-1"})
        assert refused.status_code == 422

    def test_revocation_takes_effect_on_the_next_request(self, client) -> None:
        key, auth = _issue(client)
        assert client.post("/api/v3/chat", headers=auth, json={"message": "hi"}).status_code == 200
        revoked = client.post(f"/api/v3/admin/service-keys/{key['key_id']}/revoke", headers=ADMIN)
        assert revoked.status_code == 200 and revoked.json()["key"]["revoked_by"] == "sa@example.com"
        assert client.post("/api/v3/chat", headers=auth, json={"message": "hi"}).status_code == 401
        assert client.post("/api/v3/admin/service-keys/0000000000000000/revoke", headers=ADMIN).status_code == 404

    def test_a_key_has_its_own_rate_limit(self, client) -> None:
        _, auth = _issue(client, rate_per_minute=1)
        assert client.post("/api/v3/chat", headers=auth, json={"message": "one"}).status_code == 200
        limited = client.post("/api/v3/chat", headers=auth, json={"message": "two"})
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
        # ...and it is the key's budget, not a person's: an admin is unaffected.
        assert client.get("/api/v3/admin/service-keys", headers=ADMIN).status_code == 200

    async def test_the_audit_trail_names_the_service(self, client, container) -> None:
        key, auth = _issue(client)
        client.post("/api/v3/chat", headers={**auth, "x-correlation-id": "flame-req-1"}, json={"message": "hi"})
        client.get("/api/v3/memory", headers=auth)
        events = await container.audit.query(actor="svc-flame", limit=20)
        by_action = {e.action: e for e in events}
        assert by_action["service.chat"].correlation_id == "flame-req-1"
        assert by_action["service.chat"].actor_role == "Service"
        assert by_action["service.denied"].detail["route"] == "GET /api/v3/memory"
        issued = await container.audit.query(action="service_key.issue", limit=5)
        assert issued and issued[0].actor == "sa@example.com" and issued[0].resource == f"service_key:{key['key_id']}"

    async def test_the_token_never_reaches_logs_or_audit(self, client, container, caplog) -> None:
        with caplog.at_level(logging.DEBUG):
            key, auth = _issue(client)
            token = auth["authorization"][7:]
            client.post("/api/v3/chat", headers=auth, json={"message": "hi"})
            client.get("/api/v3/memory", headers=auth)
            client.post(f"/api/v3/admin/service-keys/{key['key_id']}/revoke", headers=ADMIN)
            client.post("/api/v3/chat", headers=auth, json={"message": "hi"})
        assert token not in caplog.text
        assert all(token not in repr(e.as_dict()) for e in await container.audit.query(limit=200))

    def test_unknown_and_malformed_service_tokens_are_401(self, client) -> None:
        for token in ("aurask_" + "0" * 16 + "_" + "A" * 43, "aurask_garbage"):
            response = client.post("/api/v3/chat", headers={"authorization": f"Bearer {token}"}, json={"message": "hi"})
            assert response.status_code == 401

    def test_a_key_cannot_open_a_websocket(self, client, container) -> None:
        from starlette.websockets import WebSocketDisconnect

        _, auth = _issue(client)
        container.live_voice = {**(getattr(container, "live_voice", None) or {}), "enabled": True}
        with client.websocket_connect("/api/v3/voice/live") as ws:
            ws.send_json({"type": "auth", "token": auth["authorization"][7:]})
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
        assert closed.value.code == 4401

    @pytest.mark.parametrize("body", [
        {"name": "svc-flame", "routes": ["DELETE /api/v3/memory/{memory_id}"]},
        {"name": "svc-flame", "rate_per_minute": 0},
        {"name": "svc-flame", "unexpected": True},
    ])
    def test_issue_validation(self, client, body) -> None:
        assert client.post("/api/v3/admin/service-keys", headers=ADMIN, json=body).status_code == 422
