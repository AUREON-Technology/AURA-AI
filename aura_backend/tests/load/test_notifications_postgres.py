"""The notification stores against a real Postgres (migration 0040).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. ``tests/unit/test_notifications.py``
proves the rules on the in-memory stores; this proves the SQL on a database built by ``alembic upgrade
head``: owner scoping, a dedupe key stored once even when two requests race for it, read-only-once
marking, the cap trimming read rows first, keyset paging over rows created at the same instant, JSONB
params round trip, and the table's CHECKs.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.notifications import InMemoryNotificationSettingsStore, InMemoryNotificationStore
from aura_core.adapters.postgres_notifications import PostgresNotificationSettingsStore, PostgresNotificationStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.identifiers import UserId
from aura_core.domain.notifications import Notification, NotificationSettings
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
ALICE, BOB = UserId("alice"), UserId("bob")


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "notifications") as url:
        yield url


@pytest.fixture
async def stores(database_url):
    pool = PostgresPool(database_url, min_size=8, max_size=8)
    yield PostgresNotificationStore(database_url, pool=pool), PostgresNotificationSettingsStore(database_url, pool=pool), pool
    await pool.close()


def _n(id_: str, owner: UserId = ALICE, kind: str = "run_failed", minutes: int = 0, **kw) -> Notification:
    return Notification(id=id_, owner_id=owner, kind=kind, created_at=T0 + timedelta(minutes=minutes), **kw)


async def test_round_trip_keeps_params_and_times(stores) -> None:
    pg, _, _ = stores
    row = _n("a", params={"run_id": "r1", "n": 3, "nested": {"a": [1, 2]}}, dedupe_key="run:r1")
    assert await pg.add(row) is True
    assert await pg.get(ALICE, "a") == row
    await pg.mark_read(ALICE, ["a"], at=T0 + timedelta(minutes=2))
    got = await pg.get(ALICE, "a")
    assert got.read_at == T0 + timedelta(minutes=2) and dict(got.params) == dict(row.params) and got.dedupe_key == "run:r1"


async def test_every_statement_is_scoped_to_the_owner(stores) -> None:
    pg, _, _ = stores
    await pg.add(_n("a"))
    assert await pg.get(BOB, "a") is None and await pg.list(BOB) == [] and await pg.unread_count(BOB) == 0
    assert await pg.mark_read(BOB, ["a"], at=T0) == 0 and await pg.mark_read(BOB, None, at=T0) == 0
    assert await pg.delete(BOB, "a") is False and await pg.delete_for_owner(BOB) == 0
    assert (await pg.get(ALICE, "a")).read_at is None
    assert await pg.delete_for_owner(ALICE) == 1


async def test_a_dedupe_key_is_stored_once_per_owner_even_under_a_race(stores) -> None:
    pg, _, _ = stores
    results = await asyncio.gather(*[pg.add(_n(f"racer-{i}", dedupe_key="run:1")) for i in range(12)])
    assert sum(results) == 1, results
    assert await pg.add(_n("bobs", owner=BOB, dedupe_key="run:1")) is True
    assert await pg.add(_n("u1")) is True and await pg.add(_n("u2")) is True, "no key: never deduped"


async def test_marking_read_changes_only_unread_rows_and_keeps_the_first_time(stores) -> None:
    pg, _, _ = stores
    for i in ("a", "b", "c"):
        await pg.add(_n(i))
    first, later = T0 + timedelta(minutes=5), T0 + timedelta(minutes=9)
    assert await pg.mark_read(ALICE, ["a", "nope"], at=first) == 1
    assert await pg.mark_read(ALICE, ["a"], at=later) == 0 and await pg.mark_read(ALICE, [], at=later) == 0
    assert await pg.unread_count(ALICE) == 2
    assert await pg.mark_read(ALICE, None, at=later) == 2 and await pg.unread_count(ALICE) == 0
    assert (await pg.get(ALICE, "a")).read_at == first


async def test_the_inbox_is_capped_dropping_read_rows_first_then_the_oldest(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    pg = PostgresNotificationStore(database_url, pool=pool, max_per_owner=4)
    try:
        for i in range(4):
            await pg.add(_n(f"n{i}", minutes=i))
        await pg.mark_read(ALICE, ["n2"], at=T0)
        await pg.add(_n("n4", minutes=4))
        assert {n.id for n in await pg.list(ALICE)} == {"n0", "n1", "n3", "n4"}
        await pg.add(_n("n5", minutes=5))
        assert {n.id for n in await pg.list(ALICE)} == {"n1", "n3", "n4", "n5"}
        await pg.add(_n("bob1", owner=BOB))
        assert len(await pg.list(ALICE)) == 4 and len(await pg.list(BOB)) == 1
    finally:
        await pool.close()


async def test_listing_orders_filters_and_pages_without_gaps(stores) -> None:
    pg, _, _ = stores
    for i in range(7):
        await pg.add(_n(f"n{i}", kind="renewal_due" if i % 2 else "run_failed", minutes=i))
    await pg.add(_n("tie-a", minutes=0))
    await pg.mark_read(ALICE, ["n6", "n5"], at=T0)

    assert [n.id for n in await pg.list(ALICE, limit=3)] == ["n6", "n5", "n4"]
    assert [n.id for n in await pg.list(ALICE, unread_only=True, limit=2)] == ["n4", "n3"]
    assert [n.id for n in await pg.list(ALICE, kind="renewal_due")] == ["n5", "n3", "n1"]
    seen, before = [], None
    while page := await pg.list(ALICE, limit=3, before=before):
        seen += [n.id for n in page]
        before = (page[-1].created_at, page[-1].id)
    assert seen == ["n6", "n5", "n4", "n3", "n2", "n1", "tie-a", "n0"]


async def test_settings_round_trip_replace_and_scope(stores) -> None:
    _, settings, _ = stores
    assert await settings.get(ALICE) is None
    await settings.put(ALICE, NotificationSettings(disabled_kinds=frozenset({"run_failed", "renewal_due"})), at=T0)
    await settings.put(BOB, NotificationSettings(disabled_kinds=frozenset({"payment_received"})), at=T0)
    got = await settings.get(ALICE)
    assert got.disabled_kinds == {"run_failed", "renewal_due"} and got.updated_at == T0
    await settings.put(ALICE, NotificationSettings(), at=T0 + timedelta(minutes=1))
    assert (await settings.get(ALICE)).disabled_kinds == frozenset()
    assert (await settings.get(BOB)).disabled_kinds == {"payment_received"}
    assert await settings.delete(ALICE) is True and await settings.delete(ALICE) is False and await settings.get(ALICE) is None


async def test_the_tables_own_checks_are_the_last_guard(stores) -> None:
    import asyncpg

    _, _, pool = stores
    raw = await pool._get_pool()

    async def insert(**columns):
        values = dict(id="x", owner_id="alice", kind="run_failed", params="{}", created_at=T0, read_at=None, dedupe_key=None)
        values.update(columns)
        async with raw.acquire() as conn:
            await conn.execute(
                "INSERT INTO aura_notifications (id, owner_id, kind, params, created_at, read_at, dedupe_key) "
                "VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7)",
                *[values[k] for k in ("id", "owner_id", "kind", "params", "created_at", "read_at", "dedupe_key")],
            )

    await insert(id="fine")
    for index, bad in enumerate(({"id": ""}, {"owner_id": ""}, {"kind": ""}, {"kind": "k" * 65}, {"dedupe_key": ""}, {"dedupe_key": "d" * 201})):
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(**{"id": f"bad-{index}", **bad} if "id" not in bad else bad)


async def test_the_two_stores_agree_on_the_rules(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    real, memory = PostgresNotificationStore(database_url, pool=pool), InMemoryNotificationStore()
    try:
        async def drive(store) -> list:
            out = []
            for i in range(5):
                out.append(await store.add(_n(f"n{i}", minutes=i, dedupe_key="dup" if i in (1, 3) else None)))
            out.append(await store.mark_read(ALICE, ["n0", "n4", "zzz"], at=T0 + timedelta(hours=1)))
            out.append([n.id for n in await store.list(ALICE)])
            out.append([n.id for n in await store.list(ALICE, unread_only=True)])
            out += [await store.unread_count(ALICE), await store.delete(ALICE, "n0"), await store.delete(ALICE, "n0"),
                    await store.delete_for_owner(ALICE), await store.unread_count(ALICE)]
            return out

        assert await drive(real) == await drive(memory)
    finally:
        await pool.close()
