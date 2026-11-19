"""H1: POST /api/v3/auth/nonce issues a fresh, single-use value clients bind
into their attestation request so the server-side replay check has
something to check against.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aura_service import app
from infrastructure.http_limits import RateLimitMiddleware


def _client() -> TestClient:
    RateLimitMiddleware.request_history.clear()
    from api.v2.auth import _reset_auth_rate_limits

    _reset_auth_rate_limits()  # /nonce is throttled per address (F39)
    import infrastructure.http_limits as http_limits

    http_limits._rate_limit_redis = None
    return TestClient(app)


def test_nonce_endpoint_returns_a_value():
    response = _client().post("/api/v3/auth/nonce")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("nonce"), str)
    assert len(body["nonce"]) > 0


def test_nonce_endpoint_returns_a_different_value_each_call():
    client = _client()
    first = client.post("/api/v3/auth/nonce").json()["nonce"]
    second = client.post("/api/v3/auth/nonce").json()["nonce"]
    assert first != second
