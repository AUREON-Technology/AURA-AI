"""Phase G2: prove multi-model routing against two real, simultaneously-running providers.

``aura_core/llm/router.py`` implements five routing policies, and ``tests/unit/test_llm_routing.py``
tests their scoring, capability filtering, failover and circuit-breaker logic thoroughly -- but
only against synthetic/scripted providers. The one thing that suite structurally cannot prove is
that two *real* inference backends produce genuinely different, measured behaviour: every real run
this platform has ever had used exactly one real provider (Ollama) plus the echo provider. This
suite closes that gap the same way ``test_real_data_tier.py`` closes the "only ever tested against
in-memory adapters" gap: real infrastructure, opt-in, skipped cleanly when it is not there.

Point a single Ollama server at two pulled models and run explicitly:

    docker run -d --name routing-ollama -p 11434:11434 -v ollama_models:/root/.ollama ollama/ollama
    docker exec routing-ollama ollama pull smollm2:135m
    docker exec routing-ollama ollama pull qwen2.5:3b-instruct-q4_K_M

    export AURA_ROUTING_TEST_BASE_URL=http://localhost:11434
    export AURA_ROUTING_TEST_SMALL_MODEL=smollm2:135m
    export AURA_ROUTING_TEST_LARGE_MODEL=qwen2.5:3b-instruct-q4_K_M
    pytest tests/load -m load -s -k multi_model_routing

The two models are deliberately different in real size (135M vs 3B parameters) so that FASTEST and
QUALITY_FIRST have a genuine, not synthetic, signal to route on. CHEAPEST has no such signal here:
both are local models with a real cost of $0, so the router's own tie-break (name order) decides --
that is not a bug, it is what "cheapest" honestly means when nothing costs anything.
"""

from __future__ import annotations

import dataclasses
import os
import time

import pytest

from aura_core.domain.llm import CompletionRequest, ProviderCapabilities, RoutingPolicy, SamplingParams
from aura_core.domain.messages import Conversation, Message
from aura_core.llm.providers.ollama import OllamaProvider
from aura_core.llm.router import ProviderRouter

pytestmark = pytest.mark.load

_REQUIRED_ENV = (
    "AURA_ROUTING_TEST_BASE_URL",
    "AURA_ROUTING_TEST_SMALL_MODEL",
    "AURA_ROUTING_TEST_LARGE_MODEL",
)


def _missing_env() -> list[str]:
    return [key for key in _REQUIRED_ENV if not os.environ.get(key)]


@pytest.fixture
async def two_real_providers():
    missing = _missing_env()
    if missing:
        pytest.skip(f"multi-model routing infra not configured, missing: {', '.join(missing)}")

    base_url = os.environ["AURA_ROUTING_TEST_BASE_URL"]
    small_model = os.environ["AURA_ROUTING_TEST_SMALL_MODEL"]
    large_model = os.environ["AURA_ROUTING_TEST_LARGE_MODEL"]

    small = OllamaProvider(
        name="ollama-small",
        base_url=base_url,
        model=small_model,
        capabilities=ProviderCapabilities(
            model_id=small_model, context_window=8192, max_output_tokens=512,
            supports_streaming=True, is_local=True, quality_tier=1, max_concurrency=2,
        ),
    )
    large = OllamaProvider(
        name="ollama-large",
        base_url=base_url,
        model=large_model,
        capabilities=ProviderCapabilities(
            model_id=large_model, context_window=8192, max_output_tokens=512,
            supports_streaming=True, supports_tools=True, is_local=True,
            quality_tier=2, max_concurrency=2,
        ),
    )
    try:
        yield small, large
    finally:
        await small.close()
        await large.close()


def _request(*, policy: RoutingPolicy | None = None) -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("Reply with exactly one word: hello")]),
        sampling=SamplingParams(max_output_tokens=16, temperature=0.0),
        policy=policy,
        timeout_s=180.0,
    )


class TestRealMultiModelRouting:
    """Exit criterion: at least one policy visibly routes differently across two real models,
    driven by measured behaviour, not injected/synthetic timing."""

    async def test_both_real_providers_answer(self, two_real_providers) -> None:
        small, large = two_real_providers
        for provider in (small, large):
            status = await provider.health()
            assert status.healthy, f"{provider.name}: {status.detail}"
            response = await provider.complete(_request())
            assert response.text.strip()
            assert response.provider == provider.name

    async def test_fastest_policy_prefers_the_measurably_faster_model(self, two_real_providers) -> None:
        small, large = two_real_providers
        router = ProviderRouter(list((small, large)), default_policy=RoutingPolicy.BALANCED)

        # Prime real latency history for both providers -- FASTEST's score is `estimate_ms`,
        # which starts at a shared prior and only diverges once each provider has really run.
        for provider in (small, large):
            started = time.perf_counter()
            await provider.complete(_request())
            elapsed = (time.perf_counter() - started) * 1000.0
            router._stats[provider.name].latency.observe(elapsed)

        small_latency = router._stats[small.name].latency.estimate_ms(0.0)
        large_latency = router._stats[large.name].latency.estimate_ms(0.0)
        assert small_latency < large_latency, (
            f"expected the smaller model to be measurably faster: "
            f"small={small_latency:.0f}ms large={large_latency:.0f}ms"
        )

        decision = router.plan(_request(policy=RoutingPolicy.FASTEST))
        assert decision.primary == small.name

        response = await router.complete(_request(policy=RoutingPolicy.FASTEST))
        assert response.provider == small.name

    async def test_quality_first_prefers_the_higher_tier_model(self, two_real_providers) -> None:
        small, large = two_real_providers
        router = ProviderRouter(list((small, large)), default_policy=RoutingPolicy.BALANCED)

        decision = router.plan(_request(policy=RoutingPolicy.QUALITY_FIRST))
        assert decision.primary == large.name

        response = await router.complete(_request(policy=RoutingPolicy.QUALITY_FIRST))
        assert response.provider == large.name
        assert response.model == large.capabilities.model_id

    async def test_failover_reaches_the_second_real_provider(self, two_real_providers) -> None:
        """The primary candidate is unreachable (connection refused, not a bad request) -- the
        router must fail over to the real, working provider and return real text."""
        small, large = two_real_providers
        unreachable = OllamaProvider(
            name="ollama-unreachable",
            base_url="http://localhost:1",
            model=large.capabilities.model_id,
            timeout_s=5.0,
            capabilities=dataclasses.replace(large.capabilities, quality_tier=3),
        )
        router = ProviderRouter([unreachable, small], default_policy=RoutingPolicy.QUALITY_FIRST)
        try:
            response = await router.complete(_request())
        finally:
            await unreachable.close()

        assert response.provider == small.name
        assert response.text.strip()
