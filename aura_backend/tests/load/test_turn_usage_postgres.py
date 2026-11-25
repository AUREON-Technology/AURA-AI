"""Owner-scoped turn usage against a real Postgres (migration 0033).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. ``tests/unit/test_turn_usage.py``
proves the semantics on the in-memory sink; this proves the SQL on a database built by ``alembic
upgrade head`` -- the ``owner_ref`` column and its partial index, the owner match in the ``WHERE``,
the JSONB payload read back as the same view, and the command-tag counting in ``purge_owner``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from aura_core.adapters.postgres_store import PostgresPool
from aura_core.adapters.postgres_telemetry import PostgresTelemetrySink
from aura_core.domain.identifiers import CorrelationId, SessionId, TurnId
from aura_core.domain.telemetry import TurnTelemetry
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "turn_usage") as url:
        yield url


def _turn(owner_ref: str | None, **overrides) -> TurnTelemetry:
    base = dict(
        correlation_id=CorrelationId.new(), turn_id=TurnId.new(), started_at=NOW, owner_ref=owner_ref,
        session_id=SessionId("s-1"), provider="hosted1", model="m-1", prompt_tokens=120,
        completion_tokens=30, cost_usd=0.0021,
    )
    base.update(overrides)
    return TurnTelemetry(**base)


async def test_only_the_owner_reads_a_turn_back_and_erasure_removes_it(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    sink = PostgresTelemetrySink(database_url, pool=pool)
    try:
        mine, theirs, unowned = _turn("ref-a"), _turn("ref-b"), _turn(None)
        for turn in (mine, theirs, unowned):
            await sink.record_turn(turn)

        view = await sink.get_turn_usage(mine.turn_id.value, "ref-a")
        assert view["turn_id"] == mine.turn_id.value and view["session_id"] == "s-1"
        assert view["provider"] == "hosted1" and view["model"] == "m-1"
        assert view["usage"]["prompt_tokens"] == 120 and view["usage"]["completion_tokens"] == 30
        assert view["usage"]["total_tokens"] == 150 and view["usage"]["cost_usd"] == 0.0021
        assert view["started_at"] == NOW.isoformat()
        assert "ref-a" not in repr(view)

        assert await sink.get_turn_usage(mine.turn_id.value, "ref-b") is None, "someone else's turn"
        assert await sink.get_turn_usage(mine.turn_id.value, "") is None
        assert await sink.get_turn_usage(unowned.turn_id.value, "") is None, "an empty ref must not match NULL"
        assert await sink.get_turn_usage(TurnId.new().value, "ref-a") is None

        # The reference lives in its own column and never in the stored payload JSON.
        async with (await pool._get_pool()).acquire() as conn:
            payload_text = await conn.fetchval(
                "SELECT payload::text FROM aura_turn_telemetry WHERE turn_id = $1", mine.turn_id.value)
            stored = await conn.fetchval(
                "SELECT owner_ref FROM aura_turn_telemetry WHERE turn_id = $1", mine.turn_id.value)
        assert stored == "ref-a" and "ref-a" not in payload_text

        assert await sink.purge_owner("") == 0, "an empty reference must never match the unowned rows"
        assert await sink.purge_owner("ref-a") == 1
        assert await sink.get_turn_usage(mine.turn_id.value, "ref-a") is None
        assert await sink.get_turn_usage(theirs.turn_id.value, "ref-b") is not None
        assert await sink.get_turn_usage(unowned.turn_id.value, "") is None
    finally:
        await pool.close()


async def test_a_replayed_turn_does_not_change_its_owner(database_url) -> None:
    """``ON CONFLICT (turn_id) DO NOTHING``: recording the same turn id again under another owner
    must not hand it to them."""
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    sink = PostgresTelemetrySink(database_url, pool=pool)
    try:
        original = _turn("ref-a")
        await sink.record_turn(original)
        await sink.record_turn(_turn("ref-b", turn_id=original.turn_id))
        assert await sink.get_turn_usage(original.turn_id.value, "ref-a") is not None
        assert await sink.get_turn_usage(original.turn_id.value, "ref-b") is None
    finally:
        await pool.close()
