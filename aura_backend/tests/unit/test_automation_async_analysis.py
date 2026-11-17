"""F-23: email/issue analysis no longer blocks the event loop, and a failure is a 503.

The handlers are ``async def`` and used to call a synchronous ``httpx.Client`` post (60 s
timeout) directly, freezing the worker's event loop -- live SSE streams included -- for the whole
generation. A failure came back as HTTP 200 carrying a placeholder "analysis unavailable" dict.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from aura_core.domain.errors import AllProvidersFailedError
from services.automation import (
    ANALYSIS_MAX_OUTPUT_TOKENS,
    AnalysisUnavailable,
    AutomationService,
    _parse_json_object,
)


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Router:
    """Records requests; answers with ``text`` after ``delay_s`` of *awaited* time."""

    def __init__(self, text: str = "", *, delay_s: float = 0.0, error: Exception | None = None):
        self.text, self.delay_s, self.error = text, delay_s, error
        self.requests = []

    async def complete(self, request, **_):
        self.requests.append(request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return _Response(self.text)


EMAIL = {"subject": "Exam on Friday", "sender": "prof@uni.edu", "body": "Room 4, 9am."}
ANALYSIS = {"category": "Academic", "summary": "Exam.", "urgency": "high", "action_items": []}


class TestAutomationService:
    async def test_analysis_goes_through_the_router_with_a_sized_cap(self) -> None:
        router = _Router(json.dumps(ANALYSIS))
        result = await AutomationService().analyze_email(router, **EMAIL)

        assert {k: result[k] for k in ANALYSIS} == ANALYSIS
        (request,) = router.requests
        assert request.sampling.max_output_tokens == ANALYSIS_MAX_OUTPUT_TOKENS
        assert request.sampling.temperature == 0.0
        # Unclassified on purpose: that is SENSITIVE, so only a local provider may serve it.
        assert request.require_local is False

    async def test_a_fenced_answer_still_parses(self) -> None:
        router = _Router("Sure!\n```json\n" + json.dumps(ANALYSIS) + "\n```")
        result = await AutomationService().analyze_email(router, **EMAIL)
        assert {k: result[k] for k in ANALYSIS} == ANALYSIS

    async def test_a_provider_failure_is_unavailable_not_a_placeholder(self) -> None:
        router = _Router(error=AllProvidersFailedError("timed out"))
        with pytest.raises(AnalysisUnavailable):
            await AutomationService().analyze_github_issue(router, title="t", body="b")

    async def test_unparseable_output_is_unavailable(self) -> None:
        with pytest.raises(AnalysisUnavailable):
            await AutomationService().analyze_email(_Router("I cannot do that."), **EMAIL)

    async def test_a_slow_analysis_does_not_block_the_loop(self) -> None:
        """The defect itself: while one analysis waits on the model, other coroutines run."""
        router = _Router(json.dumps(ANALYSIS), delay_s=0.3)
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            await AutomationService().analyze_email(router, **EMAIL)
        finally:
            beat.cancel()
        assert ticks >= 10

    def test_parse_rejects_non_objects(self) -> None:
        assert _parse_json_object("[1, 2]") is None
        assert _parse_json_object("{broken") is None
        assert _parse_json_object('x {"a": 1} y') == {"a": 1}


class TestOutputIsValidatedNotPassedThrough:
    """INT-018: the model's JSON used to reach the client verbatim, which hard-casts it."""

    async def test_email_output_is_coerced_to_the_documented_types(self) -> None:
        raw = {
            "category": "academic",
            "summary": "Exam on Friday.",
            "urgency": "URGENT",
            "action_items": ["Revise chapter 3", {"title": 7, "due_date": 20261120},
                             {"title": "Book room", "due_date": "2026-11-20"}, 42],
            "should_create_event": "true",
            "event_title": 123,
            "event_start": 1763600000,
            "event_end": "2026-11-20T11:00:00",
        }
        result = await AutomationService().analyze_email(_Router(json.dumps(raw)), **EMAIL)

        assert result["category"] == "Academic"
        assert result["urgency"] == "low"
        assert result["should_create_event"] is True
        assert result["event_title"] == "123"
        assert result["event_start"] is None
        assert result["event_end"] == "2026-11-20T11:00:00"
        assert result["action_items"] == [
            {"title": "Revise chapter 3", "due_date": None},
            {"title": "7", "due_date": None},
            {"title": "Book room", "due_date": "2026-11-20"},
        ]

    async def test_email_defaults_fill_missing_optional_fields(self) -> None:
        result = await AutomationService().analyze_email(
            _Router(json.dumps({"summary": "Hi.", "category": "Nonsense"})), **EMAIL)
        assert result == {
            "category": "General", "summary": "Hi.", "urgency": "low", "action_items": [],
            "should_create_event": False, "event_title": None, "event_start": None,
            "event_end": None,
        }

    async def test_issue_output_is_coerced(self) -> None:
        raw = {"summary": "Crash", "subtasks": "Fix the null check",
               "complexity": "HIGH", "suggested_priority": 3}
        result = await AutomationService().analyze_github_issue(
            _Router(json.dumps(raw)), title="t", body="b")
        assert result == {"summary": "Crash", "subtasks": ["Fix the null check"],
                          "complexity": "high", "suggested_priority": "medium"}

    @pytest.mark.parametrize("raw", [
        {"category": "General"},                 # no summary at all
        {"summary": {"nested": "object"}},       # summary of the wrong shape
        {"summary": ["a", "b"]},
    ])
    async def test_output_without_a_usable_summary_is_unavailable(self, raw) -> None:
        with pytest.raises(AnalysisUnavailable, match="invalid_output"):
            await AutomationService().analyze_email(_Router(json.dumps(raw)), **EMAIL)
        with pytest.raises(AnalysisUnavailable, match="invalid_output"):
            await AutomationService().analyze_github_issue(
                _Router(json.dumps(raw)), title="t", body="b")


class TestRoutes:
    @pytest.fixture
    def client(self):
        import infrastructure.http_limits as http_limits
        from aura_service import app
        from infrastructure.http_limits import RateLimitMiddleware
        from services.jwt_auth import verify_access_token

        RateLimitMiddleware.request_history.clear()
        http_limits._rate_limit_redis = None
        app.dependency_overrides[verify_access_token] = lambda: {
            "uid": "user_123", "email": "user@aura.ai", "role": "User",
        }
        original = app.state.aura.router
        try:
            yield app, TestClient(app)
        finally:
            app.state.aura.router = original
            app.dependency_overrides.clear()

    def test_a_failed_analysis_is_a_503(self, client) -> None:
        app, http = client
        app.state.aura.router = _Router(error=AllProvidersFailedError("down"))
        response = http.post("/api/v3/automation/analyze-email", json=EMAIL)
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "analysis_unavailable"
        assert "down" not in response.text

    def test_a_good_analysis_is_returned(self, client) -> None:
        app, http = client
        app.state.aura.router = _Router(json.dumps({"summary": "s", "subtasks": ["a"]}))
        response = http.post("/api/v3/automation/analyze-issue", json={"title": "t", "body": "b"})
        assert response.status_code == 200
        assert response.json()["subtasks"] == ["a"]

    def test_unusable_output_is_a_clean_503(self, client) -> None:
        app, http = client
        app.state.aura.router = _Router(json.dumps({"subtasks": [{"x": 1}]}))
        response = http.post("/api/v3/automation/analyze-issue", json={"title": "t", "body": "b"})
        assert response.status_code == 503
        assert response.json()["detail"] == {
            "error": "analysis_unavailable", "reason": "invalid_output"}

    def test_the_route_returns_the_validated_shape(self, client) -> None:
        app, http = client
        app.state.aura.router = _Router(json.dumps(
            {"summary": "s", "should_create_event": "false", "extra": "dropped"}))
        response = http.post("/api/v3/automation/analyze-email", json=EMAIL)
        assert response.status_code == 200
        body = response.json()
        assert body["should_create_event"] is False
        assert "extra" not in body
        assert body["action_items"] == []


class TestRateLimiterOffTheLoop:
    async def test_a_slow_redis_does_not_stall_other_requests(self) -> None:
        """The INCR runs in the threadpool, so a stalled Redis costs a thread, not the loop."""
        import infrastructure.http_limits as http_limits
        from infrastructure.http_limits import RateLimitMiddleware

        class _SlowRedis:
            def incr(self, key):
                time.sleep(0.3)
                return 1

            def expire(self, key, ttl):
                return True

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        original = http_limits._rate_limit_redis
        http_limits._rate_limit_redis = _SlowRedis()
        try:
            middleware = RateLimitMiddleware(app)
            scope = {
                "type": "http", "method": "GET", "path": "/x", "raw_path": b"/x",
                "query_string": b"", "headers": [], "client": ("1.2.3.4", 1),
                "server": ("t", 80), "scheme": "http", "http_version": "1.1",
                "root_path": "",
            }
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            ticks = 0

            async def heartbeat() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.01)
                    ticks += 1

            beat = asyncio.create_task(heartbeat())
            try:
                await middleware(scope, receive, send)
            finally:
                beat.cancel()
            assert ticks >= 10
            assert sent[0]["status"] == 200
        finally:
            http_limits._rate_limit_redis = original
