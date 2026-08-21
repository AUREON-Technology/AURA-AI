"""Phase 4: v1 chat/RAG/memory/telemetry return 410 after the Flutter cutover.

v2 auth/sync/insights and v1 automations/learning stay mounted.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import RateLimitMiddleware, app
from services.security import verify_token

pytest.importorskip("fastapi")


def _client() -> TestClient:
    RateLimitMiddleware.request_history.clear()
    import main as main_mod

    main_mod._rate_limit_redis = None
    app.dependency_overrides[verify_token] = lambda: {
        "uid": "user_123",
        "email": "user@aura.ai",
    }
    return TestClient(app)


LOCKED = [
    ("POST", "/api/chat/stream", {"messages": [{"role": "user", "content": "hi"}]}),
    ("POST", "/api/chat/agent", {
        "agent_id": "orchestrator",
        "messages": [{"role": "user", "content": "hi"}],
    }),
    ("POST", "/api/rag/ingest", {"doc_id": "d1", "content": "hello world"}),
    ("POST", "/api/rag/query", {"query": "leave policy", "limit": 4}),
    ("POST", "/api/memory/extract", {"messages": [{"role": "user", "content": "hi"}]}),
    ("POST", "/api/memory/save", {"fact": "I like tea"}),
    ("GET", "/api/memory/list", None),
    ("DELETE", "/api/memory/delete/1", None),
    ("POST", "/api/telemetry/log", {
        "id": "t1",
        "event_type": "request",
        "latency_ms": 1.0,
        "agent_used": "test",
        "model_used": "test",
        "is_offline": False,
        "timestamp": "2026-08-21T00:00:00Z",
    }),
]


@pytest.mark.parametrize("method,path,body", LOCKED)
def test_locked_v1_ai_routes_return_410(method: str, path: str, body) -> None:
    client = _client()
    try:
        response = client.request(method, path, json=body)
        assert response.status_code == 410
        detail = response.json()["detail"]
        assert detail["error"] == "gone"
        assert "/api/v3/" in detail["message"]
    finally:
        app.dependency_overrides.clear()


def test_v2_and_automations_are_not_locked() -> None:
    """Keep /api/v2/* and /api/automation/* and /api/learning/*."""
    client = _client()
    try:
        # Unauthenticated-shape requests still must not 410: the route exists.
        automation = client.post(
            "/api/automation/analyze-email",
            json={"subject": "s", "sender": "a@b.c", "body": "hello"},
        )
        assert automation.status_code != 410

        learning = client.post(
            "/api/learning/log",
            json={
                "id": "l1",
                "prompt": "p",
                "response": "r",
                "rating": 5,
                "timestamp": "2026-08-21T00:00:00Z",
            },
        )
        assert learning.status_code != 410
    finally:
        app.dependency_overrides.clear()
