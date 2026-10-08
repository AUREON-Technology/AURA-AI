"""Ollama adapter: chat translation and the admin-only model lifecycle.

The lifecycle operations are the reason this adapter exists at all -- they are what SEC-01
exposed publicly in the legacy system. The allowlist behaviour is therefore tested as a security
property, not as a feature: an empty allowlist must refuse, not permit.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain import CompletionRequest, Conversation, Message, SamplingParams
from aura_core.domain.errors import AuthorizationError, ProviderError
from aura_core.domain.llm import FinishReason
from aura_core.llm.providers.ollama import LocalModelInfo, OllamaProvider

httpx = pytest.importorskip("httpx")


def _install_transport(provider, handler) -> None:
    provider._client = httpx.AsyncClient(
        base_url=provider._base_url(),
        headers=dict(provider._headers()),
        transport=httpx.MockTransport(handler),
    )


def _request(**overrides) -> CompletionRequest:
    base = dict(
        conversation=Conversation.of([Message.system("Be brief."), Message.user("hello")]),
        sampling=SamplingParams(temperature=0.2, max_output_tokens=64),
    )
    base.update(overrides)
    return CompletionRequest(**base)


@pytest.fixture
def provider() -> OllamaProvider:
    return OllamaProvider(
        base_url="http://ollama.internal:11434/", model="qwen2.5:7b-instruct-q4_K_M"
    )


class TestLocalModelInfo:
    def test_size_is_reported_in_gigabytes(self) -> None:
        info = LocalModelInfo(name="qwen2.5:7b", size_bytes=4_920_000_000)
        assert info.size_gb == pytest.approx(4.58, abs=0.01)

    def test_a_model_with_no_reported_size_is_zero_not_an_error(self) -> None:
        assert LocalModelInfo(name="x").size_gb == 0.0


class TestCapabilities:
    def test_defaults_declare_a_local_model_with_bounded_concurrency(self, provider) -> None:
        """A single local device saturates fast; the router must queue rather than thrash."""
        caps = provider.capabilities
        assert caps.is_local is True
        assert caps.cost_per_1k_input_usd == 0.0
        assert caps.max_concurrency == 2

    def test_a_trailing_slash_in_the_base_url_is_normalised(self) -> None:
        assert OllamaProvider(base_url="http://host:11434/")._base_url() == "http://host:11434"

    def test_explicit_capabilities_override_the_defaults(self) -> None:
        from aura_core.domain import ProviderCapabilities

        custom = ProviderCapabilities(
            model_id="phi3:mini", context_window=4096, max_output_tokens=512, is_local=True
        )
        assert OllamaProvider(capabilities=custom).capabilities.context_window == 4096


class TestChatTranslation:
    async def test_completion_translates_both_directions(self, provider) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "model": "qwen2.5:7b-instruct-q4_K_M",
                    "message": {"role": "assistant", "content": "Hi there."},
                    "prompt_eval_count": 18,
                    "eval_count": 4,
                    "done": True,
                },
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())

        assert captured["path"] == "/api/chat"
        assert captured["body"]["stream"] is False
        assert captured["body"]["options"]["temperature"] == pytest.approx(0.2)
        assert captured["body"]["options"]["num_predict"] == 64
        assert response.text == "Hi there."
        assert response.usage.prompt_tokens == 18
        assert response.usage.completion_tokens == 4
        # A local model is free at the margin; a non-zero cost would corrupt budget accounting.
        assert response.cost_usd == 0.0
        assert response.finish_reason is FinishReason.STOP
        assert response.latency_ms >= 0.0

    async def test_sampling_extras_reach_the_options_block(self, provider) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

        _install_transport(provider, handler)
        await provider.complete(
            _request(sampling=SamplingParams(temperature=0.0, stop=("\n\n",), seed=7))
        )
        assert captured["body"]["options"]["stop"] == ["\n\n"]
        assert captured["body"]["options"]["seed"] == 7

    async def test_usage_is_estimated_when_the_server_reports_none(self, provider) -> None:
        """Zero usage would make cost and budget accounting silently wrong, not merely absent."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": "a longer reply here"}})

        _install_transport(provider, handler)
        response = await provider.complete(_request())
        assert response.usage.total_tokens > 0

    async def test_a_request_level_model_overrides_the_configured_default(self, provider) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

        _install_transport(provider, handler)
        await provider.complete(_request(model="llama3.1:8b"))
        assert captured["body"]["model"] == "llama3.1:8b"

    async def test_a_json_schema_becomes_the_format_field(self, provider) -> None:
        captured: dict = {}
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": "{}"}, "done": True})

        _install_transport(provider, handler)
        await provider.complete(_request(response_schema=schema))
        assert captured["body"]["format"] == schema

    async def test_tool_calls_are_parsed_from_a_dict_argument(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "calculator",
                                          "arguments": {"expression": "2+2"}}}
                        ],
                    },
                    "done": True,
                },
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "calculator"
        assert response.tool_calls[0].arguments == {"expression": "2+2"}

    async def test_string_encoded_arguments_are_decoded(self, provider) -> None:
        """Ollama has shipped both shapes; the adapter must accept either."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "calculator",
                                          "arguments": '{"expression": "6*7"}'}}
                        ]
                    },
                    "done": True,
                },
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())
        assert response.tool_calls[0].arguments == {"expression": "6*7"}

    async def test_unparseable_arguments_are_preserved_not_dropped(self, provider) -> None:
        """Silently dropping them would surface as a tool that mysteriously did nothing."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "tool_calls": [{"function": {"name": "calculator",
                                                     "arguments": "{not json"}}]
                    },
                    "done": True,
                },
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())
        assert response.tool_calls[0].arguments["_unparsed"] == "{not json"

    async def test_a_nameless_tool_call_is_skipped(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {"tool_calls": [{"function": {"arguments": {}}},
                                               {"function": {"name": "calculator"}}]},
                    "done": True,
                },
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())
        assert [c.name for c in response.tool_calls] == ["calculator"]

    async def test_a_server_error_becomes_a_retryable_provider_error(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="model is loading")

        _install_transport(provider, handler)
        with pytest.raises(ProviderError) as excinfo:
            await provider.complete(_request())
        assert excinfo.value.retryable is True

    async def test_a_transport_failure_is_translated(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        _install_transport(provider, handler)
        with pytest.raises(ProviderError):
            await provider.complete(_request())


class TestStreaming:
    async def test_deltas_are_yielded_then_a_terminal_usage_chunk(self, provider) -> None:
        lines = [
            json.dumps({"message": {"content": "Annual "}, "done": False}),
            "",  # keep-alive blank line; must be ignored, not treated as a delta
            "{not json",  # a malformed frame must not abort a stream that is otherwise fine
            json.dumps({"message": {"content": "leave"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True,
                        "prompt_eval_count": 30, "eval_count": 2}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="\n".join(lines))

        _install_transport(provider, handler)
        chunks = [c async for c in provider.stream(_request())]

        assert "".join(c.delta for c in chunks) == "Annual leave"
        assert chunks[-1].finish_reason is FinishReason.STOP
        assert chunks[-1].usage.prompt_tokens == 30

    async def test_tool_calls_in_a_stream_set_the_finish_reason(self, provider) -> None:
        lines = [
            json.dumps({"message": {"content": "checking"}, "done": False}),
            json.dumps({
                "message": {"tool_calls": [{"function": {"name": "calculator",
                                                         "arguments": {"expression": "1+1"}}}]},
                "done": True, "eval_count": 3,
            }),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="\n".join(lines))

        _install_transport(provider, handler)
        chunks = [c async for c in provider.stream(_request())]

        assert chunks[-1].finish_reason is FinishReason.TOOL_CALLS
        assert chunks[-1].tool_calls[0].name == "calculator"

    async def test_an_http_error_surfaces_before_the_first_chunk(self, provider) -> None:
        """The port contract the router's failover depends on: after the first token it is too
        late to switch providers without duplicating output."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        _install_transport(provider, handler)
        with pytest.raises(ProviderError):
            async for _ in provider.stream(_request()):
                pytest.fail("a chunk was yielded before the error surfaced")

    async def test_the_stream_payload_sets_the_stream_flag(self, provider) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, text=json.dumps({"message": {"content": "x"},
                                                        "done": True}))

        _install_transport(provider, handler)
        [c async for c in provider.stream(_request())]
        assert captured["body"]["stream"] is True


class TestHealth:
    async def test_healthy_when_the_configured_model_is_installed(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"models": [{"name": "qwen2.5:7b-instruct-q4_K_M"}]}
            )

        _install_transport(provider, handler)
        status = await provider.health()
        assert status.healthy is True
        assert status.latency_ms >= 0.0

    async def test_a_missing_model_is_unhealthy_rather_than_a_slow_first_request(
        self, provider
    ) -> None:
        """Otherwise the first user request triggers a multi-gigabyte download inline."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": [{"name": "something-else:latest"}]})

        _install_transport(provider, handler)
        status = await provider.health()
        assert status.healthy is False
        assert "not installed" in status.detail

    async def test_an_http_error_is_reported_not_raised(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        _install_transport(provider, handler)
        status = await provider.health()
        assert status.healthy is False
        assert "500" in status.detail

    async def test_a_transport_failure_is_reported_not_raised(self, provider) -> None:
        """A health check that raises turns a degraded provider into a crashed gateway."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        _install_transport(provider, handler)
        status = await provider.health()
        assert status.healthy is False
        assert status.detail == "ConnectError"


class TestModelLifecycle:
    async def test_list_models_merges_installed_and_resident(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [
                    {"name": "qwen2.5:7b-instruct-q4_K_M", "size": 4_920_000_000,
                     "modified_at": "2026-01-02T03:04:05Z",
                     "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M",
                                 "family": "qwen2"}},
                    {"name": "llama3.1:8b", "size": 4_700_000_000},
                ]})
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct-q4_K_M"}]})

        _install_transport(provider, handler)
        models = await provider.list_models()

        assert [m.name for m in models] == ["qwen2.5:7b-instruct-q4_K_M", "llama3.1:8b"]
        assert models[0].loaded is True
        assert models[0].quantization == "Q4_K_M"
        assert models[0].family == "qwen2"
        assert models[1].loaded is False
        assert models[1].parameter_size == ""

    async def test_a_missing_ps_endpoint_does_not_fail_the_listing(self, provider) -> None:
        """/api/ps is newer than /api/tags; an older daemon must still be manageable."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ps":
                raise httpx.ConnectError("no such endpoint")
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

        _install_transport(provider, handler)
        models = await provider.list_models()
        assert len(models) == 1
        assert models[0].loaded is False

    async def test_a_non_200_from_ps_is_tolerated(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ps":
                return httpx.Response(404, text="not found")
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

        _install_transport(provider, handler)
        assert (await provider.list_models())[0].loaded is False

    async def test_a_failing_tags_call_raises(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        _install_transport(provider, handler)
        with pytest.raises(ProviderError):
            await provider.list_models()

    async def test_pulls_are_refused_when_the_allowlist_is_empty(self, provider) -> None:
        """Fails closed. An unset allowlist must not mean 'anything goes' for an operation that
        downloads and then executes third-party artefacts (SEC-01)."""
        with pytest.raises(AuthorizationError) as excinfo:
            async for _ in provider.pull_model("evil/backdoored:latest"):
                pytest.fail("the pull was permitted")
        assert "disabled" in str(excinfo.value)

    async def test_a_model_outside_the_allowlist_is_refused(self) -> None:
        guarded = OllamaProvider(model_allowlist=("qwen2.5:7b-instruct-q4_K_M",))
        with pytest.raises(AuthorizationError) as excinfo:
            async for _ in guarded.pull_model("evil/backdoored:latest"):
                pytest.fail("the pull was permitted")
        assert excinfo.value.details["allowlist"] == ["qwen2.5:7b-instruct-q4_K_M"]

    async def test_an_allowlisted_pull_streams_progress(self) -> None:
        guarded = OllamaProvider(model_allowlist=("llama3.1:8b",))
        lines = [
            json.dumps({"status": "pulling manifest"}),
            "",
            "{not json",
            json.dumps({"status": "downloading", "completed": 50, "total": 100}),
            json.dumps({"status": "success"}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content) == {"name": "llama3.1:8b"}
            return httpx.Response(200, text="\n".join(lines))

        _install_transport(guarded, handler)
        events = [e async for e in guarded.pull_model("llama3.1:8b")]

        assert [e["status"] for e in events] == ["pulling manifest", "downloading", "success"]

    async def test_a_failing_pull_raises_before_yielding(self) -> None:
        guarded = OllamaProvider(model_allowlist=("llama3.1:8b",))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="model not found in registry")

        _install_transport(guarded, handler)
        with pytest.raises(ProviderError):
            async for _ in guarded.pull_model("llama3.1:8b"):
                pytest.fail("progress was yielded despite the failure")

    async def test_delete_returns_true_on_success(self, provider) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={})

        _install_transport(provider, handler)
        assert await provider.delete_model("llama3.1:8b") is True
        assert captured["method"] == "DELETE"
        assert captured["body"] == {"name": "llama3.1:8b"}

    async def test_a_failing_delete_raises_rather_than_returning_false(self, provider) -> None:
        """A silent false would let an operator believe a model was removed when it was not."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        _install_transport(provider, handler)
        with pytest.raises(ProviderError):
            await provider.delete_model("never-installed")

    async def test_warm_sends_an_empty_prompt_with_a_keep_alive(self, provider) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"done": True})

        _install_transport(provider, handler)
        assert await provider.warm_model() is True
        assert captured["body"]["prompt"] == ""
        assert captured["body"]["keep_alive"] == "30m"
        assert captured["body"]["model"] == "qwen2.5:7b-instruct-q4_K_M"

    async def test_warm_reports_failure_without_raising(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="out of memory")

        _install_transport(provider, handler)
        assert await provider.warm_model("llama3.1:8b") is False

    async def test_a_transport_failure_while_warming_is_translated(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        _install_transport(provider, handler)
        with pytest.raises(ProviderError):
            await provider.warm_model()
