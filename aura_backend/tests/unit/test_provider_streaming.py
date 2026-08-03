"""Anthropic and Gemini streaming, conversation shaping, and failure translation.

Streaming is where these adapters diverge most from each other and are most likely to be wrong:
Anthropic delivers tool arguments as incremental JSON fragments keyed by block index, Gemini
delivers whole candidates. Both must surface an upstream failure **before** the first chunk,
because that is the contract the router's failover depends on.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain import (
    CompletionRequest,
    Conversation,
    Message,
    SamplingParams,
    ToolCallId,
)
from aura_core.domain.errors import ProviderError
from aura_core.domain.llm import FinishReason, ToolChoice
from aura_core.domain.messages import ToolCall, ToolResult

httpx = pytest.importorskip("httpx")


def _install(provider, handler) -> None:
    provider._client = httpx.AsyncClient(
        base_url=provider._base_url(),
        headers=dict(provider._headers()),
        transport=httpx.MockTransport(handler),
    )


def _request(conversation: Conversation | None = None, **overrides) -> CompletionRequest:
    base = dict(
        conversation=conversation or Conversation.of([Message.user("hello")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
    )
    base.update(overrides)
    return CompletionRequest(**base)


def _sse(*events: dict) -> str:
    return "\n".join(f"data: {json.dumps(e)}" for e in events)


@pytest.fixture
def anthropic():
    from aura_core.llm.providers.anthropic import AnthropicProvider

    return AnthropicProvider(name="anthropic", model="claude-sonnet-4-5", api_key="secret")


@pytest.fixture
def gemini():
    from aura_core.llm.providers.gemini import GeminiProvider

    return GeminiProvider(name="gemini", model="gemini-2.5-flash", api_key="secret")


class TestAnthropicConversationShaping:
    def test_system_messages_are_hoisted_out_of_the_message_list(self, anthropic) -> None:
        """Anthropic takes the system prompt as a top-level field, not as a message."""
        body = anthropic._build_body(
            _request(Conversation.of([
                Message.system("You are AURA."), Message.system("Be brief."),
                Message.user("hello"),
            ])),
            stream=False,
        )
        assert body["system"] == "You are AURA.\n\nBe brief."
        assert all(m["role"] != "system" for m in body["messages"])

    def test_consecutive_same_role_messages_are_merged(self, anthropic) -> None:
        """The API rejects consecutive same-role messages; a conversation assembled from memory
        plus history plus tool results must not have to know that rule."""
        body = anthropic._build_body(
            _request(Conversation.of([
                Message.user("first"), Message.user("second"), Message.user("third"),
            ])),
            stream=False,
        )
        assert len(body["messages"]) == 1
        assert [b["text"] for b in body["messages"][0]["content"]] == [
            "first", "second", "third"
        ]

    def test_a_tool_result_becomes_a_user_block_carrying_its_call_id(self, anthropic) -> None:
        conversation = Conversation.of([
            Message.user("what is 2+2?"),
            Message.assistant("", tool_calls=(
                ToolCall(id=ToolCallId("call_1"), name="calculator",
                         arguments={"expression": "2+2"}),
            )),
            Message.from_tool_result(ToolResult(
                call_id=ToolCallId("call_1"), name="calculator", ok=True, content="4")),
        ])
        body = anthropic._build_body(_request(conversation), stream=False)

        tool_result = body["messages"][-1]["content"][0]
        assert tool_result["type"] == "tool_result"
        assert tool_result["tool_use_id"] == "call_1"
        assert tool_result["is_error"] is False

    def test_a_failed_tool_result_is_marked_as_an_error(self, anthropic) -> None:
        conversation = Conversation.of([
            Message.user("q"),
            Message.from_tool_result(ToolResult(
                call_id=ToolCallId("c1"), name="calculator", ok=False,
                content="division by zero", error_kind="tool_execution_error")),
        ])
        body = anthropic._build_body(_request(conversation), stream=False)
        assert body["messages"][-1]["content"][-1]["is_error"] is True

    def test_an_assistant_tool_call_is_rendered_as_a_tool_use_block(self, anthropic) -> None:
        conversation = Conversation.of([
            Message.user("q"),
            Message.assistant("Let me check.", tool_calls=(
                ToolCall(id=ToolCallId("c1"), name="calculator", arguments={"expression": "1+1"}),
            )),
        ])
        body = anthropic._build_body(_request(conversation), stream=False)
        blocks = body["messages"][-1]["content"]

        assert blocks[0] == {"type": "text", "text": "Let me check."}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["input"] == {"expression": "1+1"}

    def test_top_p_is_sent_only_when_constraining(self, anthropic) -> None:
        """Sending temperature and top_p together is discouraged by the API."""
        unconstrained = anthropic._build_body(
            _request(sampling=SamplingParams(temperature=0.0, top_p=1.0)), stream=False
        )
        constrained = anthropic._build_body(
            _request(sampling=SamplingParams(temperature=0.0, top_p=0.9)), stream=False
        )
        assert "top_p" not in unconstrained
        assert constrained["top_p"] == pytest.approx(0.9)

    def test_stop_sequences_are_translated(self, anthropic) -> None:
        body = anthropic._build_body(
            _request(sampling=SamplingParams(temperature=0.0, stop=("\n\nUser:",))), stream=False
        )
        assert body["stop_sequences"] == ["\n\nUser:"]

    def test_tools_are_translated_to_input_schema(self, anthropic) -> None:
        tools = ({"name": "calculator", "description": "Do maths",
                  "parameters": {"type": "object", "properties": {}}},)
        body = anthropic._build_body(_request(tools=tools), stream=False)

        assert body["tools"][0]["name"] == "calculator"
        assert body["tools"][0]["input_schema"] == {"type": "object", "properties": {}}

    def test_a_required_tool_choice_becomes_any(self, anthropic) -> None:
        body = anthropic._build_body(
            _request(tools=({"name": "t"},), tool_choice=ToolChoice.REQUIRED), stream=False
        )
        assert body["tool_choice"] == {"type": "any"}

    def test_tool_choice_none_removes_the_declarations_entirely(self, anthropic) -> None:
        """Declaring tools and then forbidding them wastes prompt tokens on every turn."""
        body = anthropic._build_body(
            _request(tools=({"name": "t"},), tool_choice=ToolChoice.NONE), stream=False
        )
        assert "tools" not in body


class TestAnthropicStreaming:
    async def test_text_deltas_are_yielded_then_a_terminal_usage_chunk(self, anthropic) -> None:
        body = _sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 30}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "Annual "}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "leave"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
             "usage": {"output_tokens": 2}},
        )

        _install(anthropic, lambda r: httpx.Response(200, text=body))
        chunks = [c async for c in anthropic.stream(_request())]

        assert "".join(c.delta for c in chunks) == "Annual leave"
        assert chunks[-1].finish_reason is FinishReason.STOP
        assert chunks[-1].usage.prompt_tokens == 30
        assert chunks[-1].usage.completion_tokens == 2

    async def test_incremental_tool_json_fragments_are_reassembled(self, anthropic) -> None:
        """The fragments arrive keyed by block index; reassembling them wrongly silently
        produces a tool call with the wrong arguments."""
        body = _sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "toolu_1", "name": "calculator"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '{"expre'}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": 'ssion": "2+2"}'}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
             "usage": {"output_tokens": 8}},
        )

        _install(anthropic, lambda r: httpx.Response(200, text=body))
        chunks = [c async for c in anthropic.stream(_request())]

        assert chunks[-1].finish_reason is FinishReason.TOOL_CALLS
        assert chunks[-1].tool_calls[0].name == "calculator"
        assert chunks[-1].tool_calls[0].arguments == {"expression": "2+2"}

    async def test_two_concurrent_tool_blocks_stay_separate(self, anthropic) -> None:
        body = _sse(
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "t1", "name": "calculator"}},
            {"type": "content_block_start", "index": 1,
             "content_block": {"type": "tool_use", "id": "t2", "name": "current_datetime"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '{"expression": "1+1"}'}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        )

        _install(anthropic, lambda r: httpx.Response(200, text=body))
        final = [c async for c in anthropic.stream(_request())][-1]

        assert [c.name for c in final.tool_calls] == ["calculator", "current_datetime"]
        assert final.tool_calls[0].arguments == {"expression": "1+1"}

    async def test_unparseable_tool_json_is_preserved_for_the_model_to_correct(
        self, anthropic
    ) -> None:
        body = _sse(
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "t1", "name": "calculator"}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": "{truncated"}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        )

        _install(anthropic, lambda r: httpx.Response(200, text=body))
        final = [c async for c in anthropic.stream(_request())][-1]
        assert final.tool_calls[0].arguments["_unparsed"] == "{truncated"

    async def test_a_stream_error_event_becomes_a_provider_error(self, anthropic) -> None:
        body = _sse(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "partial"}},
            {"type": "error", "error": {"type": "overloaded_error"}},
        )

        _install(anthropic, lambda r: httpx.Response(200, text=body))
        with pytest.raises(ProviderError) as excinfo:
            async for _ in anthropic.stream(_request()):
                pass
        assert "overloaded_error" in str(excinfo.value)

    async def test_malformed_frames_and_comments_are_skipped(self, anthropic) -> None:
        text = "\n".join([
            ": this is an SSE comment",
            "event: ping",
            "data: {not json",
            "data: ",
            'data: {"type": "content_block_delta", "index": 0, '
            '"delta": {"type": "text_delta", "text": "ok"}}',
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}',
        ])

        _install(anthropic, lambda r: httpx.Response(200, text=text))
        chunks = [c async for c in anthropic.stream(_request())]
        assert "".join(c.delta for c in chunks) == "ok"

    async def test_usage_is_estimated_when_the_stream_reports_none(self, anthropic) -> None:
        body = _sse(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "a reply of some length"}},
        )

        _install(anthropic, lambda r: httpx.Response(200, text=body))
        final = [c async for c in anthropic.stream(_request())][-1]
        assert final.usage.total_tokens > 0

    async def test_an_http_error_surfaces_before_the_first_chunk(self, anthropic) -> None:
        _install(anthropic, lambda r: httpx.Response(429, text="rate limited"))
        with pytest.raises(ProviderError) as excinfo:
            async for _ in anthropic.stream(_request()):
                pytest.fail("a chunk was yielded before the error surfaced")
        assert excinfo.value.retryable is True

    async def test_a_transport_failure_before_connection_is_translated(self, anthropic) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        _install(anthropic, handler)
        with pytest.raises(ProviderError):
            async for _ in anthropic.stream(_request()):
                pass


class TestAnthropicHealth:
    async def test_a_successful_probe_is_healthy(self, anthropic) -> None:
        _install(anthropic, lambda r: httpx.Response(200, json={
            "content": [{"type": "text", "text": "pong"}]
        }))
        assert (await anthropic.health()).healthy is True

    async def test_an_http_error_is_reported_not_raised(self, anthropic) -> None:
        _install(anthropic, lambda r: httpx.Response(401, text="bad key"))
        status = await anthropic.health()
        assert status.healthy is False
        assert "401" in status.detail

    async def test_a_transport_failure_is_reported_not_raised(self, anthropic) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        _install(anthropic, handler)
        assert (await anthropic.health()).healthy is False


class TestGeminiConversationShaping:
    def test_system_messages_become_a_system_instruction(self, gemini) -> None:
        body = gemini._build_body(_request(Conversation.of([
            Message.system("You are AURA."), Message.user("hello"),
        ])))
        assert body["systemInstruction"]["parts"][0]["text"] == "You are AURA."

    def test_the_assistant_role_is_renamed_to_model(self, gemini) -> None:
        body = gemini._build_body(_request(Conversation.of([
            Message.user("hi"), Message.assistant("hello"), Message.user("again"),
        ])))
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]

    def test_a_tool_result_becomes_a_function_response(self, gemini) -> None:
        body = gemini._build_body(_request(Conversation.of([
            Message.user("q"),
            Message.from_tool_result(ToolResult(
                call_id=ToolCallId("c1"), name="calculator", ok=True, content="4")),
        ])))
        response_part = body["contents"][-1]["parts"][0]["functionResponse"]
        assert response_part["name"] == "calculator"
        assert response_part["response"] == {"result": "4"}

    def test_an_assistant_tool_call_becomes_a_function_call_part(self, gemini) -> None:
        body = gemini._build_body(_request(Conversation.of([
            Message.user("q"),
            Message.assistant("", tool_calls=(
                ToolCall(id=ToolCallId("c1"), name="calculator", arguments={"expression": "1+1"}),
            )),
        ])))
        assert body["contents"][-1]["parts"][0]["functionCall"]["args"] == {"expression": "1+1"}

    def test_an_empty_message_contributes_no_content_entry(self, gemini) -> None:
        """An empty parts array is rejected by the API."""
        body = gemini._build_body(_request(Conversation.of([
            Message.user("q"), Message.assistant(""),
        ])))
        assert all(c["parts"] for c in body["contents"])

    def test_a_response_schema_enables_json_mode(self, gemini) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        body = gemini._build_body(_request(response_schema=schema))
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseSchema"] == schema

    def test_tools_are_wrapped_in_function_declarations(self, gemini) -> None:
        body = gemini._build_body(_request(tools=({"name": "calculator"},)))
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "calculator"

    def test_stop_sequences_are_translated(self, gemini) -> None:
        body = gemini._build_body(
            _request(sampling=SamplingParams(temperature=0.0, stop=("END",)))
        )
        assert body["generationConfig"]["stopSequences"] == ["END"]


class TestGeminiCompletion:
    async def test_a_blocked_prompt_is_a_typed_error_not_an_empty_answer(self, gemini) -> None:
        """An empty string reads like the model choosing to say nothing, which is a different
        thing entirely and would be reported as a successful turn."""
        _install(gemini, lambda r: httpx.Response(200, json={
            "candidates": [], "promptFeedback": {"blockReason": "SAFETY"},
        }))
        with pytest.raises(ProviderError) as excinfo:
            await gemini.complete(_request())

        assert excinfo.value.retryable is False
        assert excinfo.value.details["block_reason"] == "SAFETY"

    async def test_cost_is_computed_from_reported_usage(self, gemini) -> None:
        _install(gemini, lambda r: httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 1000},
        }))
        response = await gemini.complete(_request())
        caps = gemini.capabilities
        assert response.cost_usd == pytest.approx(
            caps.cost_per_1k_input_usd + caps.cost_per_1k_output_usd
        )


class TestGeminiStreaming:
    async def test_candidate_text_is_yielded_and_usage_carried(self, gemini) -> None:
        body = _sse(
            {"candidates": [{"content": {"parts": [{"text": "Annual "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "leave"}]},
                             "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 25, "candidatesTokenCount": 3}},
        )

        _install(gemini, lambda r: httpx.Response(200, text=body))
        chunks = [c async for c in gemini.stream(_request())]

        assert "".join(c.delta for c in chunks) == "Annual leave"
        assert chunks[-1].finish_reason is FinishReason.STOP
        assert chunks[-1].usage.prompt_tokens == 25

    async def test_function_calls_accumulate_across_events(self, gemini) -> None:
        body = _sse(
            {"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "calculator", "args": {"expression": "2+2"}}}
            ]}}]},
            {"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "current_datetime", "args": {}}}
            ]}, "finishReason": "STOP"}]},
        )

        _install(gemini, lambda r: httpx.Response(200, text=body))
        final = [c async for c in gemini.stream(_request())][-1]
        assert [c.name for c in final.tool_calls] == ["calculator", "current_datetime"]

    async def test_a_done_marker_and_malformed_frames_are_skipped(self, gemini) -> None:
        text = "\n".join([
            "data: [DONE]",
            "data: {not json",
            'data: {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}',
            "unrelated line",
        ])

        _install(gemini, lambda r: httpx.Response(200, text=text))
        chunks = [c async for c in gemini.stream(_request())]
        assert "".join(c.delta for c in chunks) == "ok"

    async def test_usage_is_estimated_when_absent(self, gemini) -> None:
        body = _sse({"candidates": [{"content": {"parts": [{"text": "a reply"}]}}]})
        _install(gemini, lambda r: httpx.Response(200, text=body))
        final = [c async for c in gemini.stream(_request())][-1]
        assert final.usage.total_tokens > 0

    async def test_an_http_error_surfaces_before_the_first_chunk(self, gemini) -> None:
        _install(gemini, lambda r: httpx.Response(500, text="internal"))
        with pytest.raises(ProviderError):
            async for _ in gemini.stream(_request()):
                pytest.fail("a chunk was yielded before the error surfaced")

    async def test_a_transport_failure_before_connection_is_translated(self, gemini) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        _install(gemini, handler)
        with pytest.raises(ProviderError):
            async for _ in gemini.stream(_request()):
                pass

    async def test_the_streaming_endpoint_requests_sse(self, gemini) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, text=_sse({"candidates": []}))

        _install(gemini, handler)
        [c async for c in gemini.stream(_request())]

        assert ":streamGenerateContent" in captured["url"]
        assert "alt=sse" in captured["url"]


class TestGeminiHealth:
    async def test_a_model_metadata_probe_is_used(self, gemini) -> None:
        """Free, unlike a generation -- health checks run on an interval."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            return httpx.Response(200, json={"name": "models/gemini-2.5-flash"})

        _install(gemini, handler)
        assert (await gemini.health()).healthy is True
        assert captured["method"] == "GET"

    async def test_an_http_error_is_reported_not_raised(self, gemini) -> None:
        _install(gemini, lambda r: httpx.Response(403, text="forbidden"))
        assert (await gemini.health()).healthy is False

    async def test_a_transport_failure_is_reported_not_raised(self, gemini) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        _install(gemini, handler)
        assert (await gemini.health()).healthy is False
