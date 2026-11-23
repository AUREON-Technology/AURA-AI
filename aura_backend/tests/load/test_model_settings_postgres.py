"""``PostgresModelSettingsStore`` against a real Postgres (migration 0038).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. ``tests/unit/test_model_management.py``
proves the rules on the in-memory store; this proves the SQL on a database built by
``alembic upgrade head``: a put replaces the whole row, owner scoping, NULL meaning "no preference",
and the table's own CHECKs, the last guard if the API's ranges were ever bypassed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from aura_core.adapters.model_settings import InMemoryModelSettingsStore
from aura_core.adapters.postgres_model_settings import PostgresModelSettingsStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.identifiers import UserId
from aura_core.domain.model_settings import ModelSettings
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
ALICE, BOB = UserId("alice"), UserId("bob")


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "model_settings") as url:
        yield url


@pytest.fixture
async def store(database_url):
    pool = PostgresPool(database_url, min_size=1, max_size=4)
    yield PostgresModelSettingsStore(database_url, pool=pool), pool
    await pool.close()


async def test_round_trip_keeps_every_field_including_false_and_zero(store) -> None:
    pg, _ = store
    full = await pg.put(ALICE, ModelSettings(routing_policy="quality_first", require_local=False, temperature=0.0, max_output_tokens=16), at=NOW)
    got = await pg.get(ALICE)
    assert got == full
    assert (got.routing_policy, got.require_local, got.temperature, got.max_output_tokens, got.updated_at) == (
        "quality_first", False, 0.0, 16, NOW,
    )
    assert got.require_local is False and got.temperature == 0.0, "False and 0.0 are values, not NULL"


async def test_a_put_replaces_the_whole_row_and_nulls_mean_no_preference(store) -> None:
    pg, _ = store
    await pg.put(ALICE, ModelSettings(routing_policy="cheapest", require_local=True, temperature=1.5, max_output_tokens=4000), at=NOW)
    later = datetime(2026, 11, 24, tzinfo=timezone.utc)
    await pg.put(ALICE, ModelSettings(max_output_tokens=300), at=later)

    got = await pg.get(ALICE)
    assert (got.routing_policy, got.require_local, got.temperature, got.max_output_tokens) == (None, None, None, 300)
    assert got.updated_at == later


async def test_every_statement_is_scoped_to_the_owner(store) -> None:
    pg, _ = store
    await pg.put(ALICE, ModelSettings(temperature=1.0), at=NOW)
    await pg.put(BOB, ModelSettings(temperature=0.5), at=NOW)

    assert await pg.delete(UserId("carol")) is False
    assert (await pg.get(ALICE)).temperature == 1.0 and (await pg.get(BOB)).temperature == 0.5
    assert await pg.delete(ALICE) is True and await pg.get(ALICE) is None
    assert (await pg.get(BOB)).temperature == 0.5, "Alice's delete removed Bob's row"
    assert await pg.delete(ALICE) is False


async def test_the_tables_own_checks_are_the_last_guard(store) -> None:
    import asyncpg

    _, pool = store
    raw = await pool._get_pool()

    async def insert(owner: str, **columns):
        values = dict(routing_policy=None, require_local=None, temperature=None, max_output_tokens=None, updated_at=NOW)
        values.update(columns)
        async with raw.acquire() as conn:
            await conn.execute(
                "INSERT INTO aura_user_model_settings (owner_id, routing_policy, require_local, temperature, "
                "max_output_tokens, updated_at) VALUES ($1,$2,$3,$4,$5,$6)",
                owner, values["routing_policy"], values["require_local"], values["temperature"],
                values["max_output_tokens"], values["updated_at"],
            )

    await insert("fine", routing_policy="local_first", temperature=2.0, max_output_tokens=8192)
    for index, bad in enumerate((
        {"routing_policy": "fastest-ish"}, {"routing_policy": ""}, {"temperature": 2.01}, {"temperature": -0.01},
        {"max_output_tokens": 15}, {"max_output_tokens": 8193},
    )):
        with pytest.raises(asyncpg.CheckViolationError):
            await insert(f"bad-{index}", **bad)
    with pytest.raises(asyncpg.CheckViolationError):
        await insert("")


async def test_the_two_stores_agree_on_the_rules(database_url) -> None:
    pool = PostgresPool(database_url, min_size=1, max_size=2)
    real, memory = PostgresModelSettingsStore(database_url, pool=pool), InMemoryModelSettingsStore()
    try:
        async def drive(store) -> list:
            out = [await store.get(ALICE)]
            out.append(await store.put(ALICE, ModelSettings(routing_policy="fastest", require_local=True, temperature=0.7), at=NOW))
            out.append(await store.put(ALICE, ModelSettings(max_output_tokens=500), at=NOW))
            out += [await store.get(ALICE), await store.get(BOB), await store.delete(BOB), await store.delete(ALICE), await store.get(ALICE)]
            return out

        assert await drive(real) == await drive(memory)
    finally:
        await pool.close()
