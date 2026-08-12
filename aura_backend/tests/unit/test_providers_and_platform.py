"""Provider wire-format adapters, rate limiting, reasoning strategies, and ML specs.

Provider adapters are exercised against a mock HTTP transport rather than a live endpoint: the
translation between AURA's domain objects and each vendor's wire format is where these adapters
can actually be wrong, and that is fully testable offline.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain import (
    CompletionRequest,
    Conversation,
    Message,
    ProviderCapabilities,
    SamplingParams,
    ToolCallId,
)
from aura_core.domain.errors import ProviderError
from aura_core.domain.llm import FinishReason
from aura_core.domain.messages import Role, ToolCall
from aura_core.llm import InMemoryCache
from aura_core.security import ROUTE_COSTS, RateLimitPolicy, SlidingWindowRateLimiter, extract_client_ip

httpx = pytest.importorskip("httpx")


def _request(conversation: Conversation | None = None, **overrides) -> CompletionRequest:
    base = dict(
        conversation=conversation or Conversation.of([Message.user("hello")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
    )
    base.update(overrides)
    return CompletionRequest(**base)


def _install_transport(provider, handler) -> None:
    """Give a provider a mocked HTTP client.

    Reaches past the lazy-client accessor deliberately: constructing the client is exactly what
    would otherwise require a network, and the behaviour under test is the translation, not the
    connection.
    """
    provider._client = httpx.AsyncClient(
        base_url=provider._base_url(),
        headers=dict(provider._headers()),
        transport=httpx.MockTransport(handler),
    )


class TestOpenAICompatibleAdapter:
    @pytest.fixture
    def provider(self):
        from aura_core.llm.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            name="vllm", base_url="https://inference.internal/v1", model="qwen2.5-7b",
            api_key="secret-key",
        )

    async def test_completion_is_translated_in_both_directions(self, provider) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200,
                json={
                    "model": "qwen2.5-7b",
                    "choices": [{"message": {"content": "42"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                },
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())

        assert response.text == "42"
        assert response.usage.prompt_tokens == 11
        assert captured["body"]["model"] == "qwen2.5-7b"
        assert captured["body"]["max_tokens"] == 64
        assert captured["auth"] == "Bearer secret-key"

    async def test_tool_calls_are_parsed(self, provider) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {
                            "content": None,
                            "tool_calls": [{
                                "id": "call_1",
                                "function": {"name": "get_weather",
                                             "arguments": '{"city": "Cairo"}'},
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                },
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())

        assert response.finish_reason is FinishReason.TOOL_CALLS
        assert response.tool_calls[0].name == "get_weather"
        assert response.tool_calls[0].arguments == {"city": "Cairo"}

    async def test_malformed_tool_arguments_do_not_raise_mid_stream(self, provider) -> None:
        """Small models emit invalid JSON often enough that raising would make them unusable.
        The call survives with a marker the registry rejects with a correctable message."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{
                    "message": {"tool_calls": [{
                        "id": "c", "function": {"name": "f", "arguments": "{not json"},
                    }]},
                    "finish_reason": "tool_calls",
                }]},
            )

        _install_transport(provider, handler)
        response = await provider.complete(_request())
        assert "_unparsed" in response.tool_calls[0].arguments

    async def test_streaming_reassembles_tool_call_fragments(self, provider) -> None:
        """Tool arguments arrive spread across chunks and must be joined by index."""
        def handler(request: httpx.Request) -> httpx.Response:
            frames = [
                {"choices": [{"delta": {"content": "Chec"}}]},
                {"choices": [{"delta": {"content": "king"}}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "c1",
                     "function": {"name": "get_weather", "arguments": '{"ci'}}
                ]}}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": 'ty": "Cairo"}'}}
                ]}, "finish_reason": "tool_calls"}]},
                {"usage": {"prompt_tokens": 5, "completion_tokens": 2}},
            ]
            body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
            return httpx.Response(200, content=body.encode())

        _install_transport(provider, handler)
        text, calls = "", ()
        async for chunk in provider.stream(_request()):
            text += chunk.delta
            if chunk.tool_calls:
                calls = chunk.tool_calls

        assert text == "Checking"
        assert calls[0].arguments == {"city": "Cairo"}

    async def test_http_errors_become_domain_errors(self, provider) -> None:
        _install_transport(provider, lambda r: httpx.Response(503, text="upstream down"))
        with pytest.raises(ProviderError) as excinfo:
            await provider.complete(_request())
        assert excinfo.value.retryable, "a 5xx must be retryable so the router fails over"

    async def test_client_errors_are_not_retryable(self, provider) -> None:
        _install_transport(provider, lambda r: httpx.Response(400, text="bad request"))
        with pytest.raises(ProviderError) as excinfo:
            await provider.complete(_request())
        assert not excinfo.value.retryable

    async def test_an_empty_choices_array_is_an_error_not_an_empty_answer(self, provider) -> None:
        _install_transport(provider, lambda r: httpx.Response(200, json={"choices": []}))
        with pytest.raises(ProviderError):
            await provider.complete(_request())


class TestRateLimiting:
    async def test_requests_are_weighted_by_what_they_consume(self) -> None:
        """A health check and a full agent turn are not the same request."""
        assert ROUTE_COSTS["health"] == 0
        assert ROUTE_COSTS["chat"] > ROUTE_COSTS["read"]
        assert ROUTE_COSTS["agent"] > ROUTE_COSTS["chat"]

    async def test_the_limit_is_enforced(self) -> None:
        limiter = SlidingWindowRateLimiter(
            InMemoryCache(), policies=[RateLimitPolicy(scope="user", limit=10)]
        )
        for _ in range(10):
            assert (await limiter.check(key="u1", scope="user")).allowed
        decision = await limiter.check(key="u1", scope="user")
        assert not decision.allowed
        assert decision.retry_after_s > 0

    async def test_cost_consumes_budget_faster(self) -> None:
        limiter = SlidingWindowRateLimiter(
            InMemoryCache(), policies=[RateLimitPolicy(scope="user", limit=10)]
        )
        assert (await limiter.check(key="u1", scope="user", cost=10)).allowed
        assert not (await limiter.check(key="u1", scope="user", cost=1)).allowed

    async def test_keys_are_isolated(self) -> None:
        limiter = SlidingWindowRateLimiter(
            InMemoryCache(), policies=[RateLimitPolicy(scope="user", limit=2)]
        )
        await limiter.check(key="u1", scope="user", cost=2)
        assert (await limiter.check(key="u2", scope="user")).allowed

    async def test_zero_cost_routes_are_exempt(self) -> None:
        limiter = SlidingWindowRateLimiter(
            InMemoryCache(), policies=[RateLimitPolicy(scope="user", limit=1)]
        )
        for _ in range(50):
            assert (await limiter.check_all(user_key="u", ip_key=None, route_class="health")).allowed

    async def test_a_backend_outage_fails_open_by_default(self) -> None:
        """A Redis outage should degrade rate limiting, not availability."""
        class BrokenCache:
            async def get(self, key): raise RuntimeError("redis down")
            async def incr(self, key, *, ttl_s=None): raise RuntimeError("redis down")
            async def set(self, key, value, *, ttl_s=None): raise RuntimeError("redis down")
            async def delete(self, key): raise RuntimeError("redis down")
            async def get_many(self, keys): raise RuntimeError("redis down")

        limiter = SlidingWindowRateLimiter(
            BrokenCache(), policies=[RateLimitPolicy(scope="user", limit=1)]
        )
        assert (await limiter.check(key="u", scope="user")).allowed

        strict = SlidingWindowRateLimiter(
            BrokenCache(), policies=[RateLimitPolicy(scope="user", limit=1)], fail_open=False
        )
        assert not (await strict.check(key="u", scope="user")).allowed


class TestClientIpResolution:
    def test_forged_leading_entries_are_ignored(self) -> None:
        """Taking the leftmost entry -- the common mistake -- lets a client spoof its address
        and evade per-IP limiting entirely.

        Nginx appends the peer address it actually observed (``$proxy_add_x_forwarded_for``), so
        with one trusted proxy the real client is the last entry; everything to its left is
        attacker-supplied.
        """
        headers = {"x-forwarded-for": "1.2.3.4, 203.0.113.9, 198.51.100.7"}
        assert extract_client_ip(headers, peer="10.0.0.1", trusted_proxies=1) == "198.51.100.7"

    def test_two_trusted_hops_step_back_two_entries(self) -> None:
        """With a CDN in front of nginx, each hop appends what it saw, so the client sits two
        entries from the right."""
        headers = {"x-forwarded-for": "1.2.3.4, 198.51.100.7, 203.0.113.9"}
        assert extract_client_ip(headers, peer="10.0.0.1", trusted_proxies=2) == "198.51.100.7"

    def test_a_spoofed_single_entry_does_not_win(self) -> None:
        headers = {"x-forwarded-for": "1.2.3.4"}
        assert extract_client_ip(headers, peer="10.0.0.1", trusted_proxies=1) == "1.2.3.4"
        # With two trusted proxies the chain is shorter than expected, so the peer wins.
        assert extract_client_ip(headers, peer="10.0.0.1", trusted_proxies=2) == "10.0.0.1"

    def test_no_proxy_configured_uses_the_peer(self) -> None:
        headers = {"x-forwarded-for": "1.2.3.4"}
        assert extract_client_ip(headers, peer="198.51.100.7", trusted_proxies=0) == "198.51.100.7"


class TestReasoningStrategySelection:
    def _request(self, question: str, **kwargs):
        from aura_core.reasoning.engine import ReasoningRequest

        return ReasoningRequest(
            question=question, conversation=Conversation.of([Message.user(question)]), **kwargs
        )

    @pytest.fixture
    def engine(self, router, prompts):
        from aura_core.reasoning.engine import ReasoningEngine

        return ReasoningEngine(router=router, prompts=prompts)

    def test_a_well_grounded_lookup_stays_direct(self, engine) -> None:
        """The work was done by retrieval; deliberating adds cost without accuracy."""
        from aura_core.domain.reasoning import ReasoningStrategy

        request = self._request("What is the leave policy?", retrieval_strength=0.85)
        assert engine.select_strategy(request) is ReasoningStrategy.DIRECT

    def test_arithmetic_escalates_to_self_consistency(self, engine) -> None:
        from aura_core.domain.reasoning import ReasoningStrategy

        request = self._request("How much is 15% of 1250 plus a 30 fee?")
        assert engine.select_strategy(request) is ReasoningStrategy.SELF_CONSISTENCY

    def test_an_explicit_strategy_always_wins(self, engine) -> None:
        from aura_core.domain.reasoning import ReasoningStrategy

        request = self._request("anything", strategy=ReasoningStrategy.TREE_OF_THOUGHTS)
        assert engine.select_strategy(request) is ReasoningStrategy.TREE_OF_THOUGHTS

    def test_strategy_cost_is_declared(self) -> None:
        from aura_core.domain.reasoning import ReasoningStrategy

        assert ReasoningStrategy.DIRECT.approximate_generations == 1
        assert (
            ReasoningStrategy.TREE_OF_THOUGHTS.approximate_generations
            > ReasoningStrategy.SELF_CONSISTENCY.approximate_generations
        )


class TestConfidenceCalibration:
    def test_confidence_blends_available_signals(self) -> None:
        from aura_core.domain.reasoning import ConfidenceAssessment

        strong = ConfidenceAssessment.from_signals(
            groundedness=0.9, retrieval_strength=0.8, verification_rate=1.0
        )
        weak = ConfidenceAssessment.from_signals(
            groundedness=0.1, retrieval_strength=0.2, verification_rate=0.0
        )
        assert strong.score > weak.score
        assert strong.band == "high" and weak.band == "low"

    def test_absent_signals_are_dropped_not_zeroed(self) -> None:
        """A turn with no retrieval should not be penalised for having no retrieval score."""
        from aura_core.domain.reasoning import ConfidenceAssessment

        assessment = ConfidenceAssessment.from_signals(groundedness=0.9)
        assert assessment.score == pytest.approx(0.9)

    def test_no_signals_yields_an_explicit_neutral(self) -> None:
        from aura_core.domain.reasoning import ConfidenceAssessment

        assessment = ConfidenceAssessment.from_signals()
        assert assessment.score == 0.5
        assert "no confidence signals" in assessment.rationale


class TestPlanningGate:
    @pytest.fixture
    def planner(self, router, prompts):
        from aura_core.planning import PlanningEngine

        return PlanningEngine(router=router, prompts=prompts)

    def test_simple_questions_do_not_pay_for_a_plan(self, planner) -> None:
        assert not planner.needs_plan("What is my leave balance?", available_tools=["search"])

    def test_no_tools_means_no_plan(self, planner) -> None:
        """Without tools a plan can only contain generate/respond steps, which is what a direct
        answer already is."""
        assert not planner.needs_plan(
            "First research the vendors and then compare them, and finally recommend one.",
            available_tools=[],
        )

    def test_multi_step_requests_are_planned(self, planner) -> None:
        assert planner.needs_plan(
            "First find the vendor contract in my files, and then summarise its termination "
            "clauses for me.",
            available_tools=["search_knowledge"],
        )

    def test_an_enumerated_request_is_planned(self, planner) -> None:
        assert planner.needs_plan(
            "Please do the following:\n1. Find the invoice\n2. Check the total\n3. Draft a reply",
            available_tools=["search_knowledge"],
        )


class TestPlanIntegrity:
    def test_dependency_cycles_are_rejected(self) -> None:
        from aura_core.domain import Goal, Plan, PlanId, PlanStep, StepId, StepKind
        from aura_core.domain.errors import PlanningError

        with pytest.raises(PlanningError):
            Plan(
                id=PlanId.new(), goal=Goal("x"),
                steps=(
                    PlanStep(id=StepId("a"), kind=StepKind.GENERATE, description="a",
                             depends_on=(StepId("b"),)),
                    PlanStep(id=StepId("b"), kind=StepKind.GENERATE, description="b",
                             depends_on=(StepId("a"),)),
                ),
            )

    def test_only_steps_with_satisfied_dependencies_are_ready(self) -> None:
        from aura_core.domain import Goal, Plan, PlanId, PlanStep, StepId, StepKind

        plan = Plan(
            id=PlanId.new(), goal=Goal("x"),
            steps=(
                PlanStep(id=StepId("a"), kind=StepKind.RETRIEVE, description="find"),
                PlanStep(id=StepId("b"), kind=StepKind.GENERATE, description="summarise",
                         depends_on=(StepId("a"),)),
            ),
        )
        assert [s.id.value for s in plan.ready_steps()] == ["a"]

        done = plan.with_step(plan.step(StepId("a")).started().succeeded("result", 1.0))
        assert [s.id.value for s in done.ready_steps()] == ["b"]

    def test_a_failed_dependency_blocks_rather_than_unblocks(self) -> None:
        """Running a dependent step without its input would produce a confident wrong answer."""
        from aura_core.domain import Goal, Plan, PlanId, PlanStep, StepId, StepKind

        plan = Plan(
            id=PlanId.new(), goal=Goal("x"),
            steps=(
                PlanStep(id=StepId("a"), kind=StepKind.RETRIEVE, description="find"),
                PlanStep(id=StepId("b"), kind=StepKind.GENERATE, description="summarise",
                         depends_on=(StepId("a"),)),
            ),
        )
        failed = plan.with_step(plan.step(StepId("a")).started().failed("timeout", 1.0))
        assert not failed.ready_steps()
        assert [s.id.value for s in failed.blocked_steps()] == ["b"]


class TestMLSpecifications:
    def test_parameter_counts_need_no_training_stack(self) -> None:
        """Sizing a model before deciding whether it fits the hardware must not require a GPU."""
        from aura_core.ml import Architecture, Task, TransformerSpec

        spec = TransformerSpec(
            architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION,
            vocab_size=32_000, d_model=384, n_heads=6, n_layers=4, n_classes=7,
        )
        assert spec.parameter_count > 10_000_000
        assert spec.estimated_fp32_mb > 40

    def test_incoherent_shapes_are_rejected_at_construction(self) -> None:
        from aura_core.domain.errors import ValidationError
        from aura_core.ml import Architecture, Task, TransformerSpec

        with pytest.raises(ValidationError):
            TransformerSpec(
                architecture=Architecture.TRANSFORMER, task=Task.CLASSIFICATION,
                d_model=100, n_heads=7,
            )

    def test_lstm_and_gru_gate_counts_differ(self) -> None:
        from aura_core.ml import Architecture, RecurrentSpec, Task

        common = dict(task=Task.SEQUENCE, input_size=128, hidden_size=256, n_layers=1)
        lstm = RecurrentSpec(architecture=Architecture.LSTM, **common)
        gru = RecurrentSpec(architecture=Architecture.GRU, **common)
        assert lstm.parameter_count > gru.parameter_count

    def test_specs_serialise_for_experiment_lineage(self) -> None:
        from aura_core.ml import Architecture, AutoencoderSpec, Task

        spec = AutoencoderSpec(
            architecture=Architecture.AUTOENCODER, task=Task.RECONSTRUCTION, input_dim=384
        )
        payload = json.loads(spec.to_json())
        assert payload["architecture"] == "autoencoder"
        assert payload["parameter_count"] > 0

    def test_materialising_without_torch_fails_clearly(self) -> None:
        from aura_core.domain.errors import ConfigurationError
        from aura_core.ml import Architecture, Task, TabularSpec, build, torch_available

        if torch_available():
            pytest.skip("torch is installed; the clear-failure path is unreachable")
        with pytest.raises(ConfigurationError) as excinfo:
            build(TabularSpec(architecture=Architecture.MLP, task=Task.CLASSIFICATION))
        assert "PyTorch is required" in str(excinfo.value)

    def test_the_catalogue_covers_the_requested_families(self) -> None:
        from aura_core.ml import CATALOGUE

        names = {cls.__name__ for cls in CATALOGUE}
        assert {
            "TransformerSpec", "CNNSpec", "RecurrentSpec", "AutoencoderSpec",
            "GraphNetworkSpec", "TimeSeriesSpec", "RecommenderSpec", "AnomalyDetectorSpec",
        } <= names
