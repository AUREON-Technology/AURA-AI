"""``InMemoryTelemetrySink`` and the ``TelemetrySink`` wiring (Phase G1).

Before this phase ``KernelDependencies.telemetry_sink`` was never wired -- every turn built a
complete ``TurnTelemetry`` record and nothing kept it. These tests pin the contract
``PostgresTelemetrySink`` was verified against by hand (real Postgres, real
``percentile_cont`` aggregates), against the in-memory reference implementation the rest of the
suite runs on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aura_core.adapters.runtime import InMemoryTelemetrySink
from aura_core.domain.identifiers import CorrelationId, TurnId
from aura_core.domain.telemetry import CacheOutcome, ToolTelemetry, TurnOutcome, TurnTelemetry
from aura_core.kernel import TurnRequest


def _telemetry(**overrides) -> TurnTelemetry:
    base = dict(
        correlation_id=CorrelationId.new(), turn_id=TurnId.new(),
        started_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return TurnTelemetry(**base)


class TestInMemoryTelemetrySink:
    async def test_empty_sink_summarises_to_zero_turns(self) -> None:
        sink = InMemoryTelemetrySink()
        summary = await sink.recent_summary(since=datetime.now(timezone.utc) - timedelta(hours=1))
        assert summary["turns"] == 0
        assert summary["error_rate"] is None

    async def test_turns_before_the_window_are_excluded(self) -> None:
        sink = InMemoryTelemetrySink()
        old = datetime.now(timezone.utc) - timedelta(days=2)
        await sink.record_turn(_telemetry(started_at=old))
        summary = await sink.recent_summary(since=datetime.now(timezone.utc) - timedelta(hours=1))
        assert summary["turns"] == 0

    async def test_error_rate_reflects_non_success_outcomes(self) -> None:
        sink = InMemoryTelemetrySink()
        now = datetime.now(timezone.utc)
        await sink.record_turn(_telemetry(started_at=now, outcome=TurnOutcome.SUCCESS))
        await sink.record_turn(_telemetry(started_at=now, outcome=TurnOutcome.ERROR))
        summary = await sink.recent_summary(since=now - timedelta(hours=1))
        assert summary["turns"] == 2
        assert summary["error_rate"] == 0.5

    async def test_p50_and_p95_latency_are_computed_from_real_values(self) -> None:
        sink = InMemoryTelemetrySink()
        now = datetime.now(timezone.utc)
        for ms in [100.0, 200.0, 300.0, 400.0, 5000.0]:
            await sink.record_turn(_telemetry(started_at=now, total_ms=ms))
        summary = await sink.recent_summary(since=now - timedelta(hours=1))
        assert summary["p50_latency_ms"] == 300.0
        assert summary["p95_latency_ms"] == 5000.0

    async def test_tool_success_rate_counts_failures_across_turns(self) -> None:
        sink = InMemoryTelemetrySink()
        now = datetime.now(timezone.utc)
        await sink.record_turn(_telemetry(
            started_at=now,
            tools=(
                ToolTelemetry(name="calculator", ok=True, duration_ms=5.0),
                ToolTelemetry(name="search", ok=False, duration_ms=5.0),
            ),
        ))
        summary = await sink.recent_summary(since=now - timedelta(hours=1))
        assert summary["tool_success_rate"] == 0.5

    async def test_no_tool_calls_reports_none_not_zero(self) -> None:
        """A tool success rate of 0.0 would falsely say every call failed; None says none ran."""
        sink = InMemoryTelemetrySink()
        now = datetime.now(timezone.utc)
        await sink.record_turn(_telemetry(started_at=now))
        summary = await sink.recent_summary(since=now - timedelta(hours=1))
        assert summary["tool_success_rate"] is None

    async def test_cache_hit_rate(self) -> None:
        sink = InMemoryTelemetrySink()
        now = datetime.now(timezone.utc)
        await sink.record_turn(_telemetry(started_at=now, cache=CacheOutcome(hit=True)))
        await sink.record_turn(_telemetry(started_at=now, cache=CacheOutcome(hit=False)))
        summary = await sink.recent_summary(since=now - timedelta(hours=1))
        assert summary["cache_hit_rate"] == 0.5

    async def test_groundedness_averages_only_turns_that_set_it(self) -> None:
        sink = InMemoryTelemetrySink()
        now = datetime.now(timezone.utc)
        await sink.record_turn(_telemetry(started_at=now, groundedness=0.8))
        await sink.record_turn(_telemetry(started_at=now, groundedness=None))
        summary = await sink.recent_summary(since=now - timedelta(hours=1))
        assert summary["avg_groundedness"] == 0.8

    async def test_len_reflects_every_recorded_turn(self) -> None:
        sink = InMemoryTelemetrySink()
        await sink.record_turn(_telemetry())
        await sink.record_turn(_telemetry())
        assert len(sink) == 2


class TestContainerWiring:
    async def test_a_real_turn_is_recorded_and_summarised(self, container, user) -> None:
        await container.startup()
        await container.kernel.run(TurnRequest(message="hello", user_id=user))

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        summary = await container.telemetry_sink.recent_summary(since=since)
        assert summary["turns"] >= 1
