"""ADR-052: production inference monitoring. Real in-memory telemetry sink and cache (the same ports the gateway
uses), real drift history; nothing about the logic is mocked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.runtime import InMemoryTelemetrySink
from aura_core.domain.identifiers import CorrelationId, SessionId, TurnId, UserId
from aura_core.domain.telemetry import TurnOutcome, TurnTelemetry
from aura_core.learning.model_monitor import PLATFORM_OWNER, SERIES, last_report, rebaseline, run_model_monitor
from aura_core.llm.cache import InMemoryCache
from aura_core.tools.drift import DriftStore

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def turn(model: str, *, ok: bool = True, ms: float = 800.0, degraded: bool = False, cost: float = 0.001,
         minutes_ago: float = 10, groundedness: float | None = 0.9, blocked: bool = False) -> TurnTelemetry:
    return TurnTelemetry(
        correlation_id=CorrelationId.new(), turn_id=TurnId.new(), session_id=SessionId("s"),
        started_at=NOW - timedelta(minutes=minutes_ago), model=model,
        outcome=TurnOutcome.BLOCKED if blocked else (TurnOutcome.SUCCESS if ok else TurnOutcome.ERROR), total_ms=ms, fallback_count=1 if degraded else 0,
        cost_usd=cost, groundedness=groundedness)


async def fill(sink, model: str, n: int, **kw) -> None:
    for _ in range(n):
        await sink.record_turn(turn(model, **kw))


async def monitor(sink, cache, **kw) -> dict:
    return await run_model_monitor(sink, cache, clock=lambda: NOW, **kw)


class TestFirstRunAndStability:
    async def test_the_first_pass_records_every_series_and_pins_its_baseline(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30)
        report = await monitor(sink, cache)
        assert report["models_observed"] == ["qwen"] and report["drift"] == []
        assert {f["metric"] for f in report["findings"]} >= {"success_rate", "p95_latency_ms", "groundedness"}
        assert all(f["status"] == "baseline" and f["baseline_pinned"] == "auto" for f in report["findings"])
        assert report["evaluation_triggered"] is False and report["action_required"] is None
        data = await DriftStore(cache).load(PLATFORM_OWNER, "qwen", "success_rate")
        assert data["entries"][-1]["value"] == 1.0 and data["baseline"]["value"] == 1.0

    async def test_the_same_traffic_again_is_stable(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30)
        await monitor(sink, cache)
        again = await monitor(sink, cache)
        assert again["drift"] == [] and all(f["status"] == "stable" for f in again["findings"])
        assert (await DriftStore(cache).load(PLATFORM_OWNER, "qwen", "success_rate"))["entries"].__len__() == 2

    async def test_a_model_with_too_few_turns_is_not_judged_on_noise(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "rare", 5)
        report = await monitor(sink, cache, min_turns=20)
        assert report["models_observed"] == [] and report["observations"] == 0

    async def test_turns_outside_the_window_are_ignored(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "old", 30, minutes_ago=60 * 30)
        assert (await monitor(sink, cache, window_hours=24))["models_observed"] == []


class TestDrift:
    async def drifted(self):
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30)
        await monitor(sink, cache)  # baseline: all ok, 800 ms
        bad = InMemoryTelemetrySink()
        await fill(bad, "qwen", 20, ok=False, ms=2400.0, degraded=True, groundedness=0.5)
        await fill(bad, "qwen", 20)
        return bad, cache

    async def test_a_worse_model_is_flagged_on_the_metrics_that_moved(self) -> None:
        bad, cache = await self.drifted()
        report = await monitor(bad, cache)
        moved = {f["metric"]: f for f in report["drift"]}
        assert {"success_rate", "non_degraded_rate", "groundedness"} <= set(moved)
        assert moved["success_rate"]["reference"] == 1.0 and moved["success_rate"]["current"] == 0.5
        assert report["action_required"] == "evaluate_or_retrain" and report["evaluation_triggered"] is False

    async def test_latency_and_cost_are_judged_by_relative_rise(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30, ms=800.0, cost=0.001)
        await monitor(sink, cache)
        slow = InMemoryTelemetrySink()
        await fill(slow, "qwen", 30, ms=1500.0, cost=0.0012)
        report = await monitor(slow, cache)
        flagged = {f["metric"] for f in report["drift"]}
        assert "p95_latency_ms" in flagged, "800 -> 1500 ms is +87%"
        assert "avg_cost_usd" not in flagged, "+20% cost is inside the allowed 50%"

    async def test_a_small_wobble_is_stable(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30)
        await monitor(sink, cache)
        wobble = InMemoryTelemetrySink()
        await fill(wobble, "qwen", 29)
        await fill(wobble, "qwen", 1, ok=False)  # 96.7% success
        assert (await monitor(wobble, cache))["drift"] == []

    async def test_drift_is_measured_against_the_pin_until_an_operator_rebaselines(self) -> None:
        bad, cache = await self.drifted()
        assert (await monitor(bad, cache))["drift"]
        assert (await monitor(bad, cache))["drift"], "a slow decline is not normalised away by a moving reference"
        result = await rebaseline(cache, model="qwen")
        assert "qwen:success_rate" in result["rebaselined"]
        assert (await monitor(bad, cache))["drift"] == [], "after a deliberate re-baseline the new level is the reference"

    async def test_each_model_has_its_own_series(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "a", 30)
        await fill(sink, "b", 30)
        await monitor(sink, cache)
        degraded = InMemoryTelemetrySink()
        await fill(degraded, "a", 30)
        await fill(degraded, "b", 30, ok=False)
        drift = (await monitor(degraded, cache))["drift"]
        assert {f["model"] for f in drift} == {"b"}


class TestReportAndLimits:
    async def test_the_last_report_is_kept_for_the_admin_api(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        assert await last_report(cache) is None
        await fill(sink, "qwen", 30)
        report = await monitor(sink, cache)
        assert (await last_report(cache))["generated_at"] == report["generated_at"]

    async def test_a_series_that_cannot_be_registered_is_reported_not_fatal(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        for i in range(12):  # 12 models x 5 metrics (no tool calls, so no tool series) = 60 > the 50 per owner
            await fill(sink, f"m{i:02d}", 25)
        report = await monitor(sink, cache)
        assert report["skipped"] and report["observations"] >= 50
        assert all("at most" in s["reason"] for s in report["skipped"])

    async def test_a_metric_the_telemetry_lacks_is_skipped(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30, groundedness=None)
        assert "groundedness" not in {f["metric"] for f in (await monitor(sink, cache))["findings"]}

    def test_the_series_table_is_what_the_adr_says(self) -> None:
        assert [s[0] for s in SERIES] == ["success_rate", "non_degraded_rate", "tool_success_rate", "groundedness",
                                           "refusal_rate", "p95_latency_ms", "avg_cost_usd"]

    async def test_concurrent_monitors_do_not_lose_observations(self) -> None:
        import asyncio

        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30)
        await asyncio.gather(*(monitor(sink, cache) for _ in range(5)))
        data = await DriftStore(cache).load(PLATFORM_OWNER, "qwen", "success_rate")
        assert len({e["seq"] for e in data["entries"]}) == len(data["entries"]) == 5


class TestWiring:
    def test_it_is_off_by_default_and_scheduled_only_when_enabled(self, monkeypatch) -> None:
        import importlib

        import celery_app
        from aura_core.config.settings import LearningSettings

        assert LearningSettings().model_monitor_enabled is False
        assert "model-monitor" not in celery_app.celery_app.conf.beat_schedule
        monkeypatch.setenv("AURA_MODEL_MONITOR_ENABLED", "true")
        try:
            reloaded = importlib.reload(celery_app)
            assert reloaded.celery_app.conf.beat_schedule["model-monitor"]["task"] == "tasks.model_monitor_task"
        finally:
            monkeypatch.delenv("AURA_MODEL_MONITOR_ENABLED")
            importlib.reload(celery_app)



class TestAdminRoutes:
    """``GET /api/v3/admin/model-monitor`` and ``POST .../rebaseline`` over a real container."""

    @staticmethod
    def client(container, role: str = "Admin"):
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app

        async def authenticator(request):
            return Principal(user_id=UserId("admin-caller"), role=role)

        return TestClient(build_app(container, authenticator=authenticator))

    async def test_the_report_is_empty_before_the_job_has_run_and_shows_it_after(self, container, monkeypatch) -> None:
        # These go through the real route and a monitor on the real clock, so the turns must be recent by it
        # (a fixed NOW passed only on the day it was written).
        monkeypatch.setitem(globals(), "NOW", datetime.now(timezone.utc))
        await container.startup()
        with self.client(container) as client:
            first = client.get("/api/v3/admin/model-monitor").json()
            assert first["report"] is None and first["enabled"] is False
            await fill(container.telemetry_sink, "qwen", 30, minutes_ago=5)
            report = await run_model_monitor(container.telemetry_sink, container.cache_backend)
            shown = client.get("/api/v3/admin/model-monitor").json()["report"]
            assert shown["generated_at"] == report["generated_at"] and shown["models_observed"] == ["qwen"]

    async def test_rebaseline_pins_the_latest_observation_and_is_audited(self, container, monkeypatch) -> None:
        # These go through the real route and a monitor on the real clock, so the turns must be recent by it
        # (a fixed NOW passed only on the day it was written).
        monkeypatch.setitem(globals(), "NOW", datetime.now(timezone.utc))
        await container.startup()
        await fill(container.telemetry_sink, "qwen", 30, minutes_ago=5)
        await run_model_monitor(container.telemetry_sink, container.cache_backend)
        with self.client(container) as client:
            result = client.post("/api/v3/admin/model-monitor/rebaseline", params={"model": "qwen"})
        assert result.status_code == 200 and "qwen:success_rate" in result.json()["rebaselined"]
        actions = [e.action for e in await container.audit.query()]
        assert "model_monitor.rebaseline" in actions

    async def test_a_non_admin_is_refused_on_both(self, container) -> None:
        await container.startup()
        with self.client(container, role="User") as client:
            assert client.get("/api/v3/admin/model-monitor").status_code == 403
            assert client.post("/api/v3/admin/model-monitor/rebaseline").status_code == 403


class TestRefusalRateAndAlertSignal:
    async def test_a_model_that_starts_refusing_more_is_flagged(self) -> None:
        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30)
        first = await monitor(sink, cache)
        assert next(f for f in first["findings"] if f["metric"] == "refusal_rate")["current"] == 0.0
        await fill(sink, "qwen", 20, blocked=True, ok=False, minutes_ago=5)
        second = await monitor(sink, cache)
        refusal = next(f for f in second["drift"] if f["metric"] == "refusal_rate")
        assert refusal["model"] == "qwen" and refusal["current"] > refusal["reference"]

    async def test_the_report_becomes_gauges_a_grafana_alert_can_fire_on(self) -> None:
        from aura_core.observability.metrics import Metrics

        sink, cache = InMemoryTelemetrySink(), InMemoryCache()
        await fill(sink, "qwen", 30)
        await monitor(sink, cache)
        await fill(sink, "qwen", 40, ok=False, minutes_ago=5)
        report = await monitor(sink, cache)
        metrics = Metrics(enabled=True)
        metrics.set_model_drift(report, age_s=42.0)
        text = metrics.render().decode()
        assert 'aura_model_drift_active{metric="success_rate",model="qwen"} 1.0' in text.replace(
            'model="qwen",metric="success_rate"', 'metric="success_rate",model="qwen"')
        assert 'aura_model_drift_active{metric="refusal_rate",model="qwen"} 0.0' in text.replace(
            'model="qwen",metric="refusal_rate"', 'metric="refusal_rate",model="qwen"')
        assert "aura_model_monitor_report_age_seconds 42.0" in text
