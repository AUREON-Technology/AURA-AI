"""AURA-018: retired v1 AI routes must 410 without leaking exception strings."""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import RateLimitMiddleware, app
from services.security import verify_token

SECRET = "secret path /tmp/foo"


def _client() -> TestClient:
    RateLimitMiddleware.request_history.clear()
    import main as main_mod

    main_mod._rate_limit_redis = None
    app.dependency_overrides[verify_token] = lambda: {
        "uid": "user_123",
        "email": "user@aura.ai",
    }
    return TestClient(app)


def test_query_document_is_gone_and_does_not_leak() -> None:
    client = _client()
    try:
        response = client.post("/api/rag/query", json={"query": "leave policy", "limit": 4})
        assert response.status_code == 410
        assert SECRET not in response.text
        assert "/tmp/foo" not in response.text
        assert "gone" in response.text
    finally:
        app.dependency_overrides.clear()


def test_save_memory_is_gone_and_does_not_leak() -> None:
    client = _client()
    try:
        response = client.post("/api/memory/save", json={"fact": "I like tea"})
        assert response.status_code == 410
        assert SECRET not in response.text
        assert "gone" in response.text
    finally:
        app.dependency_overrides.clear()
