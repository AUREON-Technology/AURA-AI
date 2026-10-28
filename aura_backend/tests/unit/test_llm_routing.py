"""Provider routing, failover, circuit breaking, budgets, and caching (ADR-003)."""

from __future__ import annotations

import asyncio

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.domain import (
    CompletionRequest,
    Conversation,
    Message,
    ProviderCapabilities,
    RoutingPolicy,
    SamplingParams,
)
from aura_core.domain.errors import (
    AllProvidersFailedError,
    BudgetExceededError,
    ProviderError,
)
from aura_core.llm import InMemoryCache, LLMCache, ProviderRouter, TurnBudget
from aura_core.llm.circuit import CircuitBreaker, CircuitState, LatencyTracker
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn


def _caps(**overrides) -> ProviderCapabilities:
    base = dict(
        model_id="m", context_window=8192, max_output_tokens=512,
        supports_tools=True, supports_json_mode=True,
    )
    base.update(overrides)
    return ProviderCapabilities(**base)


def _request(**overrides) -> CompletionRequest:
    base = dict(
        conversation=Conversation.of([Message.user("hello")]),
        sampling=SamplingParams(temperature=0.0),
    )
    base.update(overrides)
    return CompletionRequest(**base)


class TestRoutingPolicies:
    @pytest.fixture
    def providers(self):
        local = EchoProvider(
            name="local", model="small", prefix="local: ",
            capabilities=_caps(model_id="small", is_local=True, quality_tier=1),
        )
        cheap = EchoProvider(
            name="cheap", model="mid", prefix="cheap: ",
            capabilities=_caps(model_id="mid", quality_tier=2, cost_per_1k_input_usd=0.0002),
        )
        premium = EchoProvider(
            name="premium", model="large", prefix="premium: ",
            capabilities=_caps(model_id="large", quality_tier=3, cost_per_1k_input_usd=0.01),
        )
        return local, cheap, premium

    def test_local_first_prefers_on_premise(self, providers) -> None:
        router = ProviderRouter(list(providers), default_policy=RoutingPolicy.LOCAL_FIRST)
        assert router.plan(_request()).candidates[0] == "local"

    def test_cheapest_prefers_zero_cost(self, providers) -> None:
        router = ProviderRouter(list(providers), default_policy=RoutingPolicy.CHEAPEST)
        assert router.plan(_request()).candidates[0] == "local"

    def test_quality_first_prefers_the_top_tier(self, providers) -> None:
        router = ProviderRouter(list(providers), default_policy=RoutingPolicy.QUALITY_FIRST)
        assert router.plan(_request()).candidates[0] == "premium"

    def test_per_request_policy_overrides_the_router_default(self, providers) -> None:
        router = ProviderRouter(list(providers), default_policy=RoutingPolicy.LOCAL_FIRST)
        plan = router.plan(_request(policy=RoutingPolicy.QUALITY_FIRST))
        assert plan.candidates[0] == "premium"

    def test_ordering_is_deterministic(self, providers) -> None:
        """Reproducible routing is what makes these tests meaningful at all."""
        router = ProviderRouter(list(providers), default_policy=RoutingPolicy.BALANCED)
        assert router.plan(_request()).candidates == router.plan(_request()).candidates


class TestCapabilityFiltering:
    def test_tool_requirement_excludes_incapable_providers(self) -> None:
        no_tools = EchoProvider(name="basic", model="b", capabilities=_caps(supports_tools=False))
        with_tools = EchoProvider(name="full", model="f", capabilities=_caps(supports_tools=True))
        router = ProviderRouter([no_tools, with_tools])

        plan = router.plan(_request(tools=({"name": "t", "description": "d", "parameters": {}},)))
        assert plan.candidates == ("full",)
        assert plan.excluded["basic"] == "no_tool_support"

    def test_context_window_excludes_providers_that_cannot_fit_the_prompt(self) -> None:
        small = EchoProvider(
            name="small", model="s",
            capabilities=_caps(context_window=512, max_output_tokens=128),
        )
        large = EchoProvider(name="large", model="l", capabilities=_caps(context_window=32_000))
        router = ProviderRouter([small, large])

        long_prompt = Conversation.of([Message.user("word " * 4000)])
        plan = router.plan(_request(conversation=long_prompt))
        assert "small" not in plan.candidates
        assert plan.excluded["small"] == "context_window_too_small"

    def test_require_local_excludes_cloud_providers(self) -> None:
        cloud = EchoProvider(name="cloud", model="c", capabilities=_caps(is_local=False))
        local = EchoProvider(name="local", model="l", capabilities=_caps(is_local=True))
        plan = ProviderRouter([cloud, local]).plan(_request(require_local=True))
        assert plan.candidates == ("local",)
        assert plan.excluded["cloud"] == "not_local"


class TestFailover:
    async def test_failover_to_the_next_candidate(self) -> None:
        broken = EchoProvider(
            name="broken", model="b",
            script=[ScriptedTurn(raise_error=ProviderError("down", provider="broken"))] * 5,
            capabilities=_caps(is_local=True),
        )
        healthy = EchoProvider(
            name="healthy", model="h", prefix="ok: ", capabilities=_caps(is_local=False)
        )
        router = ProviderRouter([broken, healthy], default_policy=RoutingPolicy.LOCAL_FIRST)

        response = await router.complete(_request())
        assert response.provider == "healthy"

    async def test_non_retryable_errors_stop_the_cascade(self) -> None:
        """A malformed request fails identically everywhere; retrying multiplies latency."""
        bad = EchoProvider(
            name="a", model="a",
            script=[ScriptedTurn(raise_error=ProviderError("bad request", provider="a", retryable=False))],
        )
        other = EchoProvider(name="b", model="b")
        router = ProviderRouter([bad, other], default_policy=RoutingPolicy.CHEAPEST)

        with pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(_request(policy=RoutingPolicy.CHEAPEST))
        assert len(excinfo.value.attempts) == 1

    async def test_exhausted_candidates_raise_rather_than_fabricating(self) -> None:
        """The legacy system falls through to hard-coded strings indistinguishable from a real
        answer (``services/agent.py:349``). An honest 503 is the correct behaviour."""
        broken = EchoProvider(
            name="only", model="o",
            script=[ScriptedTurn(raise_error=ProviderError("down", provider="only"))] * 5,
        )
        with pytest.raises(AllProvidersFailedError):
            await ProviderRouter([broken]).complete(_request())

    async def test_streaming_fails_over_before_the_first_token(self) -> None:
        broken = EchoProvider(
            name="broken", model="b",
            script=[ScriptedTurn(raise_error=ProviderError("down", provider="broken"))] * 5,
            capabilities=_caps(is_local=True),
        )
        healthy = EchoProvider(
            name="healthy", model="h", prefix="streamed: ", capabilities=_caps(is_local=False)
        )
        router = ProviderRouter([broken, healthy], default_policy=RoutingPolicy.LOCAL_FIRST)

        chunks = [c.delta async for c in router.stream(_request()) if c.delta]
        assert "".join(chunks).startswith("streamed: ")


class TestCircuitBreaker:
    def test_opens_after_consecutive_failures(self) -> None:
        clock = FrozenClock()
        breaker = CircuitBreaker(failure_threshold=3, open_seconds=30, now=clock.monotonic)

        for _ in range(3):
            breaker.record_failure()
        assert breaker.state is CircuitState.OPEN
        assert not breaker.allows_request()

    def test_half_opens_after_the_window_and_admits_one_probe(self) -> None:
        clock = FrozenClock()
        breaker = CircuitBreaker(failure_threshold=1, open_seconds=30, now=clock.monotonic)
        breaker.record_failure()
        assert breaker.state is CircuitState.OPEN

        clock.advance(31)
        assert breaker.state is CircuitState.HALF_OPEN
        assert breaker.allows_request()
        # Exactly one probe: letting full load through on a recovering provider is how a
        # breaker turns a partial outage into a thundering herd.
        assert not breaker.allows_request()

    def test_success_closes_the_circuit(self) -> None:
        clock = FrozenClock()
        breaker = CircuitBreaker(failure_threshold=1, open_seconds=10, now=clock.monotonic)
        breaker.record_failure()
        clock.advance(11)
        breaker.allows_request()
        breaker.record_success()
        assert breaker.state is CircuitState.CLOSED

    def test_failed_probe_reopens_immediately(self) -> None:
        clock = FrozenClock()
        breaker = CircuitBreaker(failure_threshold=2, open_seconds=10, now=clock.monotonic)
        breaker.record_failure()
        breaker.record_failure()
        clock.advance(11)
        breaker.allows_request()
        breaker.record_failure()
        assert breaker.state is CircuitState.OPEN

    async def test_open_circuit_removes_a_provider_from_candidacy(self) -> None:
        clock = FrozenClock()
        broken = EchoProvider(
            name="broken", model="b",
            script=[ScriptedTurn(raise_error=ProviderError("down", provider="broken"))] * 10,
            capabilities=_caps(is_local=True),
        )
        # Explicitly non-local: EchoProvider's default capabilities declare is_local=True
        # (it runs in-process), so without this both would tie under LOCAL_FIRST.
        healthy = EchoProvider(name="healthy", model="h", capabilities=_caps(is_local=False))
        router = ProviderRouter(
            [broken, healthy], default_policy=RoutingPolicy.LOCAL_FIRST,
            circuit_failure_threshold=2, clock=clock.monotonic,
        )

        for _ in range(3):
            await router.complete(_request())
        plan = router.plan(_request())
        assert plan.excluded.get("broken") == "circuit_open"

    def test_releasing_a_probe_admits_the_next_one_without_a_transition(self) -> None:
        clock = FrozenClock()
        breaker = CircuitBreaker(failure_threshold=1, open_seconds=30, now=clock.monotonic)
        breaker.record_failure()
        clock.advance(31)
        assert breaker.allows_request()
        assert not breaker.allows_request()

        breaker.release_probe()
        assert breaker.state is CircuitState.HALF_OPEN
        assert breaker.allows_request()


class TestAbandonedHalfOpenProbe:
    """A half-open probe that ends without a verdict must give its slot back (F-19).

    Only success and failure freed the slot, so a probe that was cancelled, closed by its
    consumer or answered with a 429 left the breaker refusing every later request as
    ``circuit_open``, with the provider long recovered, until the process restarted. Production
    runs one local provider, so that was a whole worker's LLM gone.
    """

    @staticmethod
    def _tripped(clock: FrozenClock, probe: ScriptedTurn, **router_kwargs) -> tuple[ProviderRouter, EchoProvider]:
        down = ScriptedTurn(raise_error=ProviderError("down", provider="local"))
        provider = EchoProvider(name="local", model="l", script=[down, down, probe])
        router = ProviderRouter(
            [provider], clock=clock.monotonic,
            circuit_failure_threshold=2, circuit_open_seconds=30, **router_kwargs,
        )
        return router, provider

    @staticmethod
    async def _trip(router: ProviderRouter, clock: FrozenClock) -> None:
        for _ in range(2):
            with pytest.raises(AllProvidersFailedError):
                await router.complete(_request())
        assert router.plan(_request()).excluded.get("local") == "circuit_open"
        clock.advance(31)

    async def test_a_cancelled_probe_does_not_lock_the_provider_out(self) -> None:
        clock = FrozenClock()
        # The probe hangs; it is cancelled long before the delay, so nothing actually sleeps.
        router, provider = self._tripped(clock, ScriptedTurn(text="late", delay_ms=3_600_000))
        await self._trip(router, clock)

        probe = asyncio.create_task(router.complete(_request()))
        while provider.call_count < 3:
            await asyncio.sleep(0)
        probe.cancel()  # e.g. the client disconnected mid-probe
        with pytest.raises(asyncio.CancelledError):
            await probe

        response = await router.complete(_request())
        assert response.provider == "local"
        assert router._stats["local"].breaker.state is CircuitState.CLOSED

    async def test_a_stream_probe_closed_by_its_consumer_does_not_lock_the_provider_out(
        self,
    ) -> None:
        clock = FrozenClock()
        router, _provider = self._tripped(clock, ScriptedTurn(text="a longer streamed answer"))
        await self._trip(router, clock)

        stream = router.stream(_request())
        assert (await stream.__anext__()).delta
        await stream.aclose()  # GeneratorExit inside the router: the consumer went away

        response = await router.complete(_request())
        assert response.provider == "local"

    async def test_a_rate_limited_probe_does_not_lock_the_provider_out(self) -> None:
        from aura_core.llm.quota import QuotaTracker

        clock = FrozenClock()
        limited = ScriptedTurn(raise_error=ProviderError(
            "slow down", provider="local", details={"status": 429, "retry_after_s": 5},
        ))
        router, _provider = self._tripped(
            clock, limited, quota=QuotaTracker(wall=clock.monotonic)
        )
        await self._trip(router, clock)

        with pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(_request())
        assert excinfo.value.attempts[0]["error"] == "rate_limited"

        clock.advance(60)  # past the cooldown the 429 named
        response = await router.complete(_request())
        assert response.provider == "local"


class TestLatencyTracker:
    def test_ewma_responds_to_recent_samples(self) -> None:
        tracker = LatencyTracker(alpha=0.5)
        tracker.observe(100)
        tracker.observe(200)
        assert 140 < tracker.value_ms < 160

    def test_unknown_latency_uses_the_supplied_prior(self) -> None:
        """A provider with no history must not sort first because "unknown" reads as zero."""
        assert LatencyTracker().estimate_ms(1500.0) == 1500.0


class TestTurnBudget:
    async def test_generation_budget_is_enforced(self) -> None:
        router = ProviderRouter([EchoProvider(name="e", model="e")])
        budget = TurnBudget(max_generations=2)

        await router.complete(_request(), budget=budget)
        await router.complete(_request(), budget=budget)
        with pytest.raises(BudgetExceededError):
            await router.complete(_request(), budget=budget)

    async def test_cost_budget_is_enforced(self) -> None:
        expensive = EchoProvider(
            name="e", model="e",
            capabilities=_caps(cost_per_1k_input_usd=1.0, cost_per_1k_output_usd=1.0),
        )
        router = ProviderRouter([expensive])
        budget = TurnBudget(max_generations=100, max_cost_usd=0.0001)

        await router.complete(_request(), budget=budget)
        with pytest.raises(BudgetExceededError):
            await router.complete(_request(), budget=budget)


class TestResponseCache:
    @pytest.fixture
    def llm_cache(self, embedder):
        return LLMCache(InMemoryCache(), embedder=embedder, similarity_threshold=0.9)

    async def test_exact_hit(self, llm_cache) -> None:
        router = ProviderRouter([EchoProvider(name="e", model="e", prefix="answer: ")])
        request = _request()
        response = await router.complete(request)

        await llm_cache.store(request, response, namespace="u1")
        hit = await llm_cache.lookup(request, namespace="u1")
        assert hit is not None and hit.tier == "exact"
        # A cache hit costs nothing; reporting the original cost would double-count spend.
        assert hit.response.cost_usd == 0.0
        assert hit.response.cached

    async def test_namespaces_isolate_users(self, llm_cache) -> None:
        """A response grounded in one user's documents must never reach another."""
        router = ProviderRouter([EchoProvider(name="e", model="e")])
        request = _request()
        await llm_cache.store(request, await router.complete(request), namespace="u1")
        assert await llm_cache.lookup(request, namespace="u2") is None

    async def test_sampled_generations_are_not_cached(self, llm_cache) -> None:
        """Caching a sample makes non-determinism inconsistent rather than absent."""
        assert not llm_cache.is_cacheable(_request(sampling=SamplingParams(temperature=0.7)))

    async def test_tool_turns_are_not_cached(self, llm_cache) -> None:
        """A replayed tool call would act on possibly-changed external state."""
        assert not llm_cache.is_cacheable(
            _request(tools=({"name": "t", "description": "d", "parameters": {}},))
        )

    async def test_whitespace_differences_hit_the_same_entry(self, llm_cache) -> None:
        router = ProviderRouter([EchoProvider(name="e", model="e")])
        original = _request(conversation=Conversation.of([Message.user("hello world")]))
        await llm_cache.store(original, await router.complete(original), namespace="u1")

        spaced = _request(conversation=Conversation.of([Message.user("hello   world")]))
        assert await llm_cache.lookup(spaced, namespace="u1") is not None

    async def test_corrupt_entries_are_a_miss_not_an_exception(self, embedder) -> None:
        backend = InMemoryCache()
        cache = LLMCache(backend, embedder=embedder)
        request = _request()
        key = cache._EXACT_PREFIX + cache._fingerprint(request, "u1")
        await backend.set(key, b"not json at all")
        assert await cache.lookup(request, namespace="u1") is None


class TestConcurrency:
    async def test_provider_concurrency_is_bounded(self) -> None:
        """A provider's declared max_concurrency is a real limit, not documentation."""
        slow = EchoProvider(
            name="slow", model="s",
            script=[ScriptedTurn(text="ok", delay_ms=30) for _ in range(10)],
            capabilities=_caps(max_concurrency=2),
        )
        router = ProviderRouter([slow])
        started = asyncio.get_event_loop().time()
        await asyncio.gather(*(router.complete(_request()) for _ in range(6)))
        elapsed = (asyncio.get_event_loop().time() - started) * 1000

        # Six requests at 30 ms with a concurrency of 2 cannot finish in under ~90 ms.
        assert elapsed >= 80, f"concurrency limit not enforced (finished in {elapsed:.0f}ms)"
