"""The liveness probe's timeout must be configurable, because "up" is not one thing.

Measured in production, on the deployed Control Plane, over five and a half minutes:

    10:44:17Z  healthy=False  5150.9 ms  ReadTimeout
    ...        (20 consecutive failures, every one at a flat ~5150 ms)
    10:49:48Z  healthy=False  5156.8 ms  ReadTimeout
    10:50:03Z  healthy=True    220.3 ms  ok
    ...        (11 consecutive successes, 209-225 ms)
    10:51:49Z  healthy=True    212.6 ms  ok

Nothing changed at that boundary except that the endpoint -- a Hugging Face Space that scales to
zero -- had finished waking up. The flatness of the failures is the tell: ~5150 ms every time is
not a server answering slowly, it is a client giving up at a fixed deadline.

Which makes the 5-second literal that used to sit in both `health()` implementations a real
defect rather than a tuning preference. For an endpoint that is always up it is correct. For one
that sleeps, the probe **cannot observe the recovery at all**: it times out identically whether
the provider is thirty seconds from ready or permanently dead, and `/health/ready` reports the
same `healthy: false` for both. An operator reading that cannot tell "asleep, warming" from
"broken", and an orchestrator acting on it will pull a service that was about to be fine.

The default stays 5.0, so no existing deployment changes behaviour. What changes is that a
deployment whose provider sleeps can now say so.

Note what this does *not* fix: whether a real user's first turn after an idle period succeeds.
That goes through `timeout_s` (180s deployed), not this probe, and is untested -- see EV-28.
Raising a probe timeout makes the report honest; it does not make a cold start fast.
"""

from __future__ import annotations

import pytest

from aura_core.config.settings import ProviderConfig
from aura_core.llm.providers.openai_compatible import OpenAICompatibleProvider
from aura_core.llm.providers.ollama import OllamaProvider
from aura_core.llm.registry import build_provider


class _SpyClient:
    """Records the timeout each probe asks for, then fails the way a sleeping endpoint does."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    async def get(self, path: str, *, timeout: float):  # noqa: ANN201
        self.timeouts.append(timeout)
        raise TimeoutError("ReadTimeout")


class TestTheProbeUsesItsConfiguredTimeout:
    @pytest.mark.parametrize(
        "factory,path",
        [
            (
                lambda t: OpenAICompatibleProvider(
                    name="hfspace", base_url="http://x", model="m", health_timeout_s=t
                ),
                "/models",
            ),
            (
                lambda t: OllamaProvider(
                    name="ollama", base_url="http://x", model="m", health_timeout_s=t
                ),
                "/api/tags",
            ),
        ],
        ids=["openai_compatible", "ollama"],
    )
    async def test_a_raised_timeout_reaches_the_request(self, factory, path) -> None:
        """Both providers carried the same 5.0 literal, so both had to change."""
        provider = factory(45.0)
        spy = _SpyClient()
        provider._client = spy  # the lazily-built client; see PostgresPool._get_pool's pattern

        status = await provider.health()

        assert spy.timeouts == [45.0], (
            f"the probe to {path} ignored health_timeout_s and used {spy.timeouts}"
        )
        assert status.healthy is False
        assert status.detail == "TimeoutError"

    async def test_the_default_is_unchanged(self) -> None:
        """Five seconds stays the default: this makes a knob available, it does not re-tune
        every deployment that never had this problem."""
        provider = OpenAICompatibleProvider(name="p", base_url="http://x", model="m")
        spy = _SpyClient()
        provider._client = spy

        await provider.health()

        assert spy.timeouts == [5.0]


class TestTheSettingIsReachableFromConfiguration:
    """A knob nothing can turn is not a fix. This is the path from an env var to the request."""

    def test_the_provider_config_carries_it(self) -> None:
        config = ProviderConfig(
            name="p", kind="openai_compatible", model="m", base_url="http://x"
        )
        assert config.health_timeout_s == 5.0

    @pytest.mark.parametrize("kind", ["openai_compatible", "ollama"])
    def test_build_provider_threads_it_through(self, kind: str) -> None:
        config = ProviderConfig(
            name="p", kind=kind, base_url="http://x", model="m", health_timeout_s=30.0
        )

        provider = build_provider(config)

        assert provider._health_timeout_s == 30.0, (
            "build_provider dropped health_timeout_s, so the env var would be silently inert -- "
            "which is worse than no setting at all, because it reads as configured"
        )

    def test_it_is_independent_of_the_request_timeout(self) -> None:
        """The two answer different questions: `timeout_s` bounds a generation someone is
        waiting on, this bounds a check that gets polled. Tying them would mean a 180s
        generation timeout implies a 180s probe, and `/health/ready` would hang."""
        config = ProviderConfig(
            name="p", kind="openai_compatible", base_url="http://x", model="m",
            timeout_s=180.0, health_timeout_s=30.0,
        )

        provider = build_provider(config)

        assert provider._timeout_s == 180.0
        assert provider._health_timeout_s == 30.0
