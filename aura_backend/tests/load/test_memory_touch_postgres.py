"""Field-level memory reinforcement against a real Postgres (audit F-16 / AR-06).

Opt-in like everything under ``tests/load``: skipped unless ``AURA_TEST_DATABASE_URL`` names a
database, and only Postgres is needed. Each test runs in a fresh schema that is dropped
afterwards. Example with a throwaway container::

    docker run -d --rm -p 55432:5432 -e POSTGRES_PASSWORD=aura postgres:16-alpine
    AURA_TEST_DATABASE_URL=postgresql://postgres:aura@127.0.0.1:55432/postgres \\
        pytest tests/load/test_memory_touch_postgres.py -m load -q

What the in-memory tests cannot show: that the ``UPDATE`` really is one statement that waits on
a concurrent supersede's row lock and then re-checks ``superseded_by IS NULL`` against the
committed row, asyncpg's parameter typing for the nullable confidence floor, and that
concurrent touches do not lose increments under real connection-level concurrency.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.field_crypto import FieldCipher
from aura_core.adapters.postgres_store import PostgresMemoryStore, PostgresPool
from aura_core.domain.identifiers import MemoryId, UserId
from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


def _with_search_path(url: str, schema: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema
    return urlunsplit(parts._replace(query=urlencode(query)))


@pytest.fixture
async def schema_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    schema = f"touch_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield _with_search_path(_URL, schema)
    finally:
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.fixture
async def store(schema_url):
    pool = PostgresPool(schema_url, min_size=1, max_size=12)
    store = PostgresMemoryStore(
        schema_url, pool=pool, cipher=FieldCipher([Fernet.generate_key().decode()])
    )
    yield store
    await pool.close()


def _memory(mid: str, *, confidence: float = 0.6, at: datetime | None = None) -> MemoryRecord:
    moment = at or datetime(2026, 10, 1, tzinfo=timezone.utc)
    return MemoryRecord(
        id=MemoryId(mid), kind=MemoryKind.SEMANTIC, owner_id=UserId("alice"),
        content=f"fact {mid}", provenance=Provenance(source="test"), subject="s",
        confidence=confidence, created_at=moment, last_accessed_at=moment,
        metadata={"k": "v"},
    )


async def test_touch_changes_only_the_reinforcement_fields(store) -> None:
    record = _memory("m1")
    await store.put(record)
    later = record.last_accessed_at + timedelta(hours=2)

    assert await store.touch_many([record.id, record.id, MemoryId("nope")], now=later) == 1
    stored = await store.get(record.id)
    assert stored.access_count == 1
    assert stored.last_accessed_at == later
    assert stored.confidence == pytest.approx(0.6)  # no floor given: unchanged
    assert (stored.content, stored.subject, dict(stored.metadata)) == ("fact m1", "s", {"k": "v"})

    # Never backwards; the floor raises but never lowers, and is capped at 1.
    await store.touch_many([record.id], now=later - timedelta(days=1), confidence_floor=0.9)
    stored = await store.get(record.id)
    assert stored.last_accessed_at == later
    assert stored.confidence == pytest.approx(0.9)
    await store.touch_many([record.id], now=later, confidence_floor=0.2)
    assert (await store.get(record.id)).confidence == pytest.approx(0.9)
    await store.touch_many([record.id], now=later, confidence_floor=3.0)
    assert (await store.get(record.id)).confidence == pytest.approx(1.0)


async def test_a_stale_snapshot_touch_does_not_undo_a_supersede(store) -> None:
    record = _memory("m1")
    await store.put(record)
    snapshot = await store.get(record.id)  # what a recall would have read

    await store.update(snapshot.superseded(MemoryId("m2")))
    assert await store.touch_many([snapshot.id], now=datetime.now(timezone.utc)) == 0

    stored = await store.get(record.id)
    assert stored.superseded_by == MemoryId("m2")
    assert stored.access_count == 0


async def test_a_touch_blocked_on_an_uncommitted_supersede_rechecks_after_it_commits(
    store, schema_url
) -> None:
    """The real interleaving: the supersede holds the row lock when the touch arrives."""
    import asyncpg

    record = _memory("m1")
    await store.put(record)

    conn = await asyncpg.connect(schema_url)
    try:
        tx = conn.transaction()
        await tx.start()
        await conn.execute("UPDATE aura_memories SET superseded_by = 'm2' WHERE id = 'm1'")
        touch = asyncio.create_task(store.touch_many([record.id], now=datetime.now(timezone.utc)))
        await asyncio.sleep(0.3)
        assert not touch.done()  # waiting on the row lock, not racing past it
        await tx.commit()
        assert await asyncio.wait_for(touch, 5) == 0
    finally:
        await conn.close()

    stored = await store.get(record.id)
    assert stored.superseded_by == MemoryId("m2")
    assert stored.access_count == 0


async def test_concurrent_touches_do_not_lose_increments(store) -> None:
    ids = [MemoryId(f"m{i}") for i in range(3)]
    for mid in ids:
        await store.put(_memory(mid.value))

    now = datetime.now(timezone.utc)
    results = await asyncio.gather(*(store.touch_many(ids, now=now) for _ in range(25)))

    assert results == [3] * 25
    for mid in ids:
        assert (await store.get(mid)).access_count == 25
