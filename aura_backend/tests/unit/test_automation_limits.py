"""F36 / INT-115: the email and issue analyses are bounded, rate limited, and say why they 503.

* F36: each call is a model generation over text the caller chooses, with no body cap on the
  fields and no rate limit -- a cheap way to occupy the inference host.
* INT-115: the analysis is unclassified, hence SENSITIVE (``aura_core/llm/sensitivity.py``),
  and stays that way: an email or issue is third-party text, the same class as a retrieved
  document, so only a self-hosted provider may read it. On a deployment whose only provider is
  hosted (Railway today) the router refuses it for content; the client should be told that,
  not "generation failed, retry".
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from aura_core.domain.errors import CapabilityUnavailableError
from aura_core.security.rate_limit import ROUTE_COSTS, RateLimitPolicy, SlidingWindowRateLimiter


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Router:
    def __init__(self, text: str = "", *, error: Exception | None = None) -> None:
        self.text, self.error, self.requests = text, error, []

    async def complete(self, request, **_):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _Response(self.text)


EMAIL = {"subject": "Exam on Friday", "sender": "prof@uni.edu", "body": "Room 4, 9am."}
GOOD = json.dumps({"summary": "s", "subtasks": ["a"]})


@pytest.fixture
def client():
    import infrastructure.http_limits as http_limits
    from aura_service import app
    from infrastructure.http_limits import RateLimitMiddleware
    from services.jwt_auth import verify_access_token

    RateLimitMiddleware.request_history.clear()
    http_limits._rate_limit_redis = None
    app.dependency_overrides[verify_access_token] = lambda: {
        "uid": "analyst_1", "email": "a@aura.ai", "role": "User",
    }
    original_router = app.state.aura.router
    original_limiter = getattr(app.state, "rate_limiter", None)
    try:
        yield app, TestClient(app)
    finally:
        app.state.aura.router = original_router
        app.state.rate_limiter = original_limiter
        app.dependency_overrides.clear()


@pytest.mark.parametrize("path,body", [
    ("/api/v3/automation/analyze-email", {**EMAIL, "body": "x" * 50_001}),
    ("/api/v3/automation/analyze-email", {**EMAIL, "subject": "s" * 2_001}),
    ("/api/v3/automation/analyze-email", {**EMAIL, "sender": "s" * 513}),
    ("/api/v3/automation/analyze-issue", {"title": "t", "body": "b" * 65_537}),
    ("/api/v3/automation/analyze-issue", {"title": "t" * 1_025, "body": "b"}),
])
def test_oversized_fields_are_refused_before_any_generation(client, path, body) -> None:
    app, http = client
    router = _Router(GOOD)
    app.state.aura.router = router
    response = http.post(path, json=body)
    assert response.status_code == 422
    assert router.requests == []


def test_fields_at_their_limit_are_analysed(client) -> None:
    app, http = client
    app.state.aura.router = _Router(GOOD)
    # 65,536 characters is GitHub's own ceiling for an issue body: any real issue fits.
    assert http.post("/api/v3/automation/analyze-issue",
                     json={"title": "t" * 1_024, "body": "b" * 65_536}).status_code == 200
    assert http.post("/api/v3/automation/analyze-email",
                     json={**EMAIL, "body": "x" * 50_000}).status_code == 200


def test_analyses_are_rate_limited_per_user_like_a_chat_turn(client) -> None:
    app, http = client
    router = _Router(GOOD)
    app.state.aura.router = router
    app.state.rate_limiter = SlidingWindowRateLimiter(
        app.state.aura.cache_backend,
        policies=(RateLimitPolicy(scope="user", limit=ROUTE_COSTS["chat"], window_s=3600),),
    )
    assert http.post("/api/v3/automation/analyze-issue", json={"title": "t", "body": "b"}).status_code == 200
    refused = http.post("/api/v3/automation/analyze-email", json=EMAIL)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 0
    assert refused.json()["detail"]["error"] == "rate_limited"
    assert len(router.requests) == 1, "the refused analysis still reached the model"


def test_no_local_provider_is_an_explicit_503_not_a_retry(client) -> None:
    app, http = client
    app.state.aura.router = _Router(error=CapabilityUnavailableError(
        "no provider answered; no provider permitted to receive this turn's content answered",
        details={"refused_for_content": ["hosted1"], "data_classification": "sensitive"},
    ))
    response = http.post("/api/v3/automation/analyze-email", json=EMAIL)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "analysis_unavailable"
    assert detail["reason"] == "no_local_provider"
    assert detail["retryable"] is False
    assert "self-hosted" in detail["message"]
    assert "hosted1" not in response.text
