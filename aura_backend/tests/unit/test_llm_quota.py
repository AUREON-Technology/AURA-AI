"""Quota-aware routing (ADR-030 Phase 2).

Two behaviours, kept apart because they answer different questions:

* a provider whose *declared* budget this request would exceed is skipped before anything is
  sent, so a free tier is never spent discovering that it is spent;
* a ``429`` benches its provider until the reset the provider named, and is never a circuit-breaker
  failure -- an exhausted quota is not an outage.

Everything runs on the echo provider and a ``FrozenClock`` (wall and monotonic time advance
together), with nothing mocked and no network.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.config.settings import LLMSettings, ProviderConfig
from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import AllProvidersFailedError, ConfigurationError, ProviderError
from aura_core.domain.llm import RoutingPolicy, SamplingParams
from aura_core.llm import (
    InMemoryCache,
    ProviderRouter,
    QuotaLimits,
    QuotaTracker,
    quota_limits_from_settings,
    retry_after_seconds,
)
from aura_core.llm.circuit import CircuitState
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn

# 2026-01-01T00:00:00Z -- FrozenClock's default start, spelled out for the day-boundary tests.
_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _wall(clock: FrozenClock):
    return lambda: clock.now().timestamp()


def _caps(name: str, **overrides) -> ProviderCapabilities:
    base = dict(model_id=name, context_window=8192, max_output_tokens=100, quality_tier=2)
    base.update(overrides)
    return ProviderCapabilities(**base)


def _request(max_output_tokens: int | None = 100) -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("hello there")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=max_output_tokens),
    )


def _rate_limited(retry_after_s: float | None = None) -> ProviderError:
    details: dict = {"status": 429}
    if retry_after_s is not None:
        details["retry_after_s"] = retry_after_s
    return ProviderError("429", provider="hosted", details=details, retryable=True)


def _router(clock: FrozenClock, *providers, limits=None, **kwargs) -> ProviderRouter:
    quota = QuotaTracker(limits or {}, wall=_wall(clock), default_cooldown_s=30.0)
    return ProviderRouter(
        list(providers),
        default_policy=RoutingPolicy.QUALITY_FIRST,
        clock=clock.monotonic,
        quota=quota,
        **kwargs,
    )


# -- header parsing --------------------------------------------------------------------


class TestRetryAfterSeconds:
    NOW = 1_800_000_000.0

    def test_retry_after_seconds_wins(self) -> None:
        headers = {"Retry-After": "7", "x-ratelimit-reset-tokens": "1h"}
        assert retry_after_seconds(headers, now=self.NOW) == 7.0

    def test_retry_after_http_date(self) -> None:
        at = datetime.fromtimestamp(self.NOW + 90, tz=timezone.utc)
        headers = {"retry-after": format_datetime(at, usegmt=True)}
        assert retry_after_seconds(headers, now=self.NOW) == pytest.approx(90.0, abs=1.0)

    def test_groq_durations_take_the_later_reset(self) -> None:
        """A 429 does not say which limit tripped; waiting for the later reset is the safe read."""
        headers = {
            "x-ratelimit-reset-requests": "2m59.56s",
            "x-ratelimit-reset-tokens": "7.66s",
        }
        assert retry_after_seconds(headers, now=self.NOW) == pytest.approx(179.56)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("1h2m3s", 3723.0), ("250ms", 0.25), ("45", 45.0), ("1m", 60.0)],
    )
    def test_duration_shapes(self, value: str, expected: float) -> None:
        assert retry_after_seconds({"x-ratelimit-reset": value}, now=self.NOW) == pytest.approx(expected)

    def test_openrouter_epoch_milliseconds(self) -> None:
        reset_ms = str(int((self.NOW + 3600) * 1000))
        assert retry_after_seconds({"X-RateLimit-Reset": reset_ms}, now=self.NOW) == pytest.approx(3600.0)

    def test_epoch_seconds(self) -> None:
        reset = str(int(self.NOW + 120))
        assert retry_after_seconds({"x-ratelimit-reset": reset}, now=self.NOW) == pytest.approx(120.0)

    @pytest.mark.parametrize("headers", [{}, None, {"retry-after": "soon"}, {"x-ratelimit-reset": "x"}])
    def test_nothing_usable_is_none(self, headers) -> None:
        assert retry_after_seconds(headers, now=self.NOW) is None

    def test_a_past_reset_is_zero_not_negative(self) -> None:
        assert retry_after_seconds({"x-ratelimit-reset": str(int(self.NOW - 50))}, now=self.NOW) == 0.0


class TestProviderErrorCarriesTheReset:
    """The provider is where the headers are; the router only sees the error."""

    class _Response:
        def __init__(self, status: int, headers: dict) -> None:
            self.status_code = status
            self.headers = headers
            self.text = "slow down"

    async def test_a_429_records_retry_after(self) -> None:
        provider = EchoProvider(name="hosted")
        with pytest.raises(ProviderError) as excinfo:
            await provider._raise_for_status(
                self._Response(429, {"retry-after": "12"}), operation="complete"
            )
        assert excinfo.value.details["status"] == 429
        assert excinfo.value.details["retry_after_s"] == 12.0
        assert excinfo.value.retryable

    async def test_other_statuses_carry_no_reset(self) -> None:
        provider = EchoProvider(name="hosted")
        with pytest.raises(ProviderError) as excinfo:
            await provider._raise_for_status(
                self._Response(503, {"retry-after": "12"}), operation="complete"
            )
        assert "retry_after_s" not in excinfo.value.details


# -- the tracker on its own ------------------------------------------------------------


class TestQuotaTracker:
    def test_undeclared_limits_never_refuse(self, clock: FrozenClock) -> None:
        tracker = QuotaTracker(wall=_wall(clock))
        for _ in range(1000):
            reservation, reason = tracker.try_reserve("local", 10_000)
            assert reason is None and reservation is not None

    def test_rpm_refuses_then_recovers_after_the_window(self, clock: FrozenClock) -> None:
        tracker = QuotaTracker({"h": QuotaLimits(rpm=2)}, wall=_wall(clock))
        assert tracker.try_reserve("h", 10)[1] is None
        assert tracker.try_reserve("h", 10)[1] is None
        assert tracker.try_reserve("h", 10) == (None, "quota_rpm")
        clock.advance(60.5)
        assert tracker.try_reserve("h", 10)[1] is None

    def test_tpm_counts_the_worst_case_and_refuses_a_request_that_could_never_fit(
        self, clock: FrozenClock
    ) -> None:
        tracker = QuotaTracker({"h": QuotaLimits(tpm=1000)}, wall=_wall(clock))
        assert tracker.refusal("h", 1001) == "quota_request_too_large"
        assert tracker.try_reserve("h", 600)[1] is None
        assert tracker.try_reserve("h", 600) == (None, "quota_tpm")
        assert tracker.try_reserve("h", 400)[1] is None

    def test_settling_to_actual_usage_frees_the_unused_estimate(self, clock: FrozenClock) -> None:
        tracker = QuotaTracker({"h": QuotaLimits(tpm=1000, tpd=1000)}, wall=_wall(clock))
        reservation, _ = tracker.try_reserve("h", 900)
        assert tracker.refusal("h", 500) == "quota_tpd"
        tracker.settle(reservation, 200)
        assert tracker.refusal("h", 500) is None
        assert tracker.snapshot("h")["day_tokens"] == 200

    def test_settling_none_keeps_the_estimate(self, clock: FrozenClock) -> None:
        tracker = QuotaTracker({"h": QuotaLimits(tpd=1000)}, wall=_wall(clock))
        reservation, _ = tracker.try_reserve("h", 900)
        tracker.settle(reservation, None)
        assert tracker.snapshot("h")["day_tokens"] == 900

    def test_a_reservation_settles_once(self, clock: FrozenClock) -> None:
        tracker = QuotaTracker({"h": QuotaLimits(tpd=1000)}, wall=_wall(clock))
        reservation, _ = tracker.try_reserve("h", 500)
        tracker.settle(reservation, 100)
        tracker.settle(reservation, 0)
        assert tracker.snapshot("h")["day_tokens"] == 100

    def test_daily_limits_reset_at_utc_midnight(self) -> None:
        clock = FrozenClock(_START.replace(hour=23, minute=59))
        tracker = QuotaTracker({"h": QuotaLimits(rpd=1)}, wall=_wall(clock))
        assert tracker.try_reserve("h", 1)[1] is None
        assert tracker.try_reserve("h", 1) == (None, "quota_rpd")
        clock.advance(61)
        assert tracker.try_reserve("h", 1)[1] is None

    def test_cooldown_refuses_until_it_elapses(self, clock: FrozenClock) -> None:
        tracker = QuotaTracker(wall=_wall(clock))
        assert tracker.cooldown("h", 120) == 120
        assert tracker.refusal("h", 1) == "rate_limited"
        clock.advance(119)
        assert tracker.refusal("h", 1) == "rate_limited"
        clock.advance(2)
        assert tracker.refusal("h", 1) is None

    def test_a_shorter_cooldown_never_cuts_a_longer_one(self, clock: FrozenClock) -> None:
        tracker = QuotaTracker(wall=_wall(clock))
        tracker.cooldown("h", 3600)
        tracker.cooldown("h", 5)
        assert tracker.cooldown_remaining("h") == pytest.approx(3600)

    def test_no_reset_named_uses_the_default_and_absurd_resets_are_capped(
        self, clock: FrozenClock
    ) -> None:
        tracker = QuotaTracker(wall=_wall(clock), default_cooldown_s=45)
        assert tracker.cooldown("a", None) == 45
        assert tracker.cooldown("b", 10 * 86400) == 26 * 3600
        assert tracker.cooldown("c", 0) == 1.0

    def test_negative_limits_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            QuotaLimits(rpm=-1)


class TestQuotaPersistence:
    """A restart must not forget that today's budget is already spent."""

    async def test_daily_counters_and_cooldowns_survive_a_restart(self, clock: FrozenClock) -> None:
        store = InMemoryCache()
        limits = {"h": QuotaLimits(rpd=3, tpd=10_000)}
        before = QuotaTracker(limits, wall=_wall(clock), store=store)
        await before.ensure_hydrated()
        for _ in range(3):
            reservation, _ = before.try_reserve("h", 100)
            before.settle(reservation, 50)
        before.cooldown("g", 600)
        await before.flush()

        after = QuotaTracker(limits, wall=_wall(clock), store=store)
        after.track(["h", "g"])  # the router registers its whole pool
        await after.ensure_hydrated()
        assert after.try_reserve("h", 1) == (None, "quota_rpd")
        assert after.snapshot("h")["day_tokens"] == 150
        assert after.refusal("g", 1) == "rate_limited"

    async def test_yesterdays_counters_do_not_carry_over(self) -> None:
        clock = FrozenClock(_START.replace(hour=23, minute=59))
        store = InMemoryCache()
        limits = {"h": QuotaLimits(rpd=1)}
        before = QuotaTracker(limits, wall=_wall(clock), store=store)
        await before.ensure_hydrated()
        before.try_reserve("h", 1)
        await before.flush()

        clock.advance(120)
        after = QuotaTracker(limits, wall=_wall(clock), store=store)
        await after.ensure_hydrated()
        assert after.try_reserve("h", 1)[1] is None

    async def test_the_router_registers_every_provider_for_hydration(self, clock: FrozenClock) -> None:
        """A provider with no declared limits still has a cooldown worth remembering."""
        store = InMemoryCache()
        before = QuotaTracker(wall=_wall(clock), store=store)
        before.cooldown("local", 300)
        await before.flush()

        after = QuotaTracker(wall=_wall(clock), store=store)
        ProviderRouter([EchoProvider(name="local")], clock=clock.monotonic, quota=after)
        await after.ensure_hydrated()
        assert after.refusal("local", 1) == "rate_limited"

    async def test_a_failing_store_starts_empty_instead_of_failing(self, clock: FrozenClock) -> None:
        class Broken(InMemoryCache):
            async def get_many(self, keys):
                raise ConnectionError("redis down")

        tracker = QuotaTracker({"h": QuotaLimits(rpd=1)}, wall=_wall(clock), store=Broken())
        await tracker.ensure_hydrated()
        assert tracker.try_reserve("h", 1)[1] is None


# -- the router ------------------------------------------------------------------------


class TestRouterRespectsDeclaredBudgets:
    async def test_an_exhausted_provider_is_skipped_before_anything_is_sent(
        self, clock: FrozenClock
    ) -> None:
        hosted = EchoProvider(name="hosted", capabilities=_caps("hosted", quality_tier=3))
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1, is_local=True))
        router = _router(clock, hosted, local, limits={"hosted": QuotaLimits(rpd=1)})

        first = await router.complete(_request())
        assert first.provider == "hosted"

        assert router.plan(_request()).excluded == {"hosted": "quota_rpd"}
        second = await router.complete(_request())
        assert second.provider == "local"
        assert hosted.call_count == 1  # the second turn never reached it

    async def test_usage_settles_the_day_counter_to_what_was_reported(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(name="hosted", capabilities=_caps("hosted"))
        router = _router(clock, hosted, limits={"hosted": QuotaLimits(tpd=10_000)})
        response = await router.complete(_request(max_output_tokens=100))
        snapshot = router.quota.snapshot("hosted")
        assert snapshot["day_tokens"] == response.usage.total_tokens
        assert snapshot["day_tokens"] < _request().conversation.approx_tokens() + 100

    async def test_a_request_too_large_for_tpm_goes_elsewhere(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(name="hosted", capabilities=_caps("hosted", quality_tier=3))
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1))
        router = _router(clock, hosted, local, limits={"hosted": QuotaLimits(tpm=50)})
        response = await router.complete(_request(max_output_tokens=100))
        assert response.provider == "local"
        assert router.plan(_request()).excluded["hosted"] == "quota_request_too_large"

    async def test_with_every_provider_out_of_budget_the_error_says_so(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(name="hosted", capabilities=_caps("hosted"))
        router = _router(clock, hosted, limits={"hosted": QuotaLimits(rpm=1)})
        await router.complete(_request())
        with pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(_request())
        assert {"provider": "hosted", "reason": "quota_rpm"} in excinfo.value.attempts

    def test_stats_expose_the_quota(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(name="hosted", capabilities=_caps("hosted"))
        router = _router(clock, hosted, limits={"hosted": QuotaLimits(rpm=30, tpd=200_000)})
        quota = router.stats()[0]["quota"]
        assert quota["limits"] == {"rpm": 30, "rpd": 0, "tpm": 0, "tpd": 200_000}


class TestRouterHandlesRateLimits:
    async def test_a_429_fails_over_and_benches_for_the_named_reset(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(
            name="hosted",
            capabilities=_caps("hosted", quality_tier=3),
            script=[ScriptedTurn(raise_error=_rate_limited(retry_after_s=120))],
        )
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1))
        router = _router(clock, hosted, local)

        response = await router.complete(_request())
        assert response.provider == "local"
        assert router.plan(_request()).excluded == {"hosted": "rate_limited"}

        clock.advance(121)
        assert "hosted" not in router.plan(_request()).excluded
        assert (await router.complete(_request())).provider == "hosted"

    async def test_429s_never_open_the_circuit(self, clock: FrozenClock) -> None:
        """An exhausted quota is not an outage, and must not look like one on the dashboard."""
        hosted = EchoProvider(
            name="hosted",
            capabilities=_caps("hosted", quality_tier=3),
            script=[ScriptedTurn(raise_error=_rate_limited(retry_after_s=1)) for _ in range(8)],
        )
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1))
        router = _router(clock, hosted, local, circuit_failure_threshold=3)

        for _ in range(8):
            assert (await router.complete(_request())).provider == "local"
            clock.advance(2)

        breaker = router._stats["hosted"].breaker
        assert breaker.state is CircuitState.CLOSED
        assert breaker.consecutive_failures == 0
        assert hosted.call_count == 8

    async def test_a_429_without_a_reset_uses_the_default_cooldown(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(
            name="hosted",
            capabilities=_caps("hosted", quality_tier=3),
            script=[ScriptedTurn(raise_error=_rate_limited())],
        )
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1))
        router = _router(clock, hosted, local)
        await router.complete(_request())
        assert router.quota.cooldown_remaining("hosted") == pytest.approx(30.0)

    async def test_other_failures_still_count_against_the_breaker(self, clock: FrozenClock) -> None:
        boom = ProviderError("500", provider="hosted", details={"status": 500}, retryable=True)
        hosted = EchoProvider(
            name="hosted",
            capabilities=_caps("hosted", quality_tier=3),
            script=[ScriptedTurn(raise_error=boom)],
        )
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1))
        router = _router(clock, hosted, local)
        await router.complete(_request())
        assert router._stats["hosted"].breaker.consecutive_failures == 1
        assert router.quota.cooldown_remaining("hosted") == 0.0

    async def test_a_rate_limited_request_releases_its_token_estimate(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(
            name="hosted",
            capabilities=_caps("hosted", quality_tier=3),
            script=[ScriptedTurn(raise_error=_rate_limited(retry_after_s=1))],
        )
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1))
        router = _router(clock, hosted, local, limits={"hosted": QuotaLimits(tpd=10_000)})
        await router.complete(_request())
        snapshot = router.quota.snapshot("hosted")
        assert snapshot["day_requests"] == 1
        assert snapshot["day_tokens"] == 0

    async def test_streaming_fails_over_on_a_429_before_the_first_token(
        self, clock: FrozenClock
    ) -> None:
        hosted = EchoProvider(
            name="hosted",
            capabilities=_caps("hosted", quality_tier=3),
            script=[ScriptedTurn(raise_error=_rate_limited(retry_after_s=60))],
        )
        local = EchoProvider(name="local", capabilities=_caps("local", quality_tier=1))
        router = _router(clock, hosted, local)
        chunks = [chunk async for chunk in router.stream(_request())]
        assert {c.provider for c in chunks} == {"local"}
        assert router.quota.cooldown_remaining("hosted") == pytest.approx(60.0)
        assert router._stats["hosted"].breaker.consecutive_failures == 0

    async def test_streaming_settles_reported_usage(self, clock: FrozenClock) -> None:
        hosted = EchoProvider(name="hosted", capabilities=_caps("hosted"))
        router = _router(clock, hosted, limits={"hosted": QuotaLimits(tpd=10_000)})
        chunks = [chunk async for chunk in router.stream(_request())]
        reported = next(c.usage for c in chunks if c.usage is not None)
        assert router.quota.snapshot("hosted")["day_tokens"] == reported.total_tokens


# -- configuration ---------------------------------------------------------------------


class TestQuotaConfiguration:
    def test_limits_are_read_from_the_environment(self, monkeypatch) -> None:
        for key, value in {"RPM": "30", "RPD": "1000", "TPM": "8000", "TPD": "200000"}.items():
            monkeypatch.setenv(f"AURA_PROVIDER_LOCALGPU_{key}", value)
        monkeypatch.setenv("AURA_PROVIDER_LOCALGPU_BASE_URL", "http://vllm:8000/v1")
        monkeypatch.setenv("AURA_PROVIDER_LOCALGPU_MODEL", "m")
        config = ProviderConfig.from_env("localgpu")
        assert (config.rpm, config.rpd, config.tpm, config.tpd) == (30, 1000, 8000, 200_000)

    def test_only_providers_that_declare_a_limit_are_tracked(self) -> None:
        settings = LLMSettings(
            providers=(
                ProviderConfig(name="a", kind="echo", model="m", rpm=30),
                ProviderConfig(name="b", kind="echo", model="m"),
            )
        )
        assert quota_limits_from_settings(settings) == {"a": QuotaLimits(rpm=30)}

    def test_health_path_must_be_absolute(self) -> None:
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="p", kind="echo", model="m", health_path="models")
        assert ProviderConfig(name="p", kind="echo", model="m").health_path == "/models"

    def test_the_container_wires_declared_limits(self, monkeypatch) -> None:
        from aura_core.config.settings import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_LLM_PROVIDERS", "echo")
        monkeypatch.setenv("AURA_PROVIDER_ECHO_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_ECHO_MODEL", "echo-1")
        monkeypatch.setenv("AURA_PROVIDER_ECHO_RPM", "7")
        monkeypatch.setenv("AURA_LLM_RATE_LIMIT_COOLDOWN_S", "45")
        container = Container(load_settings(refresh=True, validate=False))
        assert container.router.quota.limits("echo") == QuotaLimits(rpm=7)
        assert container.router.quota.cooldown("echo", None) == 45
