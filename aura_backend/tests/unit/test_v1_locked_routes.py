"""V3-only cutover: unmounted V1/V2/unversioned paths are 404, not 410.

Platform ``/api/v3/...`` routes stay mounted and must not return 410.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aura_service import app
from infrastructure.http_limits import RateLimitMiddleware

pytest.importorskip("fastapi")


def _client() -> TestClient:
    RateLimitMiddleware.request_history.clear()
    import infrastructure.http_limits as http_limits

    http_limits._rate_limit_redis = None
    return TestClient(app)


UNMOUNTED = [
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
    ("POST", "/api/v2/auth/token", {}),
    ("GET", "/api/v2/sync", None),
    ("GET", "/api/v2/insights", None),
    ("POST", "/api/automation/analyze-email", {"subject": "s", "sender": "a@b.c", "body": "hello"}),
    ("POST", "/api/learning/log", {
        "id": "l1",
        "prompt": "p",
        "response": "r",
        "rating": 5,
        "timestamp": "2026-08-21T00:00:00Z",
    }),
]


PLATFORM = [
    ("POST", "/api/v3/automation/analyze-email", {"subject": "s", "sender": "a@b.c", "body": "hello"}),
    ("POST", "/api/v3/learning/log", {
        "id": "l1",
        "prompt": "p",
        "response": "r",
        "rating": 5,
        "timestamp": "2026-08-21T00:00:00Z",
    }),
    ("POST", "/api/v3/chat", {"message": "hi"}),
    ("GET", "/api/v3/memory", None),
    ("POST", "/api/v3/auth/token", {}),
    ("GET", "/api/v3/sync", None),
    ("GET", "/api/v3/insights", None),
]


@pytest.mark.parametrize("method,path,body", UNMOUNTED)
def test_unmounted_legacy_routes_return_404(method: str, path: str, body) -> None:
    client = _client()
    response = client.request(method, path, json=body)
    assert response.status_code == 404


@pytest.mark.parametrize("method,path,body", PLATFORM)
def test_platform_v3_routes_are_not_gone(method: str, path: str, body) -> None:
    """Keep /api/v3 auth/sync/insights/automation/learning and the kernel surface."""
    client = _client()
    response = client.request(method, path, json=body)
    assert response.status_code not in (404, 410)
