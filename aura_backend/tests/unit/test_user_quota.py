"""Per-user daily token cap (module readiness WP2; D-05, NFR-02).

The provider budgets (``RPM/RPD/TPM/TPD``) are shared by everyone, so one heavy Engineer user
could spend the whole day's Claude allowance and lock every other user out. ``USER_TPD`` caps
what any one person may spend on a provider per UTC day. A request that would exceed it is
skipped before it is sent; with nothing else able to answer, the turn fails with
``user_quota_exhausted`` naming when it resets -- not a generic "model not answering".

Like the provider counters: the worst case (prompt + full output cap) is reserved before
sending and settled to the reported usage; counts are shared across workers through the cache's
atomic ``incr``. A request with no user (an unhinted background call) is never counted against
anyone.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.config.settings import ProviderConfig
from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import AllProvidersFailedError, UserQuotaExhaustedError
from aura_core.domain.llm import RoutingPolicy, SamplingParams, WorkloadClass
from aura_core.llm import InMemoryCache, ProviderRouter, QuotaLimits, QuotaTracker
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.llm.workload import hint_for, workload_scope

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CAP = 1_000


def _wall(clock: FrozenClock):
    return lambda: clock.now().timestamp()


def _request(max_output_tokens: int = 100) -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of([Message.user("hello there")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=max_output_tokens),
    )


def _claude() -> EchoProvider:
    return EchoProvider(name="claude1", capabilities=ProviderCapabilities(
        model_id="claude1", context_window=8192, max_output_tokens=600, quality_tier=3,
    ))


def _router(clock: FrozenClock, *providers, limits=None, store=None) -> ProviderRouter:
    quota = QuotaTracker(limits or {"claude1": QuotaLimits(user_tpd=CAP)},
                         wall=_wall(clock), store=store)
    return ProviderRouter(list(providers), default_policy=RoutingPolicy.QUALITY_FIRST,
                          clock=clock.monotonic, quota=quota)


def _as(user: str):
    return workload_scope(hint_for(WorkloadClass.ENGINEER, user_key=user))


class TestTracker:
    def test_a_user_is_refused_past_their_cap_and_others_are_not(self) -> None:
        clock = FrozenClock(_START)
        tracker = QuotaTracker({"claude1": QuotaLimits(user_tpd=CAP)}, wall=_wall(clock))
        first, refusal = tracker.try_reserve("claude1", 600, user_key="alice")
        assert first is not None and refusal is None
        assert tracker.refusal("claude1", 600, user_key="alice") == "user_quota_tpd"
        assert tracker.refusal("claude1", 400, user_key="alice") is None  # exactly at the cap
        assert tracker.refusal("claude1", 600, user_key="bob") is None

    def test_settling_to_real_usage_frees_the_unused_reservation(self) -> None:
        clock = FrozenClock(_START)
        tracker = QuotaTracker({"claude1": QuotaLimits(user_tpd=CAP)}, wall=_wall(clock))
        reservation, _ = tracker.try_reserve("claude1", 900, user_key="alice")
        tracker.settle(reservation, 100)
        assert tracker.refusal("claude1", 900, user_key="alice") is None

    def test_the_cap_resets_at_utc_midnight(self) -> None:
        clock = FrozenClock(_START)
        tracker = QuotaTracker({"claude1": QuotaLimits(user_tpd=CAP)}, wall=_wall(clock))
        tracker.try_reserve("claude1", 1_000, user_key="alice")
        assert tracker.refusal("claude1", 1, user_key="alice") == "user_quota_tpd"
        clock.advance(24 * 3600)
        assert tracker.refusal("claude1", 1_000, user_key="alice") is None

    def test_a_request_with_no_user_is_never_counted_or_refused(self) -> None:
        clock = FrozenClock(_START)
        tracker = QuotaTracker({"claude1": QuotaLimits(user_tpd=CAP)}, wall=_wall(clock))
        for _ in range(5):
            reservation, refusal = tracker.try_reserve("claude1", 900)
            assert reservation is not None and refusal is None

    def test_an_undeclared_user_cap_refuses_nobody(self) -> None:
        clock = FrozenClock(_START)
        tracker = QuotaTracker({}, wall=_wall(clock))
        for _ in range(5):
            assert tracker.try_reserve("claude1", 10_000, user_key="alice")[1] is None

    def test_the_provider_wide_daily_cap_still_applies(self) -> None:
        clock = FrozenClock(_START)
        tracker = QuotaTracker({"claude1": QuotaLimits(tpd=1_500, user_tpd=CAP)}, wall=_wall(clock))
        tracker.try_reserve("claude1", 900, user_key="alice")
        assert tracker.refusal("claude1", 900, user_key="bob") == "quota_tpd"

    async def test_workers_share_one_user_count_through_the_cache(self) -> None:
        clock = FrozenClock(_START)
        store = InMemoryCache()
        a = QuotaTracker({"claude1": QuotaLimits(user_tpd=CAP)}, wall=_wall(clock), store=store)
        b = QuotaTracker({"claude1": QuotaLimits(user_tpd=CAP)}, wall=_wall(clock), store=store)
        a.try_reserve("claude1", 700, user_key="alice")
        await a.flush()
        # Worker b has not seen alice yet; its own reservation folds the shared total back in.
        b.try_reserve("claude1", 100, user_key="alice")
        await b.flush()
        assert b.refusal("claude1", 300, user_key="alice") == "user_quota_tpd"

    def test_details_name_the_reset_and_the_limit(self) -> None:
        clock = FrozenClock(datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc))
        tracker = QuotaTracker({"claude1": QuotaLimits(user_tpd=CAP)}, wall=_wall(clock))
        assert tracker.user_quota_details(["claude1"]) == {
            "reset_at": "2026-01-02T00:00:00+00:00", "limit_tokens": CAP,
        }


class TestRouting:
    """Each call settles to the provider's reported usage, so the allowance is pre-spent on the
    router's own tracker rather than by a run of real calls."""

    async def test_over_the_cap_the_turn_says_so(self) -> None:
        clock = FrozenClock(_START)
        claude = _claude()
        router = _router(clock, claude)
        router._quota.try_reserve("claude1", 900, user_key="alice")
        with _as("alice"), pytest.raises(UserQuotaExhaustedError) as excinfo:
            await router.complete(_request(max_output_tokens=600))
        assert excinfo.value.details["reset_at"] == "2026-01-02T00:00:00+00:00"
        assert excinfo.value.details["limit_tokens"] == CAP
        assert excinfo.value.retryable is False and excinfo.value.http_status == 429
        assert claude.call_count == 0, "nothing is sent past the allowance"

    async def test_another_user_is_still_served(self) -> None:
        clock = FrozenClock(_START)
        claude = _claude()
        claude.queue(ScriptedTurn(text="for bob"))
        router = _router(clock, claude)
        router._quota.try_reserve("claude1", 900, user_key="alice")
        with _as("bob"):
            response = await router.complete(_request(max_output_tokens=600))
        assert response.text == "for bob"

    async def test_a_provider_wide_limit_is_not_reported_as_the_user_s(self) -> None:
        clock = FrozenClock(_START)
        claude = _claude()
        router = _router(clock, claude, limits={"claude1": QuotaLimits(tpd=1_000, user_tpd=5_000)})
        router._quota.try_reserve("claude1", 900)
        with _as("alice"), pytest.raises(AllProvidersFailedError) as excinfo:
            await router.complete(_request(max_output_tokens=600))
        assert not isinstance(excinfo.value, UserQuotaExhaustedError)


class TestConfiguration:
    def test_user_tpd_is_read_from_the_environment(self, monkeypatch) -> None:
        from aura_core.config.settings import LLMSettings
        from aura_core.llm import quota_limits_from_settings

        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_USER_TPD", "250000")
        config = ProviderConfig.from_env("claude1")
        assert config.user_tpd == 250_000
        limits = quota_limits_from_settings(LLMSettings(providers=(config,)))
        assert limits["claude1"].user_tpd == 250_000

    def test_a_negative_cap_is_refused(self) -> None:
        with pytest.raises(ValueError):
            QuotaLimits(user_tpd=-1)


class TestThroughTheKernel:
    """A consented Engineer turn over its allowance ends with a refusal the client can word:
    ``user_quota_exhausted`` with ``reset_at``, not retryable -- on the stream and on ``/chat``."""

    @pytest.fixture
    def capped(self, monkeypatch):
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_LLM_PROVIDERS", "echo")
        monkeypatch.setenv("AURA_PROVIDER_ECHO_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_ECHO_MODEL", "echo-1")
        monkeypatch.setenv("AURA_PROVIDER_ECHO_USER_TPD", "1")
        return Container(load_settings(refresh=True, validate=False))

    async def _consent(self, container, user: str) -> None:
        from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION
        from aura_core.domain.identifiers import UserId

        await container.engineer_consent.accept(
            UserId(user), ENGINEER_CONSENT_NOTICE_VERSION, at=_START
        )

    async def test_the_stream_ends_with_a_non_retryable_limit_frame(self, capped) -> None:
        from aura_core.domain.identifiers import UserId
        from aura_core.kernel import TurnRequest

        await capped.startup()
        await self._consent(capped, "eng-1")
        frames = [
            frame async for frame in capped.kernel.stream(TurnRequest(
                message="fix it", user_id=UserId("eng-1"), agent="engineer",
                capabilities=("engineer_agent",), plan=True,
            ))
        ]
        [error] = [f for f in frames if f.get("type") == "error"]
        assert error["error"] == "user_quota_exhausted"
        assert error["retryable"] is False
        assert set(error["details"]) == {"reset_at", "limit_tokens"}

    def test_chat_answers_429_with_the_reset_time(self, capped) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app
        from aura_core.domain.identifiers import UserId

        asyncio.run(self._consent(capped, "eng-1"))

        async def authenticator(_request):
            return Principal(user_id=UserId("eng-1"), role="Developer",
                             capabilities=("engineer_agent",))

        with TestClient(build_app(capped, authenticator=authenticator)) as client:
            response = client.post("/api/v3/chat", json={"message": "fix it", "agent": "engineer"})
        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "user_quota_exhausted" and body["retryable"] is False
        assert "reset_at" in body["details"]
