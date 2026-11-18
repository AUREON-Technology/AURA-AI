"""``AURA_PROVIDER_<NAME>_HEALTH_PROBE=passive``: readiness must not wake a sleeping server.

A Hugging Face Space sleeps when idle and wakes on *any* request, and it bills while awake. An
active readiness probe is a request, so a load balancer or uptime check that polls
``/health/ready`` every minute keeps a paid GPU awake around the clock (Phase I decision,
``docs/plans/runtime_feature_enablement_plan.md``). A passive provider is never called by
readiness: its health is what real generations last did to its circuit breaker.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.config.settings import ProviderConfig
from aura_core.domain import (
    CompletionRequest,
    Conversation,
    Message,
    ProviderCapabilities,
    SamplingParams,
)
from aura_core.domain.errors import AllProvidersFailedError, ConfigurationError, ProviderError
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.llm.registry import build_provider


def _caps(**overrides) -> ProviderCapabilities:
    return ProviderCapabilities(**{"model_id": "m", "context_window": 8192,
                                   "max_output_tokens": 512, **overrides})


def _request() -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("hello")]),
        sampling=SamplingParams(temperature=0.0),
    )


class TestTheSetting:
    def test_active_is_the_default(self) -> None:
        assert ProviderConfig(name="p", kind="echo", model="m").health_probe == "active"

    def test_an_unknown_mode_is_refused_at_boot(self) -> None:
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="p", kind="echo", model="m", health_probe="sometimes")

    def test_it_is_read_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_PROVIDER_SPACE_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_SPACE_MODEL", "m")
        monkeypatch.setenv("AURA_PROVIDER_SPACE_HEALTH_PROBE", " Passive ")
        assert ProviderConfig.from_env("space").health_probe == "passive"

    def test_the_built_provider_carries_it(self) -> None:
        passive = build_provider(ProviderConfig(name="p", kind="echo", model="m",
                                                health_probe="passive"))
        active = build_provider(ProviderConfig(name="a", kind="echo", model="m"))
        assert passive.capabilities.passive_health is True
        assert active.capabilities.passive_health is False


class TestReadinessNeverCallsAPassiveProvider:
    async def test_the_passive_provider_is_not_called_and_the_active_one_is(self) -> None:
        space = EchoProvider(name="space", capabilities=_caps(passive_health=True))
        hosted = EchoProvider(name="hosted", capabilities=_caps())
        router = ProviderRouter([space, hosted], clock=FrozenClock().monotonic)

        report = await router.health()

        assert space.health_calls == 0
        assert hosted.health_calls == 1
        assert report["space"]["probe"] == "passive"
        assert report["space"]["healthy"] is True
        assert report["space"]["detail"] == "passive: no traffic yet"
        assert "probe" not in report["hosted"]

    async def test_it_reports_what_real_traffic_did_to_the_breaker(self) -> None:
        down = ScriptedTurn(raise_error=ProviderError("asleep", provider="space"))
        space = EchoProvider(name="space", script=[down, down],
                             capabilities=_caps(passive_health=True))
        router = ProviderRouter([space], clock=FrozenClock().monotonic,
                                circuit_failure_threshold=2, circuit_open_seconds=30)
        for _ in range(2):
            with pytest.raises(AllProvidersFailedError):
                await router.complete(_request())

        report = await router.health()

        assert space.health_calls == 0
        assert report["space"]["healthy"] is False
        assert report["space"]["detail"] == "passive: circuit open, 2 consecutive failures"

    async def test_a_success_closes_it_again(self) -> None:
        down = ScriptedTurn(raise_error=ProviderError("waking", provider="space"))
        space = EchoProvider(name="space", script=[down, ScriptedTurn(text="awake")],
                             capabilities=_caps(passive_health=True))
        router = ProviderRouter([space], clock=FrozenClock().monotonic,
                                circuit_failure_threshold=5)
        with pytest.raises(AllProvidersFailedError):
            await router.complete(_request())
        await router.complete(_request())

        report = await router.health()
        assert report["space"]["healthy"] is True
        assert report["space"]["detail"] == "passive: circuit closed, 0 consecutive failures"


class TestReadinessGate:
    """A passive provider's "healthy" means "not known to be broken", so it must not hold the
    gateway ready while every provider that was actually checked is down."""

    async def test_an_unknown_passive_provider_does_not_carry_readiness(self, container) -> None:
        space = EchoProvider(name="space", capabilities=_caps(passive_health=True))
        hosted = EchoProvider(name="hosted", healthy=False, capabilities=_caps())
        container.router = ProviderRouter([space, hosted], clock=FrozenClock().monotonic)

        report = await container.health()

        assert report["healthy"] is False
        assert space.health_calls == 0

    async def test_a_healthy_checked_provider_is_enough(self, container) -> None:
        space = EchoProvider(name="space", capabilities=_caps(passive_health=True))
        hosted = EchoProvider(name="hosted", capabilities=_caps())
        container.router = ProviderRouter([space, hosted], clock=FrozenClock().monotonic)

        assert (await container.health())["healthy"] is True

    async def test_when_every_provider_is_passive_their_state_decides(self, container) -> None:
        space = EchoProvider(name="space", capabilities=_caps(passive_health=True))
        container.router = ProviderRouter([space], clock=FrozenClock().monotonic)

        assert (await container.health())["healthy"] is True
        assert space.health_calls == 0
