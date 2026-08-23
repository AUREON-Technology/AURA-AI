"""Browser operator login: no attestation, fail-closed below Developer."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v2.auth import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_web_token_refuses_a_consumer(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)
    monkeypatch.delenv("SUPERADMIN_EMAILS", raising=False)

    async def identity(request, authorization):
        return {"uid": "u1", "email": "user@example.com", "name": "User"}

    with patch("api.v2.auth.verify_identity_token", new=identity):
        response = _client().post(
            "/api/v3/auth/web/token", headers={"Authorization": "Bearer google-id"}
        )
    assert response.status_code == 403


def test_web_token_issues_a_pair_for_an_admin(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")

    async def identity(request, authorization):
        return {"uid": "a1", "email": "ops@aura.ai", "name": "Ops"}

    with patch("api.v2.auth.verify_identity_token", new=identity):
        response = _client().post(
            "/api/v3/auth/web/token", headers={"Authorization": "Bearer google-id"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
