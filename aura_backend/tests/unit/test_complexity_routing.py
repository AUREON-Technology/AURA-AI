"""Complexity routing and the Claude provider (ADR-033).

The properties under test:

* the scorer is deterministic and puts demanding messages above the default threshold and
  everyday ones below it, in English and Arabic;
* with ``AURA_LLM_COMPLEXITY_ROUTING`` on, a complex *interactive* turn asks for the
  ``reasoning`` pool and everything else keeps its pool; with it off nothing changes;
* the router then serves complex turns from the reasoning provider first, keeps it away from
  everyday turns entirely, falls back to the generalists when it is unavailable, and never sends
  it content above its data ceiling;
* the Claude adapter, driven through the real ``anthropic`` SDK over a mock HTTP transport,
  sends the request shape the Messages API expects and parses text, tool calls, usage, stop
  reasons and errors back into AURA's types.

No network and no model: providers are echo providers, and the SDK talks to an in-process
transport.
"""

from __future__ import annotations

import json

import pytest

from aura_core.config import load_settings
from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import ProviderError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.llm import (
    DataClassification,
    FinishReason,
    ModelPool,
    RoutingPolicy,
    SamplingParams,
    WorkloadClass,
)
from aura_core.domain.messages import Role, ToolCall
from aura_core.llm import ProviderRouter
from aura_core.llm.complexity import assess_complexity
from aura_core.llm.providers.echo import EchoProvider
from aura_core.llm.sensitivity import data_scope
from aura_core.llm.workload import workload_for_turn, workload_scope

COMPLEX_EN = (
    "Design a scalable architecture for a multi-tenant SaaS. Compare Postgres row-level "
    "security with schema-per-tenant, analyze the trade-offs, and give a step-by-step "
    "migration plan. It must support at least 10k tenants without downtime."
)
COMPLEX_AR = "صمم خطة مقارنة بين النظامين و حلل الإيجابيات والسلبيات خطوة بخطوة"
PROOF = "Prove that the sum of the first n odd numbers is n^2."
SIMPLE = ("hi", "What time is it in Cairo?", "Translate 'good morning' to French", "Can you plan my day?")


# -- the scorer ------------------------------------------------------------------------


class TestAssessComplexity:
    @pytest.mark.parametrize("message", [COMPLEX_EN, COMPLEX_AR, PROOF])
    def test_demanding_messages_clear_the_default_threshold(self, message: str) -> None:
        assert assess_complexity(message).is_complex(0.5)

    @pytest.mark.parametrize("message", SIMPLE)
    def test_everyday_messages_do_not(self, message: str) -> None:
        assert not assess_complexity(message).is_complex(0.5)

    def test_it_is_deterministic_and_bounded(self) -> None:
        huge = (COMPLEX_EN + "\n```py\nx = 1\n```\n1. a\n2. b\n3. c\n? ? ?\n") * 50
        first, second = assess_complexity(huge), assess_complexity(huge)
        assert first == second
        assert 0.0 <= first.score <= 1.0

    def test_repeating_one_word_is_not_several_asks(self) -> None:
        assert assess_complexity("design design design design design").score == pytest.approx(0.2)

    def test_length_alone_does_not_make_a_message_complex(self) -> None:
        assert not assess_complexity("word " * 1000).is_complex(0.5)

    def test_signals_name_features_not_content(self) -> None:
        assessment = assess_complexity(COMPLEX_EN)
        assert set(assessment.signals) <= {
            "length", "reasoning_terms", "formal", "constraints", "multi_part",
            "many_questions", "code_block", "inline_code", "math",
        }

    def test_empty_is_zero(self) -> None:
        assert assess_complexity("").score == 0.0
        assert assess_complexity("   ").score == 0.0


# -- the turn hint ---------------------------------------------------------------------


def _hint(message: str, *, threshold: float | None = 0.5, agent: str = "orchestrator",
          route: str = "chat", workload: WorkloadClass | None = None):
    return workload_for_turn(
        route=route, agent=agent, user_key="u", workload=workload,
        message=message, complexity_threshold=threshold,
    )


class TestWorkloadForTurn:
    def test_a_complex_interactive_turn_asks_for_the_reasoning_pool(self) -> None:
        hint = _hint(COMPLEX_EN)
        assert hint.pool is ModelPool.REASONING
        assert hint.workload is WorkloadClass.INTERACTIVE
        assert not hint.strict_pool  # a preference: generalists remain the fallback

    def test_an_everyday_turn_keeps_general_fast(self) -> None:
        assert _hint("hi").pool is ModelPool.GENERAL_FAST

    def test_off_means_unchanged(self) -> None:
        assert _hint(COMPLEX_EN, threshold=None).pool is ModelPool.GENERAL_FAST

    def test_engineer_turns_keep_the_coder_pool(self) -> None:
        assert _hint(COMPLEX_EN, agent="engineer").pool is ModelPool.CODER

    def test_voice_turns_stay_on_the_fast_pool(self) -> None:
        assert _hint(COMPLEX_EN, route="voice").pool is ModelPool.GENERAL_FAST

    def test_an_explicit_workload_is_never_overridden(self) -> None:
        hint = _hint(COMPLEX_EN, workload=WorkloadClass.INTERACTIVE)
        assert hint.pool is ModelPool.GENERAL_FAST

    def test_the_threshold_is_respected(self) -> None:
        assert _hint(COMPLEX_EN, threshold=1.0).pool is ModelPool.GENERAL_FAST


class TestSettings:
    def test_off_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("AURA_LLM_COMPLEXITY_ROUTING", raising=False)
        assert load_settings(refresh=True, validate=False).llm.complexity_routing is False

    def test_read_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_LLM_COMPLEXITY_ROUTING", "true")
        monkeypatch.setenv("AURA_LLM_COMPLEXITY_THRESHOLD", "0.4")
        llm = load_settings(refresh=True, validate=False).llm
        assert llm.complexity_routing is True
        assert llm.complexity_threshold == pytest.approx(0.4)


# -- routing ---------------------------------------------------------------------------


def _local() -> EchoProvider:
    return EchoProvider(
        name="local",
        capabilities=ProviderCapabilities(
            model_id="qwen2.5:3b", context_window=8192, max_output_tokens=512,
            supports_tools=True, quality_tier=1, is_local=True,
        ),
    )


def _deep() -> EchoProvider:
    """What the registry builds for ``kind=claude`` with ``POOLS=reasoning``."""
    return EchoProvider(
        name="claude",
        capabilities=ProviderCapabilities(
            model_id="claude-opus-5-5", context_window=200_000, max_output_tokens=32_000,
            supports_tools=True, quality_tier=3, pools=(ModelPool.REASONING,),
            supports_reasoning=True, third_party=True,
            max_data_classification=DataClassification.INTERNAL,
        ),
    )


def _request() -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("question")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
        data_classification=DataClassification.INTERNAL,
    )


@pytest.mark.parametrize("policy", list(RoutingPolicy))
class TestRouting:
    def test_complex_turns_go_to_the_reasoning_provider_first(self, policy) -> None:
        router = ProviderRouter([_local(), _deep()], default_policy=policy)
        with workload_scope(_hint(COMPLEX_EN)):
            decision = router.plan(_request())
        assert decision.candidates[0] == "claude"
        assert "local" in decision.candidates  # still the fallback

    def test_everyday_turns_never_reach_it(self, policy) -> None:
        router = ProviderRouter([_local(), _deep()], default_policy=policy)
        with workload_scope(_hint("hi")):
            decision = router.plan(_request())
        assert decision.candidates == ("local",)
        assert decision.excluded["claude"] == "pool_mismatch"

    def test_sensitive_content_stays_local_even_when_complex(self, policy) -> None:
        router = ProviderRouter([_local(), _deep()], default_policy=policy)
        with workload_scope(_hint(COMPLEX_EN)), data_scope(DataClassification.SENSITIVE):
            decision = router.plan(_request())
        assert decision.candidates == ("local",)
        assert decision.excluded["claude"] == "data_classification"


class TestFallback:
    async def test_a_failing_reasoning_provider_falls_back_to_local(self) -> None:
        deep = _deep()

        async def boom(request):
            raise ProviderError("down", provider="claude", retryable=True)

        deep.complete = boom  # type: ignore[method-assign]
        router = ProviderRouter([_local(), deep], default_policy=RoutingPolicy.LOCAL_FIRST)
        with workload_scope(_hint(COMPLEX_EN)):
            response = await router.complete(_request())
        assert response.provider == "local"


class TestKernelEndToEnd:
    """A real kernel turn on the in-memory container, providers configured from env."""

    @staticmethod
    def _container(monkeypatch, *, enabled: bool):
        from aura_core.container import Container

        monkeypatch.setenv("AURA_LLM_PROVIDERS", "local,deep")
        monkeypatch.setenv("AURA_PROVIDER_LOCAL_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_LOCAL_IS_LOCAL", "true")
        monkeypatch.setenv("AURA_PROVIDER_DEEP_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_DEEP_POOLS", "reasoning")
        monkeypatch.setenv("AURA_PROVIDER_DEEP_QUALITY_TIER", "3")
        monkeypatch.setenv("AURA_LLM_ROUTING_POLICY", "local_first")
        monkeypatch.setenv("AURA_LLM_CACHE_ENABLED", "false")
        monkeypatch.setenv("AURA_LLM_COMPLEXITY_ROUTING", "true" if enabled else "false")
        return Container(load_settings(refresh=True, validate=False))

    async def _provider_for(self, container, message: str) -> str | None:
        from aura_core.kernel import TurnRequest

        result = await container.kernel.run(
            TurnRequest(message=message, user_id=UserId("u1"), use_tools=False)
        )
        return result.telemetry.provider

    async def test_complex_turns_are_answered_by_the_reasoning_provider(self, monkeypatch) -> None:
        container = self._container(monkeypatch, enabled=True)
        assert await self._provider_for(container, COMPLEX_EN) == "deep"
        assert await self._provider_for(container, "hi") == "local"

    async def test_disabled_routes_as_before(self, monkeypatch) -> None:
        container = self._container(monkeypatch, enabled=False)
        assert await self._provider_for(container, COMPLEX_EN) == "local"


# -- the Claude adapter ----------------------------------------------------------------

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")


def _sse(*events: dict) -> bytes:
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events
    )


def _message_stream(text: str = "", tool: dict | None = None, stop: str = "end_turn") -> bytes:
    events: list[dict] = [{
        "type": "message_start",
        "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 1,
                      "cache_read_input_tokens": 30, "cache_creation_input_tokens": 0},
        },
    }]
    index = 0
    if text:
        events.append({"type": "content_block_start", "index": index,
                       "content_block": {"type": "text", "text": ""}})
        for piece in (text[: len(text) // 2], text[len(text) // 2:]):
            events.append({"type": "content_block_delta", "index": index,
                           "delta": {"type": "text_delta", "text": piece}})
        events.append({"type": "content_block_stop", "index": index})
        index += 1
    if tool:
        events.append({"type": "content_block_start", "index": index, "content_block": {
            "type": "tool_use", "id": tool["id"], "name": tool["name"], "input": {}}})
        events.append({"type": "content_block_delta", "index": index, "delta": {
            "type": "input_json_delta", "partial_json": json.dumps(tool["input"])}})
        events.append({"type": "content_block_stop", "index": index})
    events.append({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                   "usage": {"output_tokens": 42}})
    events.append({"type": "message_stop"})
    return _sse(*events)


class _Upstream:
    """An in-process Messages API: records requests, answers with a canned response."""

    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.body = body or _message_stream("Hello from Claude.")
        self.status = status
        self.headers = headers or {}

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.path.startswith("/v1/models/"):
            return httpx2.Response(200, json={
                "id": "claude-opus-5-5", "type": "model", "display_name": "Claude Opus 5.5",
                "created_at": "2026-01-01T00:00:00Z",
            })
        if self.status != 200:
            return httpx2.Response(self.status, headers=self.headers, json={
                "type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=self.body)

    def json(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)


def _provider(upstream: _Upstream, *, api_key: str = "test-key", extra_body=None, **caps):
    from aura_core.llm.providers.claude import ClaudeProvider

    capabilities = ProviderCapabilities(
        model_id="claude-opus-5-5", context_window=200_000,
        max_output_tokens=caps.pop("max_output_tokens", 32_000), supports_tools=True,
        supports_json_mode=caps.pop("supports_json_mode", False), quality_tier=3,
        third_party=True, max_data_classification=DataClassification.INTERNAL,
    )
    return ClaudeProvider(
        name="claude", model="claude-opus-5-5", api_key=api_key, capabilities=capabilities,
        extra_body=extra_body,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(upstream.handler)),
    )


def _conversation() -> Conversation:
    call = ToolCall(id=ToolCallId("call.1"), name="search", arguments={"q": "x"})
    return Conversation.of([
        Message.system("You are AURA."),
        Message.user("Find x."),
        Message(role=Role.ASSISTANT, content="", tool_calls=(call,)),
        Message(role=Role.TOOL, content="x is 42", tool_call_id=ToolCallId("call.1"), name="search"),
        Message(role=Role.TOOL, content="orphan", tool_call_id=ToolCallId("other"), name="lookup"),
        Message.user("And so?"),
    ])


class TestClaudeAdapter:
    async def test_request_shape(self) -> None:
        upstream = _Upstream()
        provider = _provider(upstream)
        request = CompletionRequest(
            conversation=_conversation(),
            sampling=SamplingParams(temperature=0.3, max_output_tokens=700, stop=("END",)),
            tools=({"name": "search", "description": "Search.",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}},),
        )
        await provider.complete(request)
        body = upstream.json()
        sent = upstream.requests[-1]

        assert sent.headers["authorization"] == "Bearer test-key"
        assert "server-side-fallback-2026-07-01" in sent.headers.get("anthropic-beta", "")
        assert body["model"] == "claude-opus-5-5"
        assert body["system"] == "You are AURA."
        assert body["max_tokens"] == 700 + 8000  # answer budget + thinking headroom
        assert body["output_config"] == {"effort": "high"}
        assert body["fallbacks"] == "default"
        assert body["cache_control"] == {"type": "ephemeral"}
        assert body["stop_sequences"] == ["END"]
        assert "temperature" not in body and "top_p" not in body
        assert body["tools"] == [{"name": "search", "description": "Search.", "input_schema": {
            "type": "object", "properties": {"q": {"type": "string"}}}}]
        assert body["tool_choice"] == {"type": "auto"}

        roles = [m["role"] for m in body["messages"]]
        assert roles == ["user", "assistant", "user"]
        assistant = body["messages"][1]["content"]
        assert assistant == [{"type": "tool_use", "id": "call_1", "name": "search", "input": {"q": "x"}}]
        last = body["messages"][2]["content"]
        assert last[0] == {"type": "tool_result", "tool_use_id": "call_1", "content": "x is 42"}
        assert last[1]["type"] == "text" and "orphan" in last[1]["text"]  # undeclared: as text
        assert last[2] == {"type": "text", "text": "And so?"}

    async def test_text_and_usage_are_parsed(self) -> None:
        provider = _provider(_Upstream())
        response = await provider.complete(CompletionRequest(
            conversation=Conversation.of([Message.user("hi")])))
        assert response.text == "Hello from Claude."
        assert response.provider == "claude"
        assert response.finish_reason is FinishReason.STOP
        assert response.usage.prompt_tokens == 150  # input + cache reads
        assert response.usage.completion_tokens == 42

    async def test_tool_calls_are_parsed(self) -> None:
        upstream = _Upstream(_message_stream(
            tool={"id": "toolu_1", "name": "search", "input": {"q": "y"}}, stop="tool_use"))
        response = await _provider(upstream).complete(
            CompletionRequest(conversation=Conversation.of([Message.user("hi")])))
        assert response.finish_reason is FinishReason.TOOL_CALLS
        assert response.tool_calls[0].name == "search"
        assert dict(response.tool_calls[0].arguments) == {"q": "y"}

    async def test_a_refusal_is_a_content_filter(self) -> None:
        upstream = _Upstream(_message_stream("", stop="refusal"))
        response = await _provider(upstream).complete(
            CompletionRequest(conversation=Conversation.of([Message.user("hi")])))
        assert response.finish_reason is FinishReason.CONTENT_FILTER

    async def test_streaming_yields_text_then_a_final_chunk(self) -> None:
        provider = _provider(_Upstream())
        chunks = [c async for c in provider.stream(
            CompletionRequest(conversation=Conversation.of([Message.user("hi")])))]
        assert "".join(c.delta for c in chunks) == "Hello from Claude."
        assert chunks[-1].is_final
        assert chunks[-1].usage is not None and chunks[-1].usage.completion_tokens == 42

    async def test_a_429_is_a_retryable_error_with_its_reset(self) -> None:
        upstream = _Upstream(status=429, headers={"retry-after": "7"})
        with pytest.raises(ProviderError) as excinfo:
            await _provider(upstream).complete(
                CompletionRequest(conversation=Conversation.of([Message.user("hi")])))
        assert excinfo.value.retryable
        assert excinfo.value.details["status"] == 429
        assert excinfo.value.details["retry_after_s"] == pytest.approx(7.0)
        assert len(upstream.requests) == 1  # no SDK retries: the router owns retry

    async def test_a_stream_failure_raises_before_any_chunk(self) -> None:
        upstream = _Upstream(status=500)
        gen = _provider(upstream).stream(
            CompletionRequest(conversation=Conversation.of([Message.user("hi")])))
        with pytest.raises(ProviderError):
            await gen.__anext__()

    async def test_an_empty_key_is_refused_not_resolved_ambiently(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key-must-not-be-used")
        upstream = _Upstream()
        provider = _provider(upstream, api_key="")
        with pytest.raises(ProviderError) as excinfo:
            await provider.complete(
                CompletionRequest(conversation=Conversation.of([Message.user("hi")])))
        assert not excinfo.value.retryable
        assert upstream.requests == []
        assert not (await provider.health()).healthy

    async def test_extra_body_overrides_effort_and_disables_fallback(self) -> None:
        upstream = _Upstream()
        provider = _provider(
            upstream, extra_body={"output_config": {"effort": "xhigh"}, "fallbacks": None})
        await provider.complete(CompletionRequest(conversation=Conversation.of([Message.user("hi")])))
        body = upstream.json()
        assert body["output_config"] == {"effort": "xhigh"}
        assert "fallbacks" not in body

    async def test_a_schema_becomes_structured_output_when_declared(self) -> None:
        upstream = _Upstream()
        provider = _provider(upstream, supports_json_mode=True)
        schema = {"type": "object", "properties": {"a": {"type": "string"}},
                  "required": ["a"], "additionalProperties": False}
        await provider.complete(CompletionRequest(
            conversation=Conversation.of([Message.user("hi")]), response_schema=schema))
        assert upstream.json()["output_config"]["format"] == {"type": "json_schema", "schema": schema}

    async def test_max_tokens_never_exceeds_the_declared_cap(self) -> None:
        upstream = _Upstream()
        provider = _provider(upstream, max_output_tokens=4000)
        await provider.complete(CompletionRequest(
            conversation=Conversation.of([Message.user("hi")]),
            sampling=SamplingParams(max_output_tokens=1000)))
        assert upstream.json()["max_tokens"] == 4000

    async def test_health_uses_the_models_api(self) -> None:
        upstream = _Upstream()
        status = await _provider(upstream).health()
        assert status.healthy
        assert upstream.requests[-1].url.path == "/v1/models/claude-opus-5-5"


class TestClaudeRegistry:
    def test_the_registry_builds_a_third_party_reasoning_provider(self, monkeypatch) -> None:
        from aura_core.llm import build_providers
        from aura_core.llm.providers.claude import ClaudeProvider

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        monkeypatch.setenv("AURA_LLM_PROVIDERS", "claude")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE_KIND", "claude")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE_MODEL", "claude-opus-5-5")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE_API_KEY_ENV", "AURA_TEST_CLAUDE_KEY")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE_POOLS", "reasoning")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE_QUALITY_TIER", "3")
        [provider] = build_providers(load_settings(refresh=True, validate=False).llm)

        assert isinstance(provider, ClaudeProvider)
        caps = provider.capabilities
        assert caps.third_party and not caps.is_local
        assert caps.pools == (ModelPool.REASONING,)
        assert caps.max_data_classification is DataClassification.INTERNAL

    def test_a_reserved_body_field_is_refused(self, monkeypatch) -> None:
        from aura_core.config.settings import ProviderConfig
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        for body in ('{"system": "x"}', '{"thinking": {"type": "disabled"}}',
                     '{"output_config": {"format": {}}}'):
            with pytest.raises(ConfigurationError):
                ProviderConfig(name="c", kind="claude", model="claude-opus-5-5",
                               api_key_env="K", extra_body=body)
