"""Desktop pairing auth (ADR-011 Unit 6) and /desktop/token."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v2.auth import router
from services.desktop_pairing import (
    clear_local_pairing_state_for_tests,
    exchange_pairing_code,
    issue_pairing_code,
    verify_desktop_device_token,
)
from services.security import verify_device_integrity


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def setup_function() -> None:
    clear_local_pairing_state_for_tests()


def test_desktop_token_issues_for_any_authenticated_user(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("DEVELOPER_EMAILS", raising=False)
    monkeypatch.delenv("SUPERADMIN_EMAILS", raising=False)

    async def identity(request, authorization):
        return {"uid": "u1", "email": "user@example.com", "name": "User"}

    with patch("api.v2.auth.verify_identity_token", new=identity):
        response = _client().post(
            "/api/v3/auth/desktop/token", headers={"Authorization": "Bearer google-id"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]


def test_pairing_code_round_trip() -> None:
    issued = issue_pairing_code(
        {"uid": "u1", "email": "u@example.com", "name": "U", "role": "User"}
    )
    assert len(issued["code"]) == 8
    exchanged = exchange_pairing_code(issued["code"], device_name="test-host")
    assert exchanged is not None
    assert exchanged["device_token"]
    assert exchanged["access_token"]
    assert verify_desktop_device_token(exchanged["device_token"])
    # Code is single-use.
    assert exchange_pairing_code(issued["code"]) is None


def test_device_integrity_accepts_desktop_pairing_token(monkeypatch) -> None:
    monkeypatch.setattr("config.settings.debug_mode", False)
    monkeypatch.setattr("config.settings.allow_mock_attestation", False)
    issued = issue_pairing_code({"uid": "u1", "email": "u@example.com", "name": "U"})
    exchanged = exchange_pairing_code(issued["code"])
    assert exchanged is not None
    assert verify_device_integrity(exchanged["device_token"], "desktop") is True
    assert verify_device_integrity("not-a-token", "desktop") is False
    assert verify_device_integrity(exchanged["device_token"], "android") is False


def test_pairing_http_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "ops@aura.ai")

    async def access(request, authorization):
        return {"uid": "a1", "email": "ops@aura.ai", "name": "Ops", "role": "Admin"}

    with patch("api.v2.auth.verify_access_token", new=access):
        start = _client().post(
            "/api/v3/auth/desktop/pairing",
            headers={"Authorization": "Bearer access"},
        )
    assert start.status_code == 200
    code = start.json()["code"]

    exchange = _client().post(
        "/api/v3/auth/desktop/pairing/exchange",
        json={"code": code, "device_name": "ci"},
    )
    assert exchange.status_code == 200
    body = exchange.json()
    assert body["device_token"]
    assert body["access_token"]

    bad = _client().post(
        "/api/v3/auth/desktop/pairing/exchange",
        json={"code": "NOPECODE"},
    )
    assert bad.status_code == 400
