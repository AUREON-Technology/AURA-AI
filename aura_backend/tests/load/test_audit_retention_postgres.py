"""Retention purge and erasure pseudonymisation against a real Postgres (audit FSA-016).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. The in-memory sinks prove
the semantics; this proves the SQL -- the command-tag counting, the JSONB session filter, the
UPDATE that drops the source IP -- on a migrated database.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.postgres_audit import PostgresAuditSink
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.adapters.postgres_telemetry import PostgresTelemetrySink
from aura_core.domain.identifiers import CorrelationId, SessionId, TurnId
from aura_core.domain.telemetry import TurnTelemetry
from aura_core.ports.runtime import AuditEvent
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "retention") as url:
        yield url


async def test_audit_events_purge_and_pseudonymise(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    sink = PostgresAuditSink(database_url, pool=pool)
    try:
        for actor, days in (("alice@example.com", 400), ("alice@example.com", 2), ("bob@example.com", 2)):
            await sink.record(AuditEvent(
                action="login", actor=actor, outcome="ok", at=NOW - timedelta(days=days), source_ip="203.0.113.9",
            ))
        assert await sink.purge_before(NOW - timedelta(days=365)) == 1
        assert await sink.pseudonymise_actor("alice@example.com", "erased:abc") == 1
        events = {e.actor: e for e in await sink.query(limit=10)}
        assert set(events) == {"erased:abc", "bob@example.com"}
        assert events["erased:abc"].source_ip is None
        assert events["bob@example.com"].source_ip == "203.0.113.9"
    finally:
        await pool.close()


async def test_telemetry_purges_by_age_and_by_session(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    sink = PostgresTelemetrySink(database_url, pool=pool)
    try:
        for session, days in (("mine", 100), ("mine", 1), ("mine", 2), ("theirs", 1)):
            await sink.record_turn(TurnTelemetry(
                correlation_id=CorrelationId.new(), turn_id=TurnId.new(), session_id=SessionId(session),
                started_at=NOW - timedelta(days=days),
            ))
        assert await sink.purge_before(NOW - timedelta(days=90)) == 1
        assert await sink.purge_sessions({"mine"}) == 2
        assert await sink.purge_sessions(set()) == 0
        summary = await sink.recent_summary(since=NOW - timedelta(days=365))
        assert summary["turns"] == 1
    finally:
        await pool.close()
