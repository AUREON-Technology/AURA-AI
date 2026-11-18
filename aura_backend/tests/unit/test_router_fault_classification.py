"""Which failures the router learns from, and which it records (F15, F29).

F15: the circuit breaker exists to stop sending traffic to a provider that is *down*. A 400, 404,
413 or 422 says the request was wrong, and a 401/403 says this provider's credentials are; neither
says the provider is unhealthy, so neither may open its circuit for every other caller.

F29: a third-party generation is audited whether or not it succeeded. A request that reached a
hosted provider and then failed still left the deployment, which is what the audit records.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.adapters.runtime import FrozenClock, InMemoryAuditSink
from aura_core.domain import (
    CompletionRequest,
    Conversation,
    Message,
    ProviderCapabilities,
    RoutingPolicy,
    SamplingParams,
)
from aura_core.domain.errors import AllProvidersFailedError, ProviderError
from aura_core.domain.llm import DataClassification
from aura_core.llm import ProviderRouter
from aura_core.llm.circuit import CircuitState
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.llm.sensitivity import data_scope


def _request() -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("hello there")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
    )


def _http_error(status: int, provider: str = "local") -> ProviderError:
    # The shape BaseProvider._raise_for_status produces.
    return ProviderError(
        f"{provider}: complete returned HTTP {status}",
        provider=provider,
        retryable=status >= 500 or status == 429,
        details={"status": status, "operation": "complete"},
    )


def _failing(status: int, times: int, *, name: str = "local", **caps) -> EchoProvider:
    script = [ScriptedTurn(raise_error=_http_error(status, name))] * times
    capabilities = ProviderCapabilities(
        model_id=f"{name}-1", context_window=8192, max_output_tokens=512, **caps
    )
    return EchoProvider(name=name, model=f"{name}-1", script=script, capabilities=capabilities)


async def _fail(router: ProviderRouter, times: int, *, stream: bool = False) -> None:
    for _ in range(times):
        with pytest.raises(AllProvidersFailedError):
            if stream:
                _ = [c async for c in router.stream(_request())]
            else:
                await router.complete(_request())


class TestOnlyHealthFailuresOpenTheCircuit:
    @pytest.mark.parametrize("status", [400, 404, 413, 422])
    async def test_request_faults_do_not_open_the_circuit(self, status: int) -> None:
        provider = _failing(status, 5)
        router = ProviderRouter([provider], circuit_failure_threshold=5)
        await _fail(router, 5)

        assert router._stats["local"].breaker.state is CircuitState.CLOSED
        assert "local" not in router.plan(_request()).excluded
        response = await router.complete(_request())  # the script is spent: a normal answer
        assert response.provider == "local"

    async def test_request_faults_do_not_open_the_circuit_when_streaming(self) -> None:
        router = ProviderRouter([_failing(400, 5)], circuit_failure_threshold=5)
        await _fail(router, 5, stream=True)
        assert router._stats["local"].breaker.state is CircuitState.CLOSED

    async def test_server_errors_still_open_the_circuit(self) -> None:
        router = ProviderRouter([_failing(503, 5)], circuit_failure_threshold=5)
        await _fail(router, 5)

        assert router._stats["local"].breaker.state is CircuitState.OPEN
        assert router.plan(_request()).excluded.get("local") == "circuit_open"

    async def test_server_errors_still_open_the_circuit_when_streaming(self) -> None:
        router = ProviderRouter([_failing(503, 5)], circuit_failure_threshold=5)
        await _fail(router, 5, stream=True)
        assert router._stats["local"].breaker.state is CircuitState.OPEN

    async def test_request_faults_are_still_counted_as_failed_requests(self) -> None:
        router = ProviderRouter([_failing(422, 2)], circuit_failure_threshold=5)
        await _fail(router, 2)
        assert router._stats["local"].total_failures == 2

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_credential_fault_fails_over_without_opening_the_circuit(
        self, status: int
    ) -> None:
        """Bad credentials on one provider say nothing about the request, so the next candidate
        is still tried; and nothing about the provider's health, so its circuit stays closed."""
        misconfigured = _failing(status, 6, name="primary", quality_tier=3)
        backup = EchoProvider(
            name="backup", model="b",
            capabilities=ProviderCapabilities(
                model_id="b", context_window=8192, max_output_tokens=512, quality_tier=1
            ),
        )
        router = ProviderRouter(
            [misconfigured, backup], default_policy=RoutingPolicy.QUALITY_FIRST,
            circuit_failure_threshold=5,
        )
        for _ in range(6):
            response = await router.complete(_request())
            assert response.provider == "backup"
        assert router._stats["primary"].breaker.state is CircuitState.CLOSED

    async def test_a_half_open_probe_answered_with_a_request_fault_gives_its_slot_back(
        self,
    ) -> None:
        clock = FrozenClock()
        down = ScriptedTurn(raise_error=_http_error(503))
        bad = ScriptedTurn(raise_error=_http_error(400))
        provider = EchoProvider(name="local", model="l", script=[down, down, bad])
        router = ProviderRouter(
            [provider], clock=clock.monotonic,
            circuit_failure_threshold=2, circuit_open_seconds=30,
        )
        await _fail(router, 2)
        assert router.plan(_request()).excluded.get("local") == "circuit_open"
        clock.advance(31)

        await _fail(router, 1)  # the probe: a 400 is no verdict on the provider's health

        assert router._stats["local"].breaker.state is CircuitState.HALF_OPEN
        response = await router.complete(_request())  # the next probe is admitted
        assert response.provider == "local"
        assert router._stats["local"].breaker.state is CircuitState.CLOSED


def _hosted(script=()) -> EchoProvider:
    return EchoProvider(
        name="hosted",
        script=list(script),
        capabilities=ProviderCapabilities(
            model_id="hosted-open-weight", context_window=8192, max_output_tokens=512,
            quality_tier=3, max_data_classification=DataClassification.INTERNAL,
            third_party=True,
        ),
    )


class TestFailedThirdPartyGenerationsAreAudited:
    async def test_a_failed_completion_is_audited_with_its_outcome(self) -> None:
        audit = InMemoryAuditSink()
        router = ProviderRouter(
            [_hosted([ScriptedTurn(raise_error=_http_error(503, "hosted"))])], audit=audit
        )
        with data_scope(DataClassification.INTERNAL):
            with pytest.raises(AllProvidersFailedError):
                await router.complete(_request())

        (event,) = await audit.query(action="llm.third_party_generation")
        assert event.resource == "hosted"
        assert event.outcome == "failure"
        assert event.detail["result"] == "error"
        assert event.detail["streamed"] is False
        assert "hello there" not in repr(event.detail)

    async def test_a_failed_stream_is_audited_with_its_outcome(self) -> None:
        audit = InMemoryAuditSink()
        router = ProviderRouter(
            [_hosted([ScriptedTurn(raise_error=_http_error(400, "hosted"))])], audit=audit
        )
        with data_scope(DataClassification.INTERNAL):
            with pytest.raises(AllProvidersFailedError):
                _ = [c async for c in router.stream(_request())]

        (event,) = await audit.query(action="llm.third_party_generation")
        assert event.outcome == "failure"
        assert event.detail["streamed"] is True

    async def test_a_cancelled_generation_is_audited(self) -> None:
        audit = InMemoryAuditSink()
        hosted = _hosted([ScriptedTurn(text="late", delay_ms=3_600_000)])
        router = ProviderRouter([hosted], audit=audit)

        async def run() -> None:
            with data_scope(DataClassification.INTERNAL):
                await router.complete(_request())

        task = asyncio.create_task(run())
        while hosted.call_count < 1:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        (event,) = await audit.query(action="llm.third_party_generation")
        assert event.detail["result"] == "cancelled"

    async def test_a_successful_generation_is_audited_once_as_a_success(self) -> None:
        audit = InMemoryAuditSink()
        router = ProviderRouter([_hosted()], audit=audit)
        with data_scope(DataClassification.INTERNAL):
            await router.complete(_request())
            _ = [c async for c in router.stream(_request())]

        events = await audit.query(action="llm.third_party_generation")
        assert len(events) == 2
        assert {e.outcome for e in events} == {"success"}
        assert {e.detail["result"] for e in events} == {"success"}
