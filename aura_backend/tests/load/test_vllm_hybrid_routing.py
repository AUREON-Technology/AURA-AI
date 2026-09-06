"""Prove the Ollama+vLLM hybrid (docs/reports/OLLAMA_VLLM_COMPARISON.md §4, Option B) against
two real, genuinely different backends.

``tests/load/test_multi_model_routing.py`` (Phase G2) proved multi-provider routing for real, but
only with two *Ollama* instances -- the roadmap and readiness reports both flag that vLLM (and
llama.cpp) are "supported by protocol, not by having been tried" anywhere in this codebase. This
suite closes that specific gap: real health checks and real completions against a real
``OllamaProvider`` and a real ``OpenAICompatibleProvider`` pointed at a vLLM server, plus a
routing-policy check that a quality-sensitive request actually reaches the GPU-backed model.

Opt-in and skipped cleanly when the infrastructure is not there, the same way
``test_multi_model_routing.py`` and ``test_real_data_tier.py`` are. Run it once against the
compose ``vllm`` profile (or any other real vLLM server) before trusting the ``large`` provider
in production:

    docker compose -f deploy/docker-compose.prod.yml --profile gpu up -d vllm
    # wait for `docker compose ... ps vllm` to report healthy

    export AURA_HYBRID_TEST_OLLAMA_BASE_URL=http://localhost:11434
    export AURA_HYBRID_TEST_OLLAMA_MODEL=qwen2.5:3b-instruct-q4_K_M
    export AURA_HYBRID_TEST_VLLM_BASE_URL=http://localhost:8000/v1
    export AURA_HYBRID_TEST_VLLM_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ
    pytest tests/load -m load -s -k vllm_hybrid_routing

Both providers are configured ``is_local=True`` here, matching the deployment guidance in
``deploy/.env.example``: is_local means "your own infrastructure, not a vendor" (ADR-003), and a
GPU box you own satisfies that just as much as the CPU Ollama box. So this suite does not assert
that ``LOCAL_FIRST`` prefers Ollama -- with both providers local, it load-balances by measured
latency instead. What it does assert is that ``QUALITY_FIRST`` reaches the higher-tier vLLM model,
since that is the policy the hybrid setup is meant to use for quality-sensitive turns.
"""

from __future__ import annotations

import os

import pytest

from aura_core.domain.llm import CompletionRequest, ProviderCapabilities, RoutingPolicy, SamplingParams
from aura_core.domain.messages import Conversation, Message
from aura_core.llm.providers.ollama import OllamaProvider
from aura_core.llm.providers.openai_compatible import OpenAICompatibleProvider
from aura_core.llm.router import ProviderRouter

pytestmark = pytest.mark.load

_REQUIRED_ENV = (
    "AURA_HYBRID_TEST_OLLAMA_BASE_URL",
    "AURA_HYBRID_TEST_OLLAMA_MODEL",
    "AURA_HYBRID_TEST_VLLM_BASE_URL",
    "AURA_HYBRID_TEST_VLLM_MODEL",
)


def _missing_env() -> list[str]:
    return [key for key in _REQUIRED_ENV if not os.environ.get(key)]


@pytest.fixture
async def hybrid_providers():
    missing = _missing_env()
    if missing:
        pytest.skip(f"vLLM hybrid routing infra not configured, missing: {', '.join(missing)}")

    ollama_base_url = os.environ["AURA_HYBRID_TEST_OLLAMA_BASE_URL"]
    ollama_model = os.environ["AURA_HYBRID_TEST_OLLAMA_MODEL"]
    vllm_base_url = os.environ["AURA_HYBRID_TEST_VLLM_BASE_URL"]
    vllm_model = os.environ["AURA_HYBRID_TEST_VLLM_MODEL"]

    local = OllamaProvider(
        name="local",
        base_url=ollama_base_url,
        model=ollama_model,
        capabilities=ProviderCapabilities(
            model_id=ollama_model, context_window=8192, max_output_tokens=512,
            supports_streaming=True, is_local=True, quality_tier=1, max_concurrency=1,
        ),
    )
    large = OpenAICompatibleProvider(
        name="large",
        base_url=vllm_base_url,
        model=vllm_model,
        capabilities=ProviderCapabilities(
            model_id=vllm_model, context_window=8192, max_output_tokens=512,
            supports_streaming=True, supports_tools=True, is_local=True,
            quality_tier=3, max_concurrency=4,
        ),
        timeout_s=180.0,
    )
    try:
        yield local, large
    finally:
        await local.close()
        await large.close()


def _request(*, policy: RoutingPolicy | None = None) -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("Reply with exactly one word: hello")]),
        sampling=SamplingParams(max_output_tokens=16, temperature=0.0),
        policy=policy,
        timeout_s=180.0,
    )


class TestVllmHybridRouting:
    """Exit criterion: a real OllamaProvider and a real OpenAICompatibleProvider (vLLM) both
    answer, and QUALITY_FIRST routes a quality-sensitive request to the vLLM-backed model."""

    async def test_both_real_providers_answer(self, hybrid_providers) -> None:
        local, large = hybrid_providers
        for provider in (local, large):
            status = await provider.health()
            assert status.healthy, f"{provider.name}: {status.detail}"
            response = await provider.complete(_request())
            assert response.text.strip()
            assert response.provider == provider.name

    async def test_quality_first_prefers_the_vllm_model(self, hybrid_providers) -> None:
        local, large = hybrid_providers
        router = ProviderRouter(list((local, large)), default_policy=RoutingPolicy.BALANCED)

        decision = router.plan(_request(policy=RoutingPolicy.QUALITY_FIRST))
        assert decision.primary == large.name

        response = await router.complete(_request(policy=RoutingPolicy.QUALITY_FIRST))
        assert response.provider == large.name
        assert response.model == large.capabilities.model_id

    async def test_failover_reaches_the_real_vllm_provider(self, hybrid_providers) -> None:
        """Ollama is unreachable (connection refused, not a bad request) -- the router must fail
        over to the real, working vLLM provider and return real text."""
        _local, large = hybrid_providers
        unreachable = OllamaProvider(
            name="local-unreachable",
            base_url="http://localhost:1",
            model="unused",
            timeout_s=5.0,
            capabilities=ProviderCapabilities(
                model_id="unused", context_window=8192, max_output_tokens=512,
                is_local=True, quality_tier=1,
            ),
        )
        router = ProviderRouter([unreachable, large], default_policy=RoutingPolicy.BALANCED)
        try:
            response = await router.complete(_request())
        finally:
            await unreachable.close()

        assert response.provider == large.name
        assert response.text.strip()
