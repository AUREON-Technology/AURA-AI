"""``PostgresMemoryStore`` update and reinforcement semantics against a real Postgres (F11, F27).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a server whose role can
create databases. Each test runs in its own database built by ``alembic upgrade head``.

The in-memory store's behaviour is pinned in ``tests/unit/test_memory_decay_and_deletion.py``;
this proves the SQL agrees: ``update`` never re-creates a deleted row and never moves access
bookkeeping backwards or clears a supersession, and ``touch_many`` restores the base
confidence kept in the JSONB metadata.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.postgres_store import PostgresMemoryStore, PostgresPool
from aura_core.domain.identifiers import MemoryId, UserId
from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
ALICE = UserId("alice")


@pytest.fixture
async def store():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "mem_test") as url:
        pool = PostgresPool(url, min_size=1, max_size=2)
        try:
            yield PostgresMemoryStore(url, pool=pool)
        finally:
            await pool.close()


def _record(mid: str = "m1", *, confidence: float = 0.8) -> MemoryRecord:
    return MemoryRecord(
        id=MemoryId(mid), kind=MemoryKind.SEMANTIC, owner_id=ALICE, content="A fact.",
        provenance=Provenance(source="t", recorded_at=T0), subject="s", confidence=confidence,
        created_at=T0, last_accessed_at=T0,
    )


async def test_update_never_recreates_a_deleted_row(store) -> None:
    record = _record()
    await store.put(record)
    snapshot = await store.get(record.id)
    assert await store.delete(record.id, owner_id=ALICE)

    await store.update(snapshot.decayed(now=T0 + timedelta(days=30)))
    await store.update(snapshot.superseded(MemoryId("m2")))

    assert await store.get(record.id) is None


async def test_update_does_not_revert_a_concurrent_access_or_supersede(store) -> None:
    record = _record()
    await store.put(record)
    snapshot = await store.get(record.id)

    later = T0 + timedelta(days=30)
    assert await store.touch_many([record.id], owner_id=ALICE, at=later) == 1
    await store.update(replace(snapshot, superseded_by=MemoryId("m2")))
    await store.update(snapshot.decayed(now=later))  # a snapshot from before both

    stored = await store.get(record.id)
    assert stored.access_count == 1
    assert stored.last_accessed_at == later
    assert stored.superseded_by == MemoryId("m2")


async def test_update_is_owner_scoped(store) -> None:
    record = _record()
    await store.put(record)
    await store.update(replace(record, owner_id=UserId("mallory"), confidence=0.1))
    assert (await store.get(record.id)).confidence == pytest.approx(0.8)


async def test_touch_restores_the_base_confidence(store) -> None:
    record = _record(confidence=0.8)
    await store.put(record)
    decayed = record.decayed(now=T0 + timedelta(days=90))
    await store.update(decayed)
    assert (await store.get(record.id)).confidence < 0.8

    await store.touch_many([record.id], owner_id=ALICE, at=T0 + timedelta(days=90))

    stored = await store.get(record.id)
    assert stored.confidence == pytest.approx(0.8)
    assert stored.base_confidence == pytest.approx(0.8)


async def test_touch_never_lowers_confidence_without_a_base(store) -> None:
    record = _record(confidence=0.65)
    await store.put(record)
    await store.touch_many([record.id], owner_id=ALICE, at=T0 + timedelta(days=1))
    assert (await store.get(record.id)).confidence == pytest.approx(0.65)


async def test_a_year_of_weekly_access_and_nightly_decay_stays_alive(store) -> None:
    """The F11 scenario through the real SQL: decay persisted nightly, an access weekly."""
    record = _record(confidence=0.8)
    await store.put(record)
    now = T0
    for day in range(1, 366):
        now = T0 + timedelta(days=day)
        if day % 7 == 0:
            await store.touch_many([record.id], owner_id=ALICE, at=now)
        current = await store.get(record.id)
        await store.update(current.decayed(now=now))

    stored = await store.get(record.id)
    assert stored.confidence >= stored.policy.min_confidence
    assert stored.confidence >= 0.79
