"""PP-06 / PP-07 (provider performance audit, 2026-11-08): bounded worst-case turn latency.

PP-06: a chat turn makes several generations (planner, answer), each trying up to three
providers with a full timeout apiece, so a failing setup could hold a person for minutes before
an error. An optional per-turn generation budget bounds the sum.

PP-07: once a stream had produced its first chunk, nothing in the router bounded the rest -- the
only limit was whatever read timeout the provider's own HTTP client happened to have. Each chunk
is now bounded by the request timeout, and ``stream_max_s`` optionally caps the whole stream.

These use the real monotonic clock with short timeouts: the behaviour under test is an asyncio
deadline actually firing, which a frozen clock cannot exercise.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import AsyncIterator

import pytest

from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import AllProvidersFailedError, ProviderError
from aura_core.domain.identifiers import UserId
from aura_core.domain.llm import (
    CompletionResponse,
    RoutingPolicy,
    SamplingParams,
    StreamChunk,
    WorkloadClass,
)
from aura_core.kernel import TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.deadline import current_generation_budget, generation_budget
from aura_core.llm.providers.echo import EchoProvider


class _Slow(EchoProvider):
    """Echo, after ``delay_s`` before the first output and ``gap_s`` between chunks."""

    def __init__(self, name: str, *, quality: int, delay_s: float = 0.0, gap_s: float = 0.0,
                 chunks: int = 3) -> None:
        super().__init__(
            name=name,
            capabilities=ProviderCapabilities(
                model_id=f"{name}-1", context_window=8192, max_output_tokens=512,
                supports_streaming=True, is_local=True, quality_tier=quality,
            ),
        )
        self.delay_s, self.gap_s, self.chunks = delay_s, gap_s, chunks

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        await asyncio.sleep(self.delay_s)
        return CompletionResponse(text="ok", provider=self.name, model=f"{self.name}-1")

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        self.calls.append(request)
        await asyncio.sleep(self.delay_s)
        for index in range(self.chunks):
            if index:
                await asyncio.sleep(self.gap_s)
            yield StreamChunk(delta=f"t{index} ", provider=self.name, model=f"{self.name}-1")


def _request(timeout_s: float = 5.0) -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("hello there")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
        timeout_s=timeout_s,
    )


def _router(*providers, **kwargs) -> ProviderRouter:
    # QUALITY_FIRST: the higher-tier provider is tried first, deterministically.
    return ProviderRouter(list(providers), default_policy=RoutingPolicy.QUALITY_FIRST, **kwargs)


# -- PP-06: the turn's generation budget ------------------------------------------------------


class TestGenerationBudget:
    async def test_the_budget_cuts_an_attempt_short_and_stops_failover(self) -> None:
        slow, fast = _Slow("slow", quality=3, delay_s=2.0), _Slow("fast", quality=1)
        router = _router(slow, fast)
        started = time.monotonic()
        with generation_budget(0.2), pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(_request(timeout_s=5.0))
        assert time.monotonic() - started < 1.0
        assert excinfo.value.attempts == [
            {"provider": "slow", "error": "turn_deadline", "timeout_s": 0.2}
        ]
        assert fast.call_count == 0

    async def test_a_budget_timeout_is_no_verdict_on_the_provider(self) -> None:
        """It was given less than its own deadline; the breaker must not learn from that."""
        slow = _Slow("slow", quality=3, delay_s=2.0)
        router = _router(slow, circuit_failure_threshold=1)
        with generation_budget(0.1), pytest.raises(AllProvidersFailedError):
            await router.complete(_request())
        stats = {s["provider"]: s for s in router.stats()}
        assert stats["slow"]["healthy"] is True
        assert stats["slow"]["failures"] == 0

    async def test_no_attempt_starts_once_the_budget_is_spent(self) -> None:
        fast = _Slow("fast", quality=1)
        router = _router(fast)
        with generation_budget(0.05) as budget:
            assert budget is not None
            budget.remaining(time.monotonic())  # start the clock
            await asyncio.sleep(0.1)
            with pytest.raises(AllProvidersFailedError) as excinfo:
                await router.complete(_request())
        assert excinfo.value.attempts == [{"provider": "fast", "error": "turn_deadline"}]
        assert fast.call_count == 0

    async def test_the_stream_obeys_it_before_the_first_chunk(self) -> None:
        slow, fast = _Slow("slow", quality=3, delay_s=2.0), _Slow("fast", quality=1)
        router = _router(slow, fast)
        with generation_budget(0.2), pytest.raises(AllProvidersFailedError) as excinfo:
            async for _ in router.stream(_request()):
                pass
        assert excinfo.value.attempts[0]["error"] == "turn_deadline"
        assert fast.call_count == 0

    async def test_without_a_budget_failover_is_unchanged(self) -> None:
        slow, fast = _Slow("slow", quality=3, delay_s=2.0), _Slow("fast", quality=1)
        router = _router(slow, fast)
        response = await router.complete(_request(timeout_s=0.2))
        assert response.provider == "fast"

    def test_a_nested_scope_cannot_extend_the_open_budget(self) -> None:
        with generation_budget(10.0) as outer:
            with generation_budget(600.0) as inner:
                assert inner is outer
                assert current_generation_budget() is outer
        assert current_generation_budget() is None

    def test_zero_means_no_budget(self) -> None:
        with generation_budget(0.0) as budget:
            assert budget is None
            assert current_generation_budget() is None


class TestKernelOpensTheBudget:
    """Only for turns a person waits on in real time; never for the Engineer's long loop."""

    @pytest.fixture
    def kernel(self, container):
        settings = container.kernel._deps.settings
        container.kernel._deps.settings = dataclasses.replace(
            settings, llm=dataclasses.replace(settings.llm, turn_generation_budget_s=30.0)
        )
        return container.kernel

    def test_a_chat_turn_gets_the_budget(self, kernel) -> None:
        turn = TurnRequest(message="hi", user_id=UserId("u"))
        assert kernel._turn_generation_budget_s(turn) == 30.0

    def test_a_voice_turn_gets_the_budget(self, kernel) -> None:
        turn = TurnRequest(message="hi", user_id=UserId("u"), workload=WorkloadClass.VOICE)
        assert kernel._turn_generation_budget_s(turn) == 30.0

    def test_an_engineer_turn_does_not(self, kernel) -> None:
        turn = TurnRequest(message="hi", user_id=UserId("u"), workload=WorkloadClass.ENGINEER)
        assert kernel._turn_generation_budget_s(turn) is None

    def test_background_work_does_not(self, kernel) -> None:
        turn = TurnRequest(
            message="hi", user_id=UserId("u"), workload=WorkloadClass.BACKGROUND_INSIGHT
        )
        assert kernel._turn_generation_budget_s(turn) is None

    def test_the_default_setting_opens_none(self, container) -> None:
        turn = TurnRequest(message="hi", user_id=UserId("u"))
        assert container.kernel._turn_generation_budget_s(turn) is None


# -- PP-07: bounded streams after the first chunk ---------------------------------------------


class TestStreamDeadlines:
    async def test_a_stall_after_the_first_chunk_ends_the_stream(self) -> None:
        stalled = _Slow("stalled", quality=3, gap_s=2.0)
        router = _router(stalled)
        received: list[str] = []
        started = time.monotonic()
        with pytest.raises(ProviderError) as excinfo:
            async for chunk in router.stream(_request(timeout_s=0.2)):
                received.append(chunk.delta)
        assert time.monotonic() - started < 1.0
        assert received == ["t0 "]
        assert excinfo.value.details.get("partial_output") is True

    async def test_stream_max_caps_a_stream_that_never_stalls(self) -> None:
        trickle = _Slow("trickle", quality=3, gap_s=0.05, chunks=200)
        router = _router(trickle, stream_max_s=0.3)
        received = 0
        started = time.monotonic()
        with pytest.raises(ProviderError) as excinfo:
            async for _ in router.stream(_request(timeout_s=5.0)):
                received += 1
        assert time.monotonic() - started < 1.5
        assert 1 < received < 200
        assert excinfo.value.details.get("partial_output") is True

    async def test_time_the_consumer_spends_is_not_charged_to_the_provider(self) -> None:
        """The deadline scopes the wait for the provider only, never the `yield`."""
        quick = _Slow("quick", quality=3, chunks=3)
        router = _router(quick, stream_max_s=0.0)
        received = []
        async for chunk in router.stream(_request(timeout_s=0.1)):
            received.append(chunk.delta)
            await asyncio.sleep(0.25)  # longer than the per-chunk deadline
        assert [d for d in received if d] == ["t0 ", "t1 ", "t2 "]

    async def test_a_healthy_stream_is_unaffected(self) -> None:
        router = _router(_Slow("ok", quality=3, gap_s=0.01, chunks=5), stream_max_s=5.0)
        deltas = [c.delta async for c in router.stream(_request(timeout_s=1.0))]
        assert [d for d in deltas if d] == ["t0 ", "t1 ", "t2 ", "t3 ", "t4 "]
