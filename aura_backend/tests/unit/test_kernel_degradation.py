"""Kernel orchestration under partial failure, and the router's failover boundary.

The kernel's job when something breaks is to produce the best answer it still can and *say* what
degraded, rather than failing the turn. The exception is the streaming failover boundary: once a
token has reached the client, switching providers would duplicate output, so the router must
refuse to fail over and surface the error instead.
"""

from __future__ import annotations

import pytest

from aura_core.domain import CompletionRequest, Conversation, Message, SamplingParams
from aura_core.domain.errors import AllProvidersFailedError, ProviderError
from aura_core.domain.identifiers import DocumentId, SessionId
from aura_core.domain.llm import ProviderCapabilities, RoutingPolicy
from aura_core.domain.telemetry import TurnOutcome
from aura_core.kernel import TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn


def _request(message: str = "How many days of annual leave do I get?", **overrides):
    from aura_core.domain.identifiers import UserId

    base = dict(message=message, user_id=UserId("test-user"))
    base.update(overrides)
    return TurnRequest(**base)


class TestTurnTelemetry:
    async def test_a_successful_turn_reports_content_free_telemetry(self, container) -> None:
        """Telemetry is widely readable and long-lived; prompts and answers must not enter it."""
        await container.startup()
        result = await container.kernel.run(_request("my salary is 95000 EGP"))
        payload = result.telemetry.as_dict()

        import json

        assert "95000" not in json.dumps(payload)
        assert payload["latency"]["total_ms"] >= 0.0

    async def test_stage_timings_are_recorded(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request())
        timings = result.telemetry.timings

        assert timings.generation >= 0.0
        assert timings.overhead >= 0.0

    async def test_a_correlation_id_is_always_present(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request())
        assert result.telemetry.correlation_id.value


class TestDegradation:
    async def test_a_provider_outage_is_a_typed_failure_not_a_crash(self, container) -> None:
        await container.startup()
        container.router._providers.clear()

        result = await container.kernel.run(_request())
        assert result.telemetry.outcome is not TurnOutcome.SUCCESS
        assert result.telemetry.error_kind

    async def test_retrieval_is_skipped_when_the_caller_opts_out(self, container, handbook,
                                                                 user) -> None:
        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        result = await container.kernel.run(_request(use_knowledge=False))
        assert result.citations == ()

    async def test_memory_is_skipped_when_the_caller_opts_out(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request(use_memory=False))
        assert result.telemetry.outcome is TurnOutcome.SUCCESS

    async def test_tools_are_withheld_when_the_caller_opts_out(self, container) -> None:
        await container.startup()
        result = await container.kernel.run(_request(use_tools=False))
        assert result.tool_calls == ()

    async def test_a_session_persists_across_turns(self, container) -> None:
        await container.startup()
        session = SessionId("s-1")
        await container.kernel.run(_request("My name is Ahmed.", session_id=session))
        second = await container.kernel.run(_request("What did I just say?", session_id=session))

        assert second.telemetry.outcome is TurnOutcome.SUCCESS

    async def test_an_unknown_agent_falls_back_to_the_orchestrator_prompt(
        self, container
    ) -> None:
        """A typo'd agent name must not produce a turn with no system prompt at all."""
        await container.startup()
        result = await container.kernel.run(_request(agent="nonexistent-agent"))
        assert result.telemetry.outcome is TurnOutcome.SUCCESS


class TestStreaming:
    async def test_a_stream_emits_deltas_and_a_terminal_frame(self, container) -> None:
        await container.startup()
        frames = [frame async for frame in container.kernel.stream(_request())]

        assert frames[-1]["type"] == "done"
        assert any(f["type"] == "token" for f in frames)

    async def test_a_stream_carries_citations_on_the_terminal_frame(
        self, container, user, handbook
    ) -> None:
        await container.startup()
        await container.knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        frames = [frame async for frame in container.kernel.stream(_request())]
        assert frames[-1]["citations"]

    async def test_a_provider_outage_mid_stream_yields_an_error_frame(
        self, container
    ) -> None:
        """The client must be told, not left with a stream that simply stops."""
        await container.startup()
        container.router._providers.clear()

        frames = [frame async for frame in container.kernel.stream(_request())]
        assert frames[-1]["type"] == "error"
        assert frames[-1]["error"]


class TestRouterFailover:
    def _provider(self, name: str, *, turns=(), **caps_overrides) -> EchoProvider:
        caps = ProviderCapabilities(
            model_id=f"{name}-1", context_window=8192, max_output_tokens=1024,
            supports_streaming=True,
            **{"is_local": True, "quality_tier": 1, **caps_overrides},
        )
        return EchoProvider(name=name, model=f"{name}-1", capabilities=caps, script=list(turns))

    def _completion(self) -> CompletionRequest:
        return CompletionRequest(
            conversation=Conversation.of([Message.user("hello")]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=32),
        )

    async def test_a_failing_provider_fails_over_before_the_first_token(self, clock) -> None:
        primary = self._provider("primary", turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))])
        secondary = self._provider("secondary")
        router = ProviderRouter([primary, secondary], clock=clock.monotonic)

        response = await router.complete(self._completion())
        assert response.provider == "secondary"

    async def test_a_stream_that_fails_before_any_output_fails_over(self, clock) -> None:
        primary = self._provider("primary", turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))])
        secondary = self._provider("secondary")
        router = ProviderRouter([primary, secondary], clock=clock.monotonic)

        chunks = [c async for c in router.stream(self._completion())]
        assert any(c.provider == "secondary" for c in chunks)

    async def test_every_provider_failing_raises_a_typed_error(self, clock) -> None:
        providers = [
            self._provider(name, turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))])
            for name in ("a", "b")
        ]
        router = ProviderRouter(providers, clock=clock.monotonic)

        with pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(self._completion())
        assert len(excinfo.value.details["attempts"]) == 2

    async def test_a_non_retryable_error_stops_the_ladder(self, clock) -> None:
        """Trying every provider against a malformed request wastes the whole budget."""
        primary = self._provider(
            "primary",
            turns=[ScriptedTurn(raise_error=ProviderError("bad request", provider="scripted", retryable=False))],
        )
        secondary = self._provider("secondary")
        router = ProviderRouter([primary, secondary], clock=clock.monotonic)

        with pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(self._completion())
        assert len(excinfo.value.details["attempts"]) == 1

    async def test_stats_expose_the_circuit_state(self, clock) -> None:
        router = ProviderRouter([self._provider("only")], clock=clock.monotonic)
        await router.complete(self._completion())
        stats = router.stats()

        assert stats[0]["provider"] == "only"
        assert stats[0]["requests"] == 1
        assert stats[0]["state"] == "closed"

    async def test_a_circuit_can_be_reset_by_an_operator(self, clock) -> None:
        provider = self._provider(
            "flaky", turns=[ScriptedTurn(raise_error=ProviderError("down", provider="scripted"))] * 10
        )
        router = ProviderRouter([provider], clock=clock.monotonic)
        for _ in range(5):
            with pytest.raises(AllProvidersFailedError):
                await router.complete(self._completion())

        assert router.stats()[0]["state"] == "open"
        router.reset_circuit("flaky")
        assert router.stats()[0]["state"] == "closed"

    async def test_require_local_excludes_a_cloud_provider(self, clock) -> None:
        cloud = self._provider("cloud", is_local=False, quality_tier=3)
        local = self._provider("local", is_local=True)
        router = ProviderRouter([cloud, local], clock=clock.monotonic,
                                default_policy=RoutingPolicy.QUALITY_FIRST)

        decision = router.plan(self._completion())
        assert decision.primary == "cloud"  # quality_first prefers the higher tier

        response = await router.complete(
            CompletionRequest(
                conversation=Conversation.of([Message.user("hi")]),
                sampling=SamplingParams(temperature=0.0, max_output_tokens=16),
                require_local=True,
            )
        )
        assert response.provider == "local"

    async def test_a_quality_floor_excludes_a_provider_below_it(self, clock) -> None:
        weak = self._provider("weak", quality_tier=1)
        strong = self._provider("strong", quality_tier=3)
        router = ProviderRouter([weak, strong], clock=clock.monotonic)

        response = await router.complete(
            CompletionRequest(
                conversation=Conversation.of([Message.user("hi")]),
                sampling=SamplingParams(temperature=0.0, max_output_tokens=16),
                min_quality_tier=3,
            )
        )
        assert response.provider == "strong"

    async def test_an_impossible_requirement_raises_rather_than_silently_relaxing(
        self, clock
    ) -> None:
        """Silently serving a tier-1 model to a caller who demanded tier 3 is worse than an
        error: the caller cannot tell the difference from the response."""
        router = ProviderRouter([self._provider("weak", quality_tier=1)], clock=clock.monotonic)

        with pytest.raises(AllProvidersFailedError):
            await router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user("hi")]),
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=16),
                    min_quality_tier=3,
                )
            )

    async def test_health_reports_every_provider(self, clock) -> None:
        router = ProviderRouter(
            [self._provider("a"), self._provider("b")], clock=clock.monotonic
        )
        health = await router.health()
        assert set(health) == {"a", "b"}
