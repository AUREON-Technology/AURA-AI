"""Admission caps and hosted quotas hold across processes, not per process (audit FSA-025 / B-02).

The gateway runs ``AURA_WORKERS`` uvicorn workers and each Celery child builds its own container,
so a per-process cap of N on a model server was really N x processes, and a hosted provider's
daily budget (``RPD``) was spent once per process -- four workers could send four times the
provider's limit before the first 429. Two fixes, both deliberately without a distributed lock
on the hot path:

* the per-group admission cap is divided by ``AURA_LLM_ADMISSION_PROCESSES`` (the compose files
  set it to ``AURA_WORKERS``), floored at one slot per process, so the sum over the gateway's
  workers is the declared cap;
* the daily request and token counters are shared atomic counters in the cache (``INCRBY``): a
  process learns the global total from every increment it makes and at hydrate, so the budget
  is spent once however many processes serve it.
"""

from __future__ import annotations

import logging

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.config.settings import LLMSettings, ProviderConfig
from aura_core.llm import QuotaLimits, QuotaTracker, admission_limits_from_settings
from aura_core.llm.cache import InMemoryCache


def _wall(clock: FrozenClock):
    return lambda: clock.now().timestamp()


def _settings(max_concurrency: int, processes: int) -> LLMSettings:
    provider = ProviderConfig(name="local", kind="echo", model="m", max_concurrency=max_concurrency)
    return LLMSettings(providers=(provider,), admission_processes=processes)


class TestTheAdmissionCapIsSharedAcrossProcesses:
    def test_the_group_cap_is_divided_by_the_process_count(self) -> None:
        assert admission_limits_from_settings(_settings(4, 4))["local"].max_concurrent == 1
        assert admission_limits_from_settings(_settings(8, 4))["local"].max_concurrent == 2

    def test_one_process_is_the_old_behaviour(self) -> None:
        assert admission_limits_from_settings(_settings(4, 1))["local"].max_concurrent == 4

    def test_a_cap_below_one_per_process_floors_at_one_and_says_so(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="aura.llm"):
            limits = admission_limits_from_settings(_settings(1, 4))
        assert limits["local"].max_concurrent == 1
        assert any("AURA_LLM_ADMISSION_PROCESSES" in r.getMessage() for r in caplog.records), (
            [r.getMessage() for r in caplog.records]
        )

    def test_the_setting_is_read_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_LLM_ADMISSION_PROCESSES", "3")
        assert LLMSettings.from_env().admission_processes == 3
        monkeypatch.delenv("AURA_LLM_ADMISSION_PROCESSES")
        monkeypatch.delenv("AURA_WORKERS", raising=False)
        assert LLMSettings.from_env().admission_processes == 1

    def test_unset_it_follows_the_gateway_s_worker_count(self, monkeypatch) -> None:
        """Railway runs the image's `--workers ${AURA_WORKERS:-4}` with no compose file to set
        the division; the gateway's own worker count is the right default there. An explicit
        value still wins, and a Celery worker (no AURA_WORKERS) keeps one process."""
        monkeypatch.delenv("AURA_LLM_ADMISSION_PROCESSES", raising=False)
        monkeypatch.setenv("AURA_WORKERS", "4")
        assert LLMSettings.from_env().admission_processes == 4
        monkeypatch.setenv("AURA_LLM_ADMISSION_PROCESSES", "2")
        assert LLMSettings.from_env().admission_processes == 2


class TestTheDailyQuotaIsSharedAcrossProcesses:
    async def test_two_processes_spend_one_request_budget(self, clock: FrozenClock) -> None:
        store = InMemoryCache()
        limits = {"h": QuotaLimits(rpd=3)}
        a = QuotaTracker(limits, wall=_wall(clock), store=store)
        b = QuotaTracker(limits, wall=_wall(clock), store=store)
        await a.ensure_hydrated()
        await b.ensure_hydrated()
        assert a.try_reserve("h", 1)[1] is None
        assert a.try_reserve("h", 1)[1] is None
        await a.flush()
        # b has made no request yet; its one reservation is the budget's third and last.
        assert b.try_reserve("h", 1)[1] is None
        await b.flush()
        assert b.try_reserve("h", 1) == (None, "quota_rpd")
        assert b.snapshot("h")["day_requests"] == 3

    async def test_a_late_process_hydrates_the_shared_spend(self, clock: FrozenClock) -> None:
        store = InMemoryCache()
        limits = {"h": QuotaLimits(rpd=2, tpd=1000)}
        a = QuotaTracker(limits, wall=_wall(clock), store=store)
        await a.ensure_hydrated()
        reservation, _ = a.try_reserve("h", 600)
        a.settle(reservation, 100)  # the provider reported far fewer tokens than estimated
        await a.flush()
        b = QuotaTracker(limits, wall=_wall(clock), store=store)
        await b.ensure_hydrated()
        assert b.snapshot("h")["day_requests"] == 1
        assert b.snapshot("h")["day_tokens"] == 100
        assert b.try_reserve("h", 950) == (None, "quota_tpd")

    async def test_tokens_are_shared_too(self, clock: FrozenClock) -> None:
        store = InMemoryCache()
        limits = {"h": QuotaLimits(tpd=1000)}
        a = QuotaTracker(limits, wall=_wall(clock), store=store)
        b = QuotaTracker(limits, wall=_wall(clock), store=store)
        await a.ensure_hydrated()
        await b.ensure_hydrated()
        assert a.try_reserve("h", 600)[1] is None
        await a.flush()
        assert b.try_reserve("h", 300)[1] is None  # 900 globally
        await b.flush()
        assert b.try_reserve("h", 200) == (None, "quota_tpd")


class TestTheCacheIncrementsByAmount:
    async def test_in_memory_incr_takes_an_amount_and_goes_both_ways(self) -> None:
        cache = InMemoryCache()
        assert await cache.incr("k", amount=5) == 5
        assert await cache.incr("k", amount=-2) == 3
        assert await cache.incr("k") == 4
