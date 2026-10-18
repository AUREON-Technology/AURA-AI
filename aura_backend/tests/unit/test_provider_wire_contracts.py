"""Wire contracts for hosted open-weight providers (ADR-030 Phase 3).

``OpenAICompatibleProvider`` is one adapter for many servers, and "OpenAI-compatible" is a
family resemblance, not a specification. Each fixture in ``tests/fixtures/provider_wire/`` pins
one way a real provider's responses differ from the common shape. Every test here asserts what
the *router* needs to be true, because that is what decides whether the difference matters:

* text is the answer, never the model's hidden reasoning;
* usage is found wherever the provider put it, so quotas settle to real numbers;
* an upstream failure is a failure -- never an empty successful answer -- and a 429 stays a 429
  so it benches the provider instead of tripping the breaker;
* SSE keep-alive comments are not content.

The fixtures marked ``documented`` are reconstructed from provider documentation, not captured
live (see that directory's README). Phase 6 replaces them with captures.

Also here: the payload side of Phase 3 -- planner output sizing per declared decode speed, and
``extra_body`` -- because both are only meaningful in the request a provider actually receives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")

from aura_core.config.settings import ProviderConfig  # noqa: E402
from aura_core.domain import CompletionRequest, Conversation, Message  # noqa: E402
from aura_core.domain.errors import ConfigurationError, ProviderError  # noqa: E402
from aura_core.domain.llm import FinishReason, ProviderCapabilities, SamplingParams  # noqa: E402
from aura_core.llm import ProviderRouter, capabilities_from_config  # noqa: E402
from aura_core.llm.providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "provider_wire"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _response(fixture: dict[str, Any]) -> httpx.Response:
    headers = dict(fixture.get("headers") or {})
    if "sse" in fixture:
        body = ("\n".join(fixture["sse"]) + "\n").encode()
        return httpx.Response(fixture["status"], headers=headers, content=body)
    return httpx.Response(fixture["status"], headers=headers, json=fixture["json"])


def _provider(
    fixture: dict[str, Any] | None = None,
    *,
    captured: dict[str, Any] | None = None,
    caps: ProviderCapabilities | None = None,
    extra_body: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(
        name="hosted",
        base_url="http://provider.test/v1",
        model="openai/gpt-oss-120b",
        api_key="k",
        capabilities=caps or ProviderCapabilities(
            model_id="openai/gpt-oss-120b", context_window=32768, max_output_tokens=4096,
            supports_tools=True,
        ),
        extra_body=extra_body,
        timeout_s=timeout_s,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["body"] = json.loads(request.content)
        if fixture is None:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
        return _response(fixture)

    provider._client = httpx.AsyncClient(
        base_url="http://provider.test/v1", transport=httpx.MockTransport(handler)
    )
    return provider


def _request(**overrides) -> CompletionRequest:
    base = dict(
        conversation=Conversation.of([Message.user("What is the capital of France?")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=256),
    )
    base.update(overrides)
    return CompletionRequest(**base)


async def _drain(provider: OpenAICompatibleProvider, request: CompletionRequest | None = None):
    return [chunk async for chunk in provider.stream(request or _request())]


# -- responses -------------------------------------------------------------------------


class TestResponseShapes:
    async def test_reasoning_never_leaks_into_the_answer(self) -> None:
        response = await _provider(_fixture("groq_complete_reasoning")).complete(_request())
        assert response.text == "Paris."
        assert "capital of France" not in response.text
        assert response.usage.prompt_tokens == 80 and response.usage.completion_tokens == 40
        assert response.raw_metadata["usage_estimated"] is False

    async def test_usage_under_x_groq_is_found_in_a_stream(self) -> None:
        chunks = await _drain(_provider(_fixture("groq_stream_xgroq_usage")))
        assert "".join(c.delta for c in chunks) == "Paris."
        final = chunks[-1]
        assert final.finish_reason is FinishReason.STOP
        # The real 3 completion tokens, not a character-count estimate: quotas settle on this.
        assert (final.usage.prompt_tokens, final.usage.completion_tokens) == (80, 3)

    async def test_keepalive_comments_are_not_content(self) -> None:
        chunks = await _drain(_provider(_fixture("openrouter_stream_keepalive")))
        assert "".join(c.delta for c in chunks) == "Hello there"
        assert chunks[-1].usage.total_tokens == 14

    async def test_tool_calls_from_workers_ai_parse(self) -> None:
        response = await _provider(_fixture("workers_ai_tool_call")).complete(_request())
        assert response.finish_reason is FinishReason.TOOL_CALLS
        (call,) = response.tool_calls
        assert call.name == "get_weather" and dict(call.arguments) == {"city": "Cairo"}


class TestFailuresStayFailures:
    async def test_a_429_carries_the_reset_the_provider_named(self) -> None:
        with pytest.raises(ProviderError) as excinfo:
            await _provider(_fixture("groq_rate_limited")).complete(_request())
        details = excinfo.value.details
        assert details["status"] == 429
        # Retry-After wins over the reset headers (8s, not the 10-minute request reset).
        assert details["retry_after_s"] == 8.0
        assert excinfo.value.retryable

    async def test_a_midstream_error_is_not_an_empty_answer(self) -> None:
        """Before this, the adapter read only ``choices`` and returned "" with finish STOP."""
        with pytest.raises(ProviderError) as excinfo:
            await _drain(_provider(_fixture("openrouter_stream_midstream_error")))
        assert excinfo.value.details["status"] == 502
        assert "Upstream provider returned an error" in excinfo.value.details["upstream_error"]
        assert excinfo.value.retryable

    async def test_an_upstream_429_in_a_stream_benches_the_provider(self) -> None:
        """The 429 code survives, so the router applies a cooldown, not a breaker failure."""
        hosted = _provider(_fixture("openrouter_stream_upstream_429"))
        from aura_core.llm.providers.echo import EchoProvider

        local = EchoProvider(name="local", capabilities=ProviderCapabilities(
            model_id="m", context_window=8192, max_output_tokens=512, quality_tier=1,
        ))
        router = ProviderRouter([hosted, local], default_policy=_quality_first())
        chunks = [c async for c in router.stream(_request())]
        assert {c.provider for c in chunks} == {"local"}
        assert router.quota.cooldown_remaining("hosted") > 0
        assert router._stats["hosted"].breaker.consecutive_failures == 0

    async def test_a_200_error_body_is_an_error(self) -> None:
        fixture = {"status": 200, "headers": {}, "json": {"error": {"code": 503, "message": "overloaded"}}}
        with pytest.raises(ProviderError) as excinfo:
            await _provider(fixture).complete(_request())
        assert excinfo.value.details["status"] == 503


def _quality_first():
    from aura_core.domain.llm import RoutingPolicy

    return RoutingPolicy.QUALITY_FIRST


# -- requests --------------------------------------------------------------------------


def _planner_like(timeout_s: float) -> CompletionRequest:
    return _request(
        sampling=SamplingParams(temperature=0.0, max_output_tokens=700),
        preferred_max_output_tokens=1200,
        timeout_s=timeout_s,
    )


class TestOutputSizing:
    """The planner asks for 700 and prefers 1200 (``planning/engine.py``)."""

    @pytest.mark.parametrize(
        ("tokens_per_second", "timeout_s", "expected"),
        [
            (0.0, 60.0, 700),    # undeclared: never more than the safe size
            (5.0, 180.0, 700),   # the CPU provider: 1200 would need 240s+, as measured
            (300.0, 60.0, 1200), # a hosted GPU API finishes 1200 in seconds
            (300.0, 4.0, 700),   # ...but not inside a 4-second deadline
        ],
    )
    async def test_the_preferred_size_needs_a_declared_speed_that_fits(
        self, tokens_per_second: float, timeout_s: float, expected: int
    ) -> None:
        captured: dict[str, Any] = {}
        caps = ProviderCapabilities(
            model_id="m", context_window=32768, max_output_tokens=4096,
            tokens_per_second=tokens_per_second,
        )
        provider = _provider(captured=captured, caps=caps, timeout_s=timeout_s)
        await provider.complete(_planner_like(timeout_s))
        assert captured["body"]["max_tokens"] == expected

    def test_the_quota_estimate_uses_the_same_budget(self) -> None:
        fast = ProviderCapabilities(
            model_id="m", context_window=32768, max_output_tokens=4096, tokens_per_second=300.0,
        )
        provider = _provider(caps=fast)
        router = ProviderRouter([provider])
        request = _planner_like(60.0)
        assert router._estimated_tokens(request, provider) == request.conversation.approx_tokens() + 1200

    async def test_the_provider_cap_still_wins(self) -> None:
        captured: dict[str, Any] = {}
        caps = ProviderCapabilities(
            model_id="m", context_window=8192, max_output_tokens=900, tokens_per_second=300.0,
        )
        await _provider(captured=captured, caps=caps).complete(_planner_like(60.0))
        assert captured["body"]["max_tokens"] == 900


class TestExtraBody:
    async def test_extra_fields_reach_the_provider(self) -> None:
        captured: dict[str, Any] = {}
        await _provider(captured=captured, extra_body={"reasoning_effort": "low"}).complete(_request())
        assert captured["body"]["reasoning_effort"] == "low"
        assert captured["body"]["model"] == "openai/gpt-oss-120b"

    def test_it_is_read_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_PROVIDER_FAST_BASE_URL", "http://vllm:8000/v1")
        monkeypatch.setenv("AURA_PROVIDER_FAST_MODEL", "m")
        monkeypatch.setenv("AURA_PROVIDER_FAST_EXTRA_BODY", '{"reasoning_effort": "low"}')
        monkeypatch.setenv("AURA_PROVIDER_FAST_TOKENS_PER_SECOND", "250")
        config = ProviderConfig.from_env("fast")
        assert config.extra_body_fields == {"reasoning_effort": "low"}
        assert capabilities_from_config(config).tokens_per_second == 250.0

    @pytest.mark.parametrize(
        "raw",
        ["{not json", '["a list"]', '{"max_tokens": 99999}', '{"response_format": {"type": "json_object"}}',
         '{"model": "something-else"}', '{"tools": []}'],
    )
    def test_invalid_or_reserved_bodies_are_refused(self, raw: str) -> None:
        """A provider-side override of what AURA sent would make the router's checks untrue."""
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="p", kind="echo", model="m", extra_body=raw)

    def test_tokens_per_second_cannot_be_negative(self) -> None:
        with pytest.raises(ValueError):
            ProviderCapabilities(model_id="m", context_window=8192, max_output_tokens=512,
                                 tokens_per_second=-1.0)


class TestCapabilityTruthfulness:
    async def test_json_schema_is_sent_only_to_a_provider_that_declares_it(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        captured: dict[str, Any] = {}
        no_json = ProviderCapabilities(model_id="m", context_window=8192, max_output_tokens=512)
        await _provider(captured=captured, caps=no_json).complete(_request(response_schema=schema))
        assert "response_format" not in captured["body"]

        with_json = ProviderCapabilities(
            model_id="m", context_window=8192, max_output_tokens=512, supports_json_mode=True,
        )
        await _provider(captured=captured, caps=with_json).complete(_request(response_schema=schema))
        assert captured["body"]["response_format"]["type"] == "json_schema"

    def test_a_schema_request_is_never_routed_to_a_provider_without_json_mode(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        no_json = _provider(caps=ProviderCapabilities(
            model_id="m", context_window=8192, max_output_tokens=512,
        ))
        router = ProviderRouter([no_json])
        assert router.plan(_request(response_schema=schema)).excluded == {"hosted": "no_json_mode"}


# -- live captures (Phase 6) -----------------------------------------------------------------

#: Written by ``scripts/capture_provider_wire.py`` (or the Verify Hosted Inference workflow) against
#: a live API. Each one replays here as soon as it is committed: the documented fixtures above say
#: what the adapter expects, these say what the provider actually sends.
_CAPTURES = sorted(_FIXTURES.glob("*_capture_*.json"))


def _capture_params():
    if not _CAPTURES:
        return [pytest.param(None, marks=pytest.mark.skip(reason="no live captures committed yet"))]
    return [pytest.param(path, id=path.stem) for path in _CAPTURES]


class TestLiveCaptures:
    @pytest.mark.parametrize("path", _capture_params())
    async def test_a_live_capture_parses_with_real_usage(self, path: Path) -> None:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert fixture["provenance"].startswith("captured "), path.name
        assert fixture["status"] == 200, f"{path.name} recorded HTTP {fixture['status']}"
        provider = _provider(fixture)

        if "sse" in fixture:
            chunks = await _drain(provider)
            assert "".join(c.delta for c in chunks).strip(), "the stream carried no text"
            usage = next((c.usage for c in reversed(chunks) if c.usage is not None), None)
            assert usage is not None and usage.completion_tokens > 0, "no usage in the stream"
            return

        response = await provider.complete(_request())
        assert response.raw_metadata["usage_estimated"] is False, "the provider reported no usage"
        message = fixture["json"]["choices"][0]["message"]
        if message.get("tool_calls"):
            assert response.finish_reason is FinishReason.TOOL_CALLS
            assert [c.name for c in response.tool_calls] == ["get_weather"]
            assert "city" in dict(response.tool_calls[0].arguments)
        else:
            assert response.text.strip(), "the completion carried no text"
