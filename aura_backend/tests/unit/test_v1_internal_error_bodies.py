"""AURA-018: live platform routes must not leak exception strings."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_service import app
from infrastructure.http_limits import RateLimitMiddleware
from services.jwt_auth import verify_access_token

SECRET = "secret path /tmp/foo"


def _client() -> TestClient:
    RateLimitMiddleware.request_history.clear()
    import infrastructure.http_limits as http_limits

    http_limits._rate_limit_redis = None
    app.dependency_overrides[verify_access_token] = lambda: {
        "uid": "user_123",
        "email": "user@aura.ai",
        "role": "Admin",
    }
    return TestClient(app)


def test_analyze_email_does_not_leak_exception_strings() -> None:
    from services.automation import automation_service

    client = _client()
    try:
        with patch.object(
            automation_service, "analyze_email", side_effect=RuntimeError(SECRET)
        ):
            response = client.post(
                "/api/v3/automation/analyze-email",
                json={"subject": "s", "sender": "a@b.c", "body": "hello"},
            )
        assert response.status_code == 500
        assert SECRET not in response.text
        assert "/tmp/foo" not in response.text
        assert response.json()["detail"] == "internal_error"
    finally:
        app.dependency_overrides.clear()


def test_export_learning_does_not_leak_exception_strings() -> None:
    from services.learning_pipeline import learning_pipeline

    client = _client()
    try:
        with patch.object(
            learning_pipeline, "build_lora_dataset", side_effect=RuntimeError(SECRET)
        ):
            response = client.get("/api/v3/learning/export")
        assert response.status_code == 500
        assert SECRET not in response.text
        assert "/tmp/foo" not in response.text
        assert response.json()["detail"] == "internal_error"
    finally:
        app.dependency_overrides.clear()
