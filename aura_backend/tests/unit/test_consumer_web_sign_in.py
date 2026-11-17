"""SaaS P1-B: web customers sign in without device attestation, and never as an operator.

``POST /api/v3/auth/consumer/token`` is the browser door for *customers*. Unlike ``/web/token``
(operators only) it accepts anyone with a verified Google or Firebase ID token -- so the session it
issues is capped at the ``User`` platform role at issue, on every refresh, and in the gateway's own
per-request role resolution. Otherwise an operator's address (or a uid in roles.json) would turn a
customer web session into a console session with no attestation at all.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.jwt_auth as jwt_auth
from api.v2.auth import router

OPERATOR = {
    "uid": "op-1", "email": "boss@example.com", "name": "Boss",
    "email_verified": True, "sign_in_provider": "google.com",
}


@pytest.fixture(autouse=True)
def _operator_lists(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "boss@example.com")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """Every TestClient shares one client address, so the bucket is cleared before *and* after
    each test -- a full bucket must not leak into another file's sign-in tests."""
    from api.v2.auth import _reset_consumer_rate_limit

    _reset_consumer_rate_limit()
    yield
    _reset_consumer_rate_limit()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


async def _operator_identity(request, authorization):
    return dict(OPERATOR)


def _claims(access_token: str) -> dict:
    return jwt_auth._decode_access_token(access_token)


def _sign_in(client: TestClient, path: str = "/api/v3/auth/consumer/token"):
    return client.post(path, headers={"Authorization": "Bearer firebase-id"})


def test_operator_email_capped_to_user() -> None:
    with patch("api.v2.auth.verify_identity_token", new=_operator_identity):
        response = _sign_in(_client())
    assert response.status_code == 200, response.text
    claims = _claims(response.json()["access_token"])
    assert claims["role"] == "User"
    assert claims["channel"] == "consumer_web"


def test_cap_survives_refresh() -> None:
    client = _client()
    with patch("api.v2.auth.verify_identity_token", new=_operator_identity):
        pair = _sign_in(client).json()
    refreshed = client.post("/api/v3/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    claims = _claims(refreshed.json()["access_token"])
    assert claims["role"] == "User" and claims["channel"] == "consumer_web"


def test_roles_json_uid_grant_also_capped(monkeypatch) -> None:
    async def assigned(uid):
        return "SuperAdmin"

    monkeypatch.setattr("infrastructure.db.user_repo.assigned_role", assigned)
    with patch("api.v2.auth.verify_identity_token", new=_operator_identity):
        response = _sign_in(_client())
    assert _claims(response.json()["access_token"])["role"] == "User"


def test_gateway_resolves_capped_role(container) -> None:
    """The v3 authenticator re-resolves the role from the access token's claims on every call."""
    from aura_core.api import build_app
    from aura_service import _build_authenticator

    with patch("api.v2.auth.verify_identity_token", new=_operator_identity):
        access = _sign_in(_client()).json()["access_token"]
    authenticator = _build_authenticator(
        container.settings, service_keys=container.service_keys, tenancy=container.tenancy
    )
    with TestClient(build_app(container, authenticator=authenticator)) as app:
        response = app.get("/api/v3/admin/service-keys", headers={"Authorization": f"Bearer {access}"})
    assert response.status_code == 403


def test_mobile_token_route_unchanged() -> None:
    from api.v2.auth import verify_token

    client = _client()
    client.app.dependency_overrides[verify_token] = lambda: dict(OPERATOR)
    response = client.post("/api/v3/auth/token", headers={"Authorization": "Bearer google-id"})
    assert response.status_code == 200, response.text
    claims = _claims(response.json()["access_token"])
    assert claims["role"] == "SuperAdmin" and "channel" not in claims


def test_consumer_route_rate_limited() -> None:
    client = _client()
    with patch("api.v2.auth.verify_identity_token", new=_operator_identity):
        statuses = [_sign_in(client).status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10 and statuses[10] == 429


def test_rate_key_ignores_the_callers_own_forwarded_entries() -> None:
    """Review B#2. Behind Railway, uvicorn's --forwarded-allow-ips * sets request.client.host to the
    *leftmost* X-Forwarded-For entry, which the caller writes. The limiter must key on the entry
    the trusted proxy appended (counted from the right), so rotating the left part changes nothing."""
    from types import SimpleNamespace

    from api.v2.auth import _consumer_client_key

    keys = {
        _consumer_client_key(SimpleNamespace(
            client=SimpleNamespace(host=f"10.0.0.{i}"),
            headers={"x-forwarded-for": f"10.0.0.{i}, 203.0.113.7"},
        ))
        for i in range(5)
    }
    assert keys == {"203.0.113.7"}


def test_rate_table_does_not_grow_without_bound() -> None:
    from api.v2 import auth as auth_module

    for i in range(auth_module.CONSUMER_RATE_TABLE_MAX + 50):
        auth_module._consumer_hits[f"198.51.{i // 256}.{i % 256}"] = [0.0]
    client = _client()
    with patch("api.v2.auth.verify_identity_token", new=_operator_identity):
        _sign_in(client)
    assert len(auth_module._consumer_hits) <= auth_module.CONSUMER_RATE_TABLE_MAX
