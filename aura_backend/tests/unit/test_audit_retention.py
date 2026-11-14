"""The audit log and turn telemetry have a retention policy and take part in erasure (audit FSA-016).

Both tables kept email addresses (the audit actor) and source IPs forever, and ``forget_user``
listed them under ``not_erased``. Now a daily task purges rows past the configured retention,
and erasure pseudonymises the audit actor (the events stay: who-did-what to the system is a
security record, the *identity* in it is personal data) and drops the telemetry of the user's
conversations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.runtime import InMemoryAuditSink, InMemoryTelemetrySink
from aura_core.config.settings import ObservabilitySettings
from aura_core.domain.identifiers import CorrelationId, SessionId, TurnId
from aura_core.domain.telemetry import TurnTelemetry
from aura_core.ports.runtime import AuditEvent

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _event(actor: str, *, days_ago: int, ip: str | None = "203.0.113.9") -> AuditEvent:
    return AuditEvent(action="login", actor=actor, outcome="ok", at=NOW - timedelta(days=days_ago), source_ip=ip)


def _turn(session: str, *, days_ago: int) -> TurnTelemetry:
    return TurnTelemetry(
        correlation_id=CorrelationId.new(), turn_id=TurnId.new(), session_id=SessionId(session),
        started_at=NOW - timedelta(days=days_ago),
    )


class TestRetentionSettings:
    def test_defaults_keep_audit_a_year_and_telemetry_a_quarter(self, monkeypatch) -> None:
        monkeypatch.delenv("AURA_AUDIT_RETENTION_DAYS", raising=False)
        monkeypatch.delenv("AURA_TELEMETRY_RETENTION_DAYS", raising=False)
        settings = ObservabilitySettings.from_env()
        assert settings.audit_retention_days == 365
        assert settings.telemetry_retention_days == 90

    def test_the_environment_sets_them_and_zero_means_keep(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_AUDIT_RETENTION_DAYS", "30")
        monkeypatch.setenv("AURA_TELEMETRY_RETENTION_DAYS", "0")
        settings = ObservabilitySettings.from_env()
        assert settings.audit_retention_days == 30
        assert settings.telemetry_retention_days == 0


class TestTheInMemorySinksPurgeAndPseudonymise:
    async def test_audit_rows_past_the_cutoff_go_and_newer_ones_stay(self) -> None:
        sink = InMemoryAuditSink()
        await sink.record(_event("a@example.com", days_ago=400))
        await sink.record(_event("a@example.com", days_ago=10))
        assert await sink.purge_before(NOW - timedelta(days=365)) == 1
        assert [e.at for e in await sink.query()] == [NOW - timedelta(days=10)]

    async def test_pseudonymising_an_actor_keeps_the_event_but_not_the_identity(self) -> None:
        sink = InMemoryAuditSink()
        await sink.record(_event("a@example.com", days_ago=1))
        await sink.record(_event("b@example.com", days_ago=1))
        assert await sink.pseudonymise_actor("a@example.com", "erased:7f3a") == 1
        events = {e.actor: e for e in await sink.query()}
        assert set(events) == {"erased:7f3a", "b@example.com"}
        assert events["erased:7f3a"].source_ip is None
        assert events["b@example.com"].source_ip == "203.0.113.9"

    async def test_telemetry_rows_past_the_cutoff_go(self) -> None:
        sink = InMemoryTelemetrySink()
        await sink.record_turn(_turn("s1", days_ago=100))
        await sink.record_turn(_turn("s1", days_ago=5))
        assert await sink.purge_before(NOW - timedelta(days=90)) == 1
        assert len(sink) == 1

    async def test_a_user_s_conversations_can_be_dropped_from_telemetry(self) -> None:
        sink = InMemoryTelemetrySink()
        await sink.record_turn(_turn("mine", days_ago=1))
        await sink.record_turn(_turn("mine", days_ago=2))
        await sink.record_turn(_turn("theirs", days_ago=1))
        assert await sink.purge_sessions({"mine"}) == 2
        assert len(sink) == 1


class TestTheRetentionTask:
    async def test_the_task_purges_both_sinks_by_the_configured_retention(self, container, monkeypatch) -> None:
        import tasks
        from dataclasses import replace

        settings = container.settings
        container.settings = replace(
            settings, observability=replace(settings.observability, audit_retention_days=30, telemetry_retention_days=7)
        )
        now = container.clock.now()
        await container.audit.record(AuditEvent(action="x", actor="a", outcome="ok", at=now - timedelta(days=31)))
        await container.audit.record(AuditEvent(action="x", actor="a", outcome="ok", at=now - timedelta(days=1)))
        await container.telemetry_sink.record_turn(TurnTelemetry(
            correlation_id=CorrelationId.new(), turn_id=TurnId.new(), started_at=now - timedelta(days=8)
        ))
        await container.telemetry_sink.record_turn(TurnTelemetry(
            correlation_id=CorrelationId.new(), turn_id=TurnId.new(), started_at=now - timedelta(days=1)
        ))

        async def cached():
            return container

        monkeypatch.setattr(tasks, "_ensure_worker_container", cached)
        result = await tasks._run_retention_purge()
        assert result == {"status": "ok", "audit_purged": 1, "telemetry_purged": 1}
        assert len(await container.audit.query()) == 1
        assert len(container.telemetry_sink) == 1

    def test_the_task_is_scheduled_daily_on_the_realtime_queue(self) -> None:
        import tasks
        from celery_app import celery_app

        entry = celery_app.conf.beat_schedule["purge-audit-and-telemetry"]
        assert entry["task"] == "tasks.purge_audit_and_telemetry_task"
        assert entry["options"]["queue"] == "realtime"
        assert tasks.purge_audit_and_telemetry_task.queue == "realtime"


class TestErasureReachesTheLogs:
    async def test_the_actor_is_pseudonymised_and_the_conversations_telemetry_dropped(self, container) -> None:
        from api.admin_platform import NOT_ERASED, _erase_core

        now = container.clock.now()
        await container.audit.record(AuditEvent(action="login", actor="alice-sub", outcome="ok", at=now, source_ip="198.51.100.4"))
        await container.audit.record(AuditEvent(action="login", actor="alice@example.com", outcome="ok", at=now, source_ip="198.51.100.4"))
        await container.audit.record(AuditEvent(action="login", actor="bob-sub", outcome="ok", at=now))
        await container.telemetry_sink.record_turn(TurnTelemetry(
            correlation_id=CorrelationId.new(), turn_id=TurnId.new(), session_id=SessionId("c1"), started_at=now
        ))
        await container.telemetry_sink.record_turn(TurnTelemetry(
            correlation_id=CorrelationId.new(), turn_id=TurnId.new(), session_id=SessionId("other"), started_at=now
        ))

        counts, failed = await _erase_core(container, "alice-sub", {"c1"}, aliases={"alice@example.com"})

        assert failed == []
        assert counts["audit_events_pseudonymised"] == 2
        assert counts["telemetry_turns"] == 1
        actors = {e.actor for e in await container.audit.query()}
        assert "alice-sub" not in actors and "alice@example.com" not in actors and "bob-sub" in actors
        assert all(e.source_ip is None for e in await container.audit.query() if e.actor != "bob-sub")
        assert len(container.telemetry_sink) == 1
        assert not any("audit log" == item or "turn telemetry" == item for item in NOT_ERASED), NOT_ERASED
