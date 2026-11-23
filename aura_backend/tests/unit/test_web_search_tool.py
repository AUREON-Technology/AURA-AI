"""ADR-040: ``web_search`` against an operator-run SearXNG.

The HTTP boundary is ``httpx.MockTransport``: the network is the one thing a default test run may not
touch, so the SearXNG server is stood in for by a handler that answers like one (its real JSON
shape). Everything on this side of the boundary -- URL building, response caps, redirect refusal,
result cleaning, registration, the persona grants and the taint -- is the real code.
"""

from __future__ import annotations

import json

import httpx
import pytest

from aura_core.agents.registry import build_default_agent_registry
from aura_core.config.settings import AuraSettings
from aura_core.domain.errors import ConfigurationError, ToolExecutionError
from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.domain.network import validate_search_url
from aura_core.ports.runtime import ToolContext
from aura_core.tools.builtin import default_tools
from aura_core.tools.websearch import WEB_SEARCH_TOOL_NAMES, build_web_search_tool

BASE = "http://searxng.internal:8080"


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-search", deadline_s=30.0)


def searx_payload(n: int = 3, **overrides) -> dict:
    payload = {
        "query": "ignored",
        "number_of_results": n,
        "results": [
            {"title": f"Result {i}", "url": f"https://docs.example.org/page/{i}", "content": f"Snippet {i}",
             "engine": "duckduckgo", "engines": ["duckduckgo", "brave"], "publishedDate": None}
            for i in range(n)
        ],
        "unresponsive_engines": [["google", "CAPTCHA"]],
    }
    payload.update(overrides)
    return payload


def tool_with(handler, **kwargs):
    (web_search,) = build_web_search_tool(base_url=BASE, transport=httpx.MockTransport(handler), **kwargs)
    return web_search


class TestRequestShape:
    async def test_goes_only_to_the_configured_host_with_the_query_as_one_parameter(self, ctx) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=searx_payload(2))

        out = json.loads(await tool_with(handler).execute({"query": "fastapi lifespan events"}, ctx))
        assert len(seen) == 1
        request = seen[0]
        assert (request.url.scheme, request.url.host, request.url.port, request.url.path) == (
            "http", "searxng.internal", 8080, "/search")
        assert request.url.params["q"] == "fastapi lifespan events"
        assert request.url.params["format"] == "json"
        assert out["count"] == 2

    @pytest.mark.parametrize("hostile", [
        "x&engines=../../admin", "x\r\nHost: evil.test", "http://169.254.169.254/latest/meta-data/",
        "@evil.test/", "../../etc/passwd", "x#frag", "x?format=html",
    ])
    async def test_a_hostile_query_cannot_change_host_path_or_parameters(self, ctx, hostile) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=searx_payload(1))

        try:
            await tool_with(handler).execute({"query": hostile}, ctx)
        except ToolExecutionError:
            assert "\n" in hostile  # only control characters are refused outright
            return
        (request,) = seen
        assert request.url.host == "searxng.internal" and request.url.path == "/search"
        # Exactly the parameters the tool set; the hostile text is the *value* of q, nothing more.
        assert set(request.url.params.keys()) == {"q", "format", "safesearch"}
        assert request.url.params["q"] == hostile
        assert request.url.params["format"] == "json"
        assert "Host" not in {k for k in request.headers if k.lower() == "host" and request.headers[k] == "evil.test"}

    async def test_engines_and_language_are_the_operator_s_not_the_model_s(self, ctx) -> None:
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=searx_payload(1))

        await tool_with(handler, engines="duckduckgo,wikipedia", language="en").execute({"query": "q"}, ctx)
        assert seen[0].url.params["engines"] == "duckduckgo,wikipedia"
        assert seen[0].url.params["language"] == "en"

    async def test_redirects_are_refused_and_the_target_is_never_contacted(self, ctx) -> None:
        contacted: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            contacted.append(str(request.url))
            if request.url.host == "searxng.internal":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
            return httpx.Response(200, json=searx_payload(1))  # pragma: no cover - must not be reached

        with pytest.raises(ToolExecutionError, match="redirect"):
            await tool_with(handler).execute({"query": "q"}, ctx)
        assert all("169.254.169.254" not in url for url in contacted)

    async def test_empty_and_control_character_queries_are_refused(self, ctx) -> None:
        def handler(request):  # pragma: no cover - must not be reached
            raise AssertionError("no request should be made")

        for bad in ("", "   ", "a\x00b", "a\nb"):
            with pytest.raises(ToolExecutionError):
                await tool_with(handler).execute({"query": bad}, ctx)


class TestResults:
    async def test_shape_cap_and_cleaning(self, ctx) -> None:
        payload = searx_payload(0)
        payload["results"] = [
            {"title": "Good\x00 title  with \n whitespace", "url": "https://a.example/x", "content": "S" * 5000,
             "engine": "ddg", "publishedDate": "2025-01-02"},
            {"title": "js", "url": "javascript:alert(1)", "content": "x", "engine": "ddg"},
            {"title": "data", "url": "data:text/html,<script>", "content": "x", "engine": "ddg"},
            {"title": "file", "url": "file:///etc/passwd", "content": "x", "engine": "ddg"},
            {"title": "dup", "url": "https://a.example/x", "content": "x", "engine": "ddg"},
            "not an object",
            {"title": "second", "url": "https://b.example/y", "content": "ok", "engines": ["brave"]},
            {"title": "third", "url": "https://c.example/z", "content": "ok", "engine": "ddg"},
        ]
        out = json.loads(await tool_with(lambda r: httpx.Response(200, json=payload)).execute(
            {"query": "q", "limit": 2}, ctx))
        assert out["count"] == 2
        first, second = out["results"]
        assert first["title"] == "Good title with whitespace"
        assert len(first["snippet"]) <= 400 and first["snippet"].endswith("…")
        assert first["published"] == "2025-01-02"
        assert first["engine"] == "ddg"
        assert second == {"title": "second", "url": "https://b.example/y", "snippet": "ok", "engine": "brave"}
        assert out["unresponsive_engines"] == ["google"]
        assert out["skipped"] == 5  # js, data, file, duplicate, non-object
        assert "Untrusted" in out["note"]

    async def test_max_results_is_clamped(self, ctx) -> None:
        out = json.loads(await tool_with(lambda r: httpx.Response(200, json=searx_payload(30))).execute(
            {"query": "q", "limit": 99}, ctx))
        assert out["count"] == 10

    async def test_no_results_is_an_empty_list_not_an_error(self, ctx) -> None:
        out = json.loads(await tool_with(lambda r: httpx.Response(200, json=searx_payload(0))).execute(
            {"query": "q"}, ctx))
        assert out["count"] == 0 and out["results"] == []


class TestFailures:
    async def test_oversized_body_is_refused_not_partially_parsed(self, ctx) -> None:
        big = json.dumps(searx_payload(3)).encode() + b" " * 5_000
        with pytest.raises(ToolExecutionError, match="budget"):
            await tool_with(lambda r: httpx.Response(200, content=big), max_body_bytes=2_000).execute(
                {"query": "q"}, ctx)

    async def test_declared_oversize_is_refused_before_reading(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="budget"):
            await tool_with(
                lambda r: httpx.Response(200, headers={"content-length": "999999999"}, content=b"{}"),
                max_body_bytes=1_000).execute({"query": "q"}, ctx)

    async def test_http_errors_and_non_searxng_bodies(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="403"):
            await tool_with(lambda r: httpx.Response(403)).execute({"query": "q"}, ctx)
        with pytest.raises(ToolExecutionError, match="HTTP 502"):
            await tool_with(lambda r: httpx.Response(502)).execute({"query": "q"}, ctx)
        with pytest.raises(ToolExecutionError, match="unreadable"):
            await tool_with(lambda r: httpx.Response(200, text="<html>hello</html>")).execute({"query": "q"}, ctx)
        with pytest.raises(ToolExecutionError, match="unreadable"):
            await tool_with(lambda r: httpx.Response(200, json={"results": "nope"})).execute({"query": "q"}, ctx)

    async def test_timeout_and_connection_errors_are_clean_tool_errors(self, ctx) -> None:
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        def down(request):
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(ToolExecutionError, match="did not answer"):
            await tool_with(slow).execute({"query": "q"}, ctx)
        with pytest.raises(ToolExecutionError, match="could not be reached"):
            await tool_with(down).execute({"query": "q"}, ctx)


class TestRegistrationAndGrants:
    def test_not_registered_without_a_url(self) -> None:
        assert build_web_search_tool(base_url="") == []
        assert not WEB_SEARCH_TOOL_NAMES & {t.spec.name for t in default_tools()}

    def test_registered_with_a_url_and_tainting(self) -> None:
        names = {t.spec.name: t.spec for t in default_tools(web_search_url=BASE)}
        spec = names["web_search"]
        assert "untrusted_content" in spec.tags
        assert spec.side_effecting is False and spec.required_capability is None

    @pytest.mark.parametrize("bad", [
        "ftp://x", "searx.local", "http://u:p@searx/", "http://searx/?x=1", "http://searx/#a", "http://searx\\x",
        "http://searx /a", "http://:80", "http://searx:notaport",
    ])
    def test_a_bad_url_is_refused(self, bad) -> None:
        with pytest.raises(ValueError):
            validate_search_url(bad)

    def test_settings_report_a_bad_url_at_validation(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_WEB_SEARCH_URL", "ftp://nope")
        settings = AuraSettings.from_env()
        with pytest.raises(ConfigurationError) as raised:
            settings.validate()
        assert any("AURA_WEB_SEARCH_URL" in problem for problem in raised.value.details["problems"])
        monkeypatch.setenv("AURA_WEB_SEARCH_URL", BASE + "/")
        assert AuraSettings.from_env().tools.web_search_url == BASE + "/"

    def test_engineer_and_research_agent_hold_search_and_fetch(self) -> None:
        agents = build_default_agent_registry()
        engineer = agents.get("engineer")
        research = agents.get("research_agent")
        assert {"web_search", "fetch_url"} <= set(engineer.allowed_tools)
        assert {"web_search", "fetch_url"} <= set(research.allowed_tools)
        for other in ("decision_agent", "finance_advisor", "health_companion", "task_planner"):
            assert "web_search" not in set(agents.get(other).allowed_tools)

    async def test_a_search_taints_the_turn_through_the_real_registry(self, user) -> None:
        from aura_core.tools.policy import ToolPolicy
        from aura_core.tools.registry import ToolRegistry

        (web_search,) = build_web_search_tool(
            base_url=BASE, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=searx_payload(1))))
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(web_search)
        context = ToolContext(user_id=user, correlation_id="c1", deadline_s=10, tainted=False)
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("t1"), name="web_search", arguments={"query": "hello"}), context)
        assert result.ok
        assert context.tainted, "search results are untrusted: the turn must be tainted"
