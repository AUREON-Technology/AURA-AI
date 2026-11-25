"""Capability-aware routing, admission control, and latency decomposition (roadmap P2.2-P2.5).

Everything here runs on the echo provider and a ``FrozenClock``: queue timeouts are fired by
advancing the clock and calling ``expire_overdue()``, generation time is simulated by a provider
that advances the same clock, and nothing sleeps. ``asyncio.sleep(0)`` below is a scheduler yield
(let queued tasks reach their ``await``), not a wait.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.config.settings import LLMSettings, ProviderConfig, load_settings
from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import (
    AllProvidersFailedError,
    ConfigurationError,
    OverloadedError,
    ProviderError,
    UserConcurrencyLimitError,
)
from aura_core.domain.llm import (
    CompletionResponse,
    GenerationTiming,
    Modality,
    ModelPool,
    RoutingPolicy,
    SamplingParams,
    StreamChunk,
    TokenUsage,
    WorkloadClass,
    WorkloadHint,
)
from aura_core.llm import (
    AdmissionController,
    PoolLimits,
    ProviderRouter,
    admission_limits_from_settings,
    capabilities_from_config,
    current_workload,
    workload_for_turn,
    workload_scope,
)
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.observability.metrics import PROMETHEUS_AVAILABLE, Metrics


def _caps(**overrides) -> ProviderCapabilities:
    base = dict(model_id="m", context_window=8192, max_output_tokens=512, supports_tools=True)
    base.update(overrides)
    return ProviderCapabilities(**base)


def _request(hint: WorkloadHint | None = None, **overrides) -> CompletionRequest:
    base = dict(
        conversation=Conversation.of([Message.user("hello there")]),
        sampling=SamplingParams(temperature=0.0),
        workload=hint,
    )
    base.update(overrides)
    return CompletionRequest(**base)


async def _settle(rounds: int = 10) -> None:
    """Yield to the event loop so woken tasks run. Not a timed wait."""
    for _ in range(rounds):
        await asyncio.sleep(0)


class GatedProvider(EchoProvider):
    """Echo provider whose generations block until the test opens the gate.

    Holds an admission slot for exactly as long as the test wants, with no timing involved.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gate = asyncio.Event()
        self.started = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.started += 1
        await self.gate.wait()
        return await super().complete(request)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        self.started += 1
        await self.gate.wait()
        async for chunk in super().stream(request):
            yield chunk


class ClockedProvider(EchoProvider):
    """Streams fixed chunks, advancing a FrozenClock to simulate prefill and decode time."""

    def __init__(self, clock: FrozenClock, *, prefill_s: float, per_chunk_s: float, chunks: int,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._clock = clock
        self._prefill = prefill_s
        self._per_chunk = per_chunk_s
        self._chunks = chunks

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self._clock.advance(self._prefill + self._per_chunk * self._chunks)
        return CompletionResponse(
            text="x" * self._chunks, provider=self.name, model=self._model,
            usage=TokenUsage(prompt_tokens=40, completion_tokens=self._chunks),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        self._clock.advance(self._prefill)
        for i in range(self._chunks):
            if i:
                self._clock.advance(self._per_chunk)
            yield StreamChunk(delta="x", provider=self.name, model=self._model)
        yield StreamChunk(
            finish_reason=None, usage=TokenUsage(prompt_tokens=40, completion_tokens=self._chunks)
        )


# ---------------------------------------------------------------------------------------------
# P2.3: model-level metadata and backward compatibility
# ---------------------------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_legacy_provider_env_produces_undeclared_metadata(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_LLM_PROVIDERS", "local")
        monkeypatch.setenv("AURA_PROVIDER_LOCAL_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_LOCAL_MAX_CONCURRENCY", "1")
        settings = load_settings(refresh=True, validate=False)
        caps = capabilities_from_config(settings.llm.providers[0])

        assert caps.pools == ()
        assert caps.speed_tier == 2
        assert caps.modalities == (Modality.TEXT,)
        assert caps.languages == ()
        assert caps.memory_cost == caps.gpu_cost == 0.0
        assert caps.is_generative

        # Admission defaults reproduce the pre-P2.4 behaviour: the declared max_concurrency is
        # the cap, the queue is unbounded, and nothing times out.
        assert settings.llm.admission_max_queue == -1
        assert settings.llm.admission_queue_timeout_s == 0.0
        assert settings.llm.user_max_concurrent_generations == 0
        assert admission_limits_from_settings(settings.llm) == {
            "local": PoolLimits(max_concurrent=1, max_queue=-1, queue_timeout_s=0.0)
        }

    def test_hints_do_not_reorder_generalist_providers(self) -> None:
        """Every provider configured before pools existed is a generalist; a pool hint (which
        the kernel now always sends) must leave their policy ordering exactly as it was."""
        providers = [
            EchoProvider(name="local", capabilities=_caps(is_local=True, quality_tier=1)),
            EchoProvider(name="remote", capabilities=_caps(is_local=False, quality_tier=3)),
        ]
        for policy in RoutingPolicy:
            router = ProviderRouter(providers, default_policy=policy)
            unhinted = router.plan(_request())
            for pool in (ModelPool.GENERAL_FAST, ModelPool.CODER):
                hinted = router.plan(_request(WorkloadHint(pool=pool)))
                assert hinted.candidates == unhinted.candidates, (policy, pool)
                assert not hinted.excluded

    def test_new_metadata_is_read_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_LLM_PROVIDERS", "coder")
        p = "AURA_PROVIDER_CODER_"
        for key, value in {
            "KIND": "openai_compatible", "BASE_URL": "http://vllm-coder:8000/v1",
            "MODEL": "Qwen/Qwen2.5-Coder-7B-Instruct", "POOLS": "coder, reasoning",
            "SPEED_TIER": "3", "GPU_COST": "1.5", "MEMORY_COST": "2",
            "MODALITIES": "text", "SUPPORTS_REASONING": "true", "LANGUAGES": "en,ar",
            "MODEL_REVISION": "abc123", "CAPACITY_GROUP": "gpu0", "MAX_CONCURRENCY": "16",
            "MAX_QUEUE": "32", "QUEUE_TIMEOUT_S": "20",
        }.items():
            monkeypatch.setenv(p + key, value)
        monkeypatch.setenv("AURA_LLM_ADMISSION_MAX_QUEUE", "4")
        settings = load_settings(refresh=True, validate=False)
        caps = capabilities_from_config(settings.llm.providers[0])

        assert caps.pools == (ModelPool.CODER, ModelPool.REASONING)
        assert caps.speed_tier == 3 and caps.supports_reasoning
        assert caps.languages == ("en", "ar")
        assert caps.model_revision == "abc123"
        assert caps.capacity_group == "gpu0"
        # The provider's own queue settings override the LLM-wide default.
        assert admission_limits_from_settings(settings.llm) == {
            "gpu0": PoolLimits(max_concurrent=16, max_queue=32, queue_timeout_s=20.0)
        }

    def test_unknown_pool_fails_at_boot(self) -> None:
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="x", kind="echo", model="e", pools=("codr",))

    def test_shared_capacity_group_takes_the_smallest_cap(self) -> None:
        settings = LLMSettings(
            providers=(
                ProviderConfig(name="a", kind="echo", model="a", max_concurrency=4,
                               capacity_group="ollama"),
                ProviderConfig(name="b", kind="echo", model="b", max_concurrency=1,
                               capacity_group="ollama"),
            ),
            admission_queue_timeout_s=30.0,
        )
        limits = admission_limits_from_settings(settings)
        assert limits == {"ollama": PoolLimits(max_concurrent=1, max_queue=-1, queue_timeout_s=30.0)}


# ---------------------------------------------------------------------------------------------
# P2.2: capability-aware filtering and scoring
# ---------------------------------------------------------------------------------------------


class TestCapabilityRouting:
    @pytest.fixture
    def pools(self):
        general = EchoProvider(name="general", capabilities=_caps(
            pools=(ModelPool.GENERAL_FAST, ModelPool.GENERAL_QUALITY), is_local=True))
        coder = EchoProvider(name="coder", capabilities=_caps(pools=(ModelPool.CODER,), is_local=True))
        embed = EchoProvider(name="embed", capabilities=_caps(pools=(ModelPool.EMBEDDING,)))
        ollama = EchoProvider(name="ollama", capabilities=_caps(is_local=True, quality_tier=1))
        return [general, coder, embed, ollama]

    def test_pool_members_come_first_then_generalist_fallbacks(self, pools) -> None:
        plan = ProviderRouter(pools).plan(_request(WorkloadHint(pool=ModelPool.CODER)))
        assert plan.candidates[0] == "coder"
        assert plan.pool_members == ("coder",)
        # The general pool and the no-pool Ollama generalist remain as failover.
        assert set(plan.candidates[1:]) == {"general", "ollama"}
        assert plan.excluded["embed"] == "not_generative"

    def test_a_non_fallback_pool_never_serves_another_pool(self, pools) -> None:
        plan = ProviderRouter(pools).plan(_request(WorkloadHint(pool=ModelPool.GENERAL_FAST)))
        assert plan.candidates[0] == "general"
        assert "coder" not in plan.candidates
        assert plan.excluded["coder"] == "pool_mismatch"

    def test_strict_pool_refuses_fallbacks(self, pools) -> None:
        plan = ProviderRouter(pools).plan(
            _request(WorkloadHint(pool=ModelPool.CODER, strict_pool=True))
        )
        assert plan.candidates == ("coder",)
        assert plan.excluded["ollama"] == "pool_mismatch"

    async def test_an_open_pool_circuit_falls_back_to_generalists(self, clock) -> None:
        coder = EchoProvider(name="coder", prefix="coder: ", capabilities=_caps(pools=(ModelPool.CODER,)),
                             script=[ScriptedTurn(raise_error=ProviderError("down", provider="coder"))] * 5)
        general = EchoProvider(name="general", prefix="general: ", capabilities=_caps())
        router = ProviderRouter([coder, general], circuit_failure_threshold=1, clock=clock.monotonic)
        response = await router.complete(_request(WorkloadHint(pool=ModelPool.CODER)))
        assert response.provider == "general"
        plan = router.plan(_request(WorkloadHint(pool=ModelPool.CODER)))
        assert plan.excluded["coder"] == "circuit_open"
        assert plan.candidates == ("general",)

    def test_modality_language_reasoning_and_context_filters(self) -> None:
        text_en = EchoProvider(name="text_en", capabilities=_caps(
            pools=(ModelPool.GENERAL_FAST,), languages=("en",)))
        vision = EchoProvider(name="vision", capabilities=_caps(
            pools=(ModelPool.VISION, ModelPool.GENERAL_FAST), supports_vision=True,
            supports_reasoning=True, context_window=32_768))
        router = ProviderRouter([text_en, vision])

        assert router.plan(_request(WorkloadHint(modalities=(Modality.IMAGE,)))).candidates == ("vision",)
        assert router.plan(_request(WorkloadHint(language="ar"))).excluded["text_en"] == "language_unsupported"
        assert "text_en" in router.plan(_request(WorkloadHint(language="en-GB"))).candidates
        assert router.plan(_request(WorkloadHint(needs_reasoning=True))).candidates == ("vision",)
        assert router.plan(_request(WorkloadHint(min_context=16_000))).candidates == ("vision",)

    def test_undeclared_language_is_not_a_restriction(self) -> None:
        plain = EchoProvider(name="plain", capabilities=_caps())
        assert ProviderRouter([plain]).plan(_request(WorkloadHint(language="ar"))).candidates == ("plain",)

    def test_cheapest_uses_resource_cost_between_free_models(self) -> None:
        big = EchoProvider(name="big", capabilities=_caps(gpu_cost=4.0, memory_cost=4.0))
        small = EchoProvider(name="small", capabilities=_caps(gpu_cost=1.0, memory_cost=1.0))
        assert ProviderRouter([big, small], default_policy=RoutingPolicy.CHEAPEST).plan(
            _request()).candidates == ("small", "big")

    def test_fastest_uses_speed_tier_as_the_prior(self) -> None:
        slow = EchoProvider(name="a_slow", capabilities=_caps(speed_tier=1))
        fast = EchoProvider(name="b_fast", capabilities=_caps(speed_tier=3))
        assert ProviderRouter([slow, fast], default_policy=RoutingPolicy.FASTEST).plan(
            _request()).candidates == ("b_fast", "a_slow")

    def test_quality_first_orders_within_the_pool(self) -> None:
        q1 = EchoProvider(name="q1", capabilities=_caps(pools=(ModelPool.CODER,), quality_tier=1))
        q3 = EchoProvider(name="q3", capabilities=_caps(pools=(ModelPool.CODER,), quality_tier=3))
        generalist = EchoProvider(name="gen", capabilities=_caps(quality_tier=3))
        plan = ProviderRouter([q1, q3, generalist], default_policy=RoutingPolicy.QUALITY_FIRST).plan(
            _request(WorkloadHint(pool=ModelPool.CODER)))
        # Pool membership outranks tier: a tier-3 generalist is still only the fallback.
        assert plan.candidates == ("q3", "q1", "gen")

    def test_queued_load_counts_against_a_provider(self) -> None:
        a = EchoProvider(name="a", capabilities=_caps(max_concurrency=1))
        b = EchoProvider(name="b", capabilities=_caps(max_concurrency=1))
        router = ProviderRouter([a, b], default_policy=RoutingPolicy.BALANCED)
        assert router.plan(_request()).candidates[0] == "a"
        router.admission._group("a").inflight = 1  # a is busy
        assert router.plan(_request()).candidates[0] == "b"


class TestWorkloadClassification:
    def test_turn_classification_table(self) -> None:
        eng = workload_for_turn(route="chat", agent="engineer", user_key="u")
        assert (eng.workload, eng.pool, eng.priority) == (WorkloadClass.ENGINEER, ModelPool.CODER, 2)
        voice = workload_for_turn(route="voice_live", agent="orchestrator", user_key="u")
        assert (voice.workload, voice.priority) == (WorkloadClass.VOICE, 1)
        chat = workload_for_turn(route="chat", agent="orchestrator", user_key="u")
        assert (chat.pool, chat.priority, chat.user_key) == (ModelPool.GENERAL_FAST, 1, "u")
        forced = workload_for_turn(route="chat", agent=None, user_key=None,
                                   workload=WorkloadClass.RAG_INDEXING)
        assert forced.priority == 4
        assert WorkloadClass.TRAINING.priority == 5
        assert WorkloadClass.BACKGROUND_INSIGHT.priority == 3

    async def test_kernel_turn_sets_the_ambient_hint_for_every_generation(self, container) -> None:
        """Planner, reasoner and generation calls all see the turn's hint -- including the
        user key the quota needs -- without any engine signature changing."""
        from aura_core.domain.identifiers import UserId
        from aura_core.kernel.kernel import TurnRequest

        seen: list[WorkloadHint | None] = []
        provider = container.providers[0]
        original = provider.complete

        async def spy(request):
            seen.append(current_workload())
            return await original(request)

        provider.complete = spy  # type: ignore[method-assign]
        result = await container.kernel.run(
            TurnRequest(message="hello", user_id=UserId("u-1"), use_tools=False)
        )
        assert result.telemetry.outcome.value == "success"
        assert seen and all(h is not None and h.user_key == "u-1" for h in seen)
        assert all(h.workload is WorkloadClass.INTERACTIVE for h in seen)
        assert current_workload() is None  # nothing leaks out of the turn

    def test_request_hint_wins_over_ambient(self) -> None:
        from aura_core.llm.workload import effective_workload

        explicit = WorkloadHint(workload=WorkloadClass.TRAINING)
        with workload_scope(WorkloadHint(workload=WorkloadClass.VOICE)):
            assert effective_workload(_request(explicit)).workload is WorkloadClass.TRAINING
            assert effective_workload(_request()).workload is WorkloadClass.VOICE
        assert effective_workload(_request()).workload is WorkloadClass.INTERACTIVE


# ---------------------------------------------------------------------------------------------
# P2.4: admission control and backpressure
# ---------------------------------------------------------------------------------------------


class TestAdmissionController:
    async def test_higher_priority_is_admitted_first_when_capacity_frees(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=1)}, clock=clock.monotonic)
        held = await ctl.acquire("g", priority=1)
        order: list[str] = []

        async def wait(priority: int, tag: str):
            ticket = await ctl.acquire("g", priority=priority)
            order.append(tag)
            return ticket

        arrivals = [(5, "training"), (3, "insight"), (1, "chat-a"), (4, "indexing"),
                    (2, "engineer"), (1, "chat-b")]
        tasks = {tag: asyncio.create_task(wait(p, tag)) for p, tag in arrivals}
        await _settle()
        assert ctl.queued("g") == 6 and ctl.inflight("g") == 1

        ctl.release(held)
        for _ in arrivals:
            await _settle()
            ctl.release(await tasks[order[-1]])
        # Priority first, FIFO within a priority.
        assert order == ["chat-a", "chat-b", "engineer", "insight", "indexing", "training"]
        assert ctl.inflight("g") == 0 and ctl.queued("g") == 0

    async def test_full_queue_refuses_with_retry_after(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=1, max_queue=1)},
                                  clock=clock.monotonic)
        held = await ctl.acquire("g")
        queued = asyncio.create_task(ctl.acquire("g", priority=1))
        await _settle()
        with pytest.raises(OverloadedError) as info:
            await ctl.acquire("g", priority=1)
        assert info.value.reason == "queue_full"
        assert info.value.http_status == 503
        assert info.value.retry_after >= 1
        ctl.release(held)
        ctl.release(await queued)

    async def test_urgent_request_preempts_the_least_urgent_waiter(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=1, max_queue=1)},
                                  clock=clock.monotonic)
        held = await ctl.acquire("g")
        background = asyncio.create_task(ctl.acquire("g", priority=4))
        await _settle()
        chat = asyncio.create_task(ctl.acquire("g", priority=1))
        await _settle()
        with pytest.raises(OverloadedError) as info:
            await background
        assert info.value.reason == "preempted"
        ctl.release(held)
        ctl.release(await chat)
        assert ctl.inflight("g") == 0

    async def test_queue_timeout_via_frozen_clock(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=1, queue_timeout_s=30)},
                                  clock=clock.monotonic)
        held = await ctl.acquire("g")
        waiter = asyncio.create_task(ctl.acquire("g"))
        await _settle()
        clock.advance(29)
        assert ctl.expire_overdue() == 0
        clock.advance(2)
        assert ctl.expire_overdue() == 1
        with pytest.raises(OverloadedError) as info:
            await waiter
        assert info.value.reason == "queue_timeout"
        assert ctl.queued("g") == 0
        ctl.release(held)
        assert ctl.inflight("g") == 0

    async def test_cancelled_waiter_leaves_the_queue_and_leaks_nothing(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=1)}, clock=clock.monotonic)
        held = await ctl.acquire("g")
        waiter = asyncio.create_task(ctl.acquire("g"))
        await _settle()
        waiter.cancel()
        await _settle()
        assert waiter.cancelled()
        assert ctl.queued("g") == 0
        ctl.release(held)
        assert ctl.inflight("g") == 0
        # Capacity is fully usable afterwards.
        again = await ctl.acquire("g")
        ctl.release(again)

    async def test_waiter_cancelled_after_grant_returns_the_slot(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=1)}, clock=clock.monotonic)
        held = await ctl.acquire("g")
        waiter = asyncio.create_task(ctl.acquire("g"))
        await _settle()
        ctl.release(held)          # grants the slot to the waiter (future resolved) ...
        waiter.cancel()            # ... which is cancelled before its task resumes
        await _settle()
        assert ctl.inflight("g") == 0

    async def test_release_is_idempotent(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=2)}, clock=clock.monotonic)
        ticket = await ctl.acquire("g")
        ctl.release(ticket)
        ctl.release(ticket)
        assert ctl.inflight("g") == 0

    async def test_per_user_quota(self, clock) -> None:
        ctl = AdmissionController({"g": PoolLimits(max_concurrent=4)},
                                  user_max_concurrent=1, clock=clock.monotonic)
        first = await ctl.acquire("g", user_key="alice")
        # At quota: Alice's second request waits even though the pool has free slots ...
        second = asyncio.create_task(ctl.acquire("g", user_key="alice"))
        await _settle()
        assert not second.done() and ctl.queued("g") == 1
        # ... a third is refused outright (she already has as many queued as running) ...
        with pytest.raises(UserConcurrencyLimitError) as info:
            await ctl.acquire("g", user_key="alice")
        assert info.value.http_status == 429 and info.value.retry_after >= 1
        # ... and Bob is not held up behind her.
        bob = await ctl.acquire("g", user_key="bob")
        ctl.release(first)
        await _settle()
        assert second.done()
        ctl.release(await second)
        ctl.release(bob)
        assert ctl.user_inflight("alice") == 0 and ctl.inflight("g") == 0


class TestRouterAdmission:
    async def test_cancelled_generation_releases_its_slot(self, clock) -> None:
        gated = GatedProvider(name="gated", capabilities=_caps(max_concurrency=1))
        router = ProviderRouter([gated], clock=clock.monotonic)
        running = asyncio.create_task(router.complete(_request()))
        queued = asyncio.create_task(router.complete(_request()))
        await _settle()
        assert gated.started == 1 and router.admission.queued("gated") == 1

        running.cancel()       # client disconnects mid-generation
        await _settle()
        assert running.cancelled()
        assert gated.started == 2  # the queued request got the freed slot
        gated.gate.set()
        await queued
        assert router.admission.inflight("gated") == 0

    async def test_abandoned_stream_releases_its_slot(self, clock) -> None:
        provider = EchoProvider(name="p", chunk_size=1, capabilities=_caps(max_concurrency=1))
        router = ProviderRouter([provider], clock=clock.monotonic)
        stream = router.stream(_request())
        first = await stream.__anext__()
        assert first.delta and router.admission.inflight("p") == 1
        await stream.aclose()  # consumer stops reading, e.g. SSE client went away
        assert router.admission.inflight("p") == 0

    async def test_saturation_does_not_fail_over(self, clock) -> None:
        """LOCAL_FIRST must not spill to the remote provider just because local is busy."""
        local = GatedProvider(name="local", capabilities=_caps(is_local=True, max_concurrency=1))
        remote = EchoProvider(name="remote", capabilities=_caps(is_local=False))
        router = ProviderRouter([local, remote], default_policy=RoutingPolicy.LOCAL_FIRST,
                                default_max_queue=0, clock=clock.monotonic)
        busy = asyncio.create_task(router.complete(_request()))
        await _settle()
        with pytest.raises(OverloadedError) as info:
            await router.complete(_request())
        assert info.value.pool == "local"
        assert remote.call_count == 0
        # Saturation is not a provider failure: the breaker stays closed.
        assert router.stats()[0]["failures"] == 0
        local.gate.set()
        await busy

    async def test_router_queue_timeout(self, clock) -> None:
        gated = GatedProvider(name="g", capabilities=_caps(max_concurrency=1))
        router = ProviderRouter([gated], default_queue_timeout_s=10, clock=clock.monotonic)
        busy = asyncio.create_task(router.complete(_request()))
        waiting = asyncio.create_task(router.complete(_request()))
        await _settle()
        clock.advance(11)
        router.admission.expire_overdue()
        with pytest.raises(OverloadedError) as info:
            await waiting
        assert info.value.reason == "queue_timeout"
        gated.gate.set()
        await busy
        assert router.admission.inflight("g") == 0 and router.admission.queued("g") == 0

    async def test_stream_failover_still_only_before_first_token(self, clock) -> None:
        broken = EchoProvider(name="broken", capabilities=_caps(is_local=True),
                              script=[ScriptedTurn(raise_error=ProviderError("down", provider="broken"))])
        healthy = EchoProvider(name="healthy", prefix="ok: ", capabilities=_caps(is_local=False))
        router = ProviderRouter([broken, healthy], default_policy=RoutingPolicy.LOCAL_FIRST,
                                clock=clock.monotonic)
        text = "".join([c.delta async for c in router.stream(_request())])
        assert text.startswith("ok: ")
        assert router.admission.inflight("broken") == 0 and router.admission.inflight("healthy") == 0

        class MidStreamFailure(EchoProvider):
            async def stream(self, request):
                yield StreamChunk(delta="partial", provider=self.name, model=self._model)
                raise ProviderError("dropped", provider=self.name)

        flaky = MidStreamFailure(name="flaky", capabilities=_caps(is_local=True))
        backup = EchoProvider(name="backup", capabilities=_caps(is_local=False))
        router = ProviderRouter([flaky, backup], default_policy=RoutingPolicy.LOCAL_FIRST,
                                clock=clock.monotonic)
        seen: list[str] = []
        with pytest.raises(ProviderError) as info:
            async for chunk in router.stream(_request()):
                seen.append(chunk.delta)
        assert seen == ["partial"]
        assert info.value.details.get("partial_output") is True
        assert backup.call_count == 0
        assert router.admission.inflight("flaky") == 0

    async def test_exhausted_providers_still_raise_all_failed(self, clock) -> None:
        broken = EchoProvider(name="b", capabilities=_caps(),
                              script=[ScriptedTurn(raise_error=ProviderError("down", provider="b"))])
        with pytest.raises(AllProvidersFailedError):
            await ProviderRouter([broken], clock=clock.monotonic).complete(_request())


# ---------------------------------------------------------------------------------------------
# P2.5: latency decomposition
# ---------------------------------------------------------------------------------------------


class _Recorder:
    """Stands in for the metric catalogue's `observe_generation` only; the real catalogue is
    exercised in TestGenerationMetrics below."""

    def __init__(self) -> None:
        self.timings: list[GenerationTiming] = []
        disabled = Metrics(enabled=False)
        for name in ("llm_requests_total", "llm_duration", "llm_ttft", "llm_inflight",
                     "provider_healthy", "provider_circuit_opens_total", "llm_fallbacks_total",
                     "llm_admission_total", "llm_group_capacity", "llm_group_inflight",
                     "llm_queue_depth", "dependency_up"):
            setattr(self, name, getattr(disabled, name))

    def observe_generation(self, timing: GenerationTiming) -> None:
        self.timings.append(timing)


class TestLatencyDecomposition:
    async def test_stream_decomposes_queue_ttft_and_decode(self, clock) -> None:
        provider = ClockedProvider(clock, prefill_s=0.8, per_chunk_s=0.1, chunks=11,
                                   name="vllm-general", model="qwen-7b",
                                   capabilities=_caps(model_id="qwen-7b", max_concurrency=1))
        recorder = _Recorder()
        router = ProviderRouter([provider], clock=clock.monotonic, metrics=recorder)

        # Hold the only slot for 2 simulated seconds so the stream really queues.
        held = await router.admission.acquire("vllm-general")
        stream_task = asyncio.create_task(
            _drain(router.stream(_request(WorkloadHint(pool=ModelPool.GENERAL_FAST))))
        )
        await _settle()
        clock.advance(2.0)
        router.admission.release(held)
        await stream_task

        (timing,) = recorder.timings
        assert timing.outcome == "success" and timing.streamed
        assert timing.pool == "general-fast" and timing.priority == 1
        assert timing.queue_wait_ms == pytest.approx(2000.0)
        assert timing.ttft_ms == pytest.approx(800.0)
        assert timing.decode_ms == pytest.approx(1000.0)
        assert timing.service_ms == pytest.approx(1800.0)
        assert timing.total_ms == pytest.approx(3800.0)
        assert (timing.prompt_tokens, timing.completion_tokens) == (40, 11)
        assert timing.tokens_per_second == pytest.approx(11.0)

    async def test_complete_reports_queue_wait_on_the_response(self, clock) -> None:
        provider = ClockedProvider(clock, prefill_s=0.5, per_chunk_s=0.1, chunks=5,
                                   name="p", capabilities=_caps(max_concurrency=1))
        recorder = _Recorder()
        router = ProviderRouter([provider], clock=clock.monotonic, metrics=recorder)
        held = await router.admission.acquire("p")
        task = asyncio.create_task(router.complete(_request()))
        await _settle()
        clock.advance(3.0)
        router.admission.release(held)
        response = await task
        assert response.queue_wait_ms == pytest.approx(3000.0)
        (timing,) = recorder.timings
        assert not timing.streamed and timing.ttft_ms is None
        assert timing.service_ms == pytest.approx(1000.0)
        assert timing.tokens_per_second == pytest.approx(5.0)

    async def test_failed_attempt_is_recorded_with_its_outcome(self, clock) -> None:
        broken = EchoProvider(name="b", capabilities=_caps(),
                              script=[ScriptedTurn(raise_error=ProviderError("x", provider="b"))])
        recorder = _Recorder()
        with pytest.raises(AllProvidersFailedError):
            await ProviderRouter([broken], clock=clock.monotonic, metrics=recorder).complete(_request())
        assert [t.outcome for t in recorder.timings] == ["error"]


async def _drain(stream) -> str:
    return "".join([c.delta async for c in stream])


@pytest.mark.skipif(not PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestGenerationMetrics:
    async def test_series_carry_provider_model_pool_labels(self, clock) -> None:
        metrics = Metrics(enabled=True)
        provider = ClockedProvider(clock, prefill_s=0.4, per_chunk_s=0.2, chunks=6,
                                   name="coder", model="qwen-coder",
                                   capabilities=_caps(model_id="qwen-coder", max_concurrency=1,
                                                      pools=(ModelPool.CODER,)))
        router = ProviderRouter([provider], clock=clock.monotonic, metrics=metrics)
        await _drain(router.stream(_request(WorkloadHint(workload=WorkloadClass.ENGINEER,
                                                         pool=ModelPool.CODER))))
        sample = metrics.registry.get_sample_value
        labels = {"provider": "coder", "model": "qwen-coder", "pool": "coder"}
        assert sample("aura_llm_generation_ttft_seconds_sum", labels) == pytest.approx(0.4)
        assert sample("aura_llm_generation_decode_seconds_sum", labels) == pytest.approx(1.0)
        assert sample("aura_llm_generation_queue_wait_seconds_count", labels) == 1
        # An unbudgeted, unlabelled generation is purpose="engine" (KAN-107).
        assert sample("aura_llm_generation_total_seconds_count",
                      {**labels, "outcome": "success", "purpose": "engine"}) == 1
        assert sample("aura_llm_generation_tokens_total",
                      {**labels, "direction": "output", "purpose": "engine"}) == 6
        assert sample("aura_llm_generation_tokens_per_second_count", labels) == 1
        assert sample("aura_llm_admission_total",
                      {"group": "coder", "outcome": "admitted", "priority": "2"}) == 1
        assert sample("aura_llm_group_capacity", {"group": "coder"}) == 1
        assert sample("aura_llm_group_inflight", {"group": "coder"}) == 0

    async def test_no_content_reaches_any_label(self, clock) -> None:
        metrics = Metrics(enabled=True)
        router = ProviderRouter([EchoProvider(name="e", capabilities=_caps())],
                                clock=clock.monotonic, metrics=metrics)
        secret = "my-very-private-prompt-text"
        await router.complete(_request(conversation=Conversation.of([Message.user(secret)])))
        assert secret.encode() not in metrics.render()


# ---------------------------------------------------------------------------------------------
# HTTP: overload maps to 503 / 429 with Retry-After
# ---------------------------------------------------------------------------------------------


httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")


def _app(container):
    from aura_core.api import Principal, build_app
    from aura_core.domain.identifiers import UserId

    async def authenticator(request):
        return Principal(user_id=UserId("http-user"), email="u@example.com", role="User")

    return build_app(container, authenticator=authenticator, rate_limiter=None)


@pytest.fixture
def saturating_container(monkeypatch):
    from aura_core.container import Container

    monkeypatch.setenv("AURA_LLM_PROVIDERS", "echo")
    monkeypatch.setenv("AURA_PROVIDER_ECHO_KIND", "echo")
    monkeypatch.setenv("AURA_PROVIDER_ECHO_IS_LOCAL", "true")
    monkeypatch.setenv("AURA_PROVIDER_ECHO_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("AURA_LLM_ADMISSION_MAX_QUEUE", "0")
    monkeypatch.setenv("AURA_LLM_USER_MAX_CONCURRENT_GENERATIONS", "1")
    monkeypatch.setenv("AURA_LLM_CACHE_ENABLED", "false")
    return Container(load_settings(refresh=True, validate=False))


class TestOverloadHttpMapping:
    async def test_saturated_pool_is_503_with_retry_after(self, saturating_container) -> None:
        held = await saturating_container.router.admission.acquire("echo", user_key="someone-else")
        transport = httpx.ASGITransport(app=_app(saturating_container))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            chat = await client.post("/api/v3/chat", json={"message": "hi", "use_tools": False})
            complete = await client.post("/api/v3/complete", json={"prefix": "x = ", "suffix": ""})
        saturating_container.router.admission.release(held)

        assert chat.status_code == 503
        assert chat.json()["error"] == "overloaded"
        assert int(chat.headers["Retry-After"]) >= 1
        assert complete.status_code == 503
        assert complete.json()["details"]["reason"] == "queue_full"
        assert int(complete.headers["Retry-After"]) >= 1

    async def test_user_over_quota_is_429_with_retry_after(self, saturating_container, monkeypatch) -> None:
        admission = saturating_container.router.admission
        admission._group("echo").limits = PoolLimits(max_concurrent=4, max_queue=4)
        running = await admission.acquire("echo", user_key="http-user")
        queued = asyncio.create_task(admission.acquire("echo", user_key="http-user"))
        await _settle()
        transport = httpx.ASGITransport(app=_app(saturating_container))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            chat = await client.post("/api/v3/chat", json={"message": "hi", "use_tools": False})
        admission.release(running)
        admission.release(await queued)

        assert chat.status_code == 429
        assert chat.json()["error"] == "user_concurrency_limit"
        assert int(chat.headers["Retry-After"]) >= 1

    async def test_domain_error_handler_sets_retry_after(self, saturating_container) -> None:
        from fastapi import APIRouter

        app = _app(saturating_container)
        extra = APIRouter()

        @extra.get("/boom")
        async def boom():
            raise OverloadedError("full", retry_after=7, pool="echo", reason="queue_full")

        app.include_router(extra)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/boom")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "7"
