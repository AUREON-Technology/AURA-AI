"""INT-004 against a real Postgres: migration 0021 and ``PostgresPromptPinStore``.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN. The database
is built by ``alembic upgrade head`` -- the adapter creates nothing.

What the in-memory store cannot show and this does: two registries on two store instances (two
gateway workers, each with its own pool) see each other's pin and unpin through the table; a
re-pin upserts rather than colliding on the primary key; and the table refuses a non-semantic
version even if a caller skipped the registry's validation.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from aura_core.adapters.postgres_prompt_pins import PostgresPromptPinStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.prompts import build_default_registry
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
TEMPLATE = "system.orchestrator"


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "prompt_pins") as url:
        yield url


def _registry(store):
    registry = build_default_registry(pins=store)
    current = registry.get(TEMPLATE)
    registry.register(replace(current, version="0.9.0", body=current.body + "\n(previous)"))
    return registry


async def test_pins_cross_workers_through_the_table(database_url) -> None:
    pool_a = PostgresPool(database_url, min_size=1, max_size=2)
    pool_b = PostgresPool(database_url, min_size=1, max_size=2)
    try:
        a = _registry(PostgresPromptPinStore(database_url, pool=pool_a))
        b = _registry(PostgresPromptPinStore(database_url, pool=pool_b))
        latest = b.get(TEMPLATE).version

        await a.set_pin(TEMPLATE, "0.9.0", pinned_by="ops")
        await a.set_pin(TEMPLATE, "0.9.0", pinned_by="ops-again")  # upsert, not a PK conflict
        await b.refresh_pins()
        assert b.get(TEMPLATE).version == "0.9.0"

        assert await b.clear_pin(TEMPLATE) is True
        await a.refresh_pins()
        assert a.get(TEMPLATE).version == latest
        assert await b.clear_pin(TEMPLATE) is False
    finally:
        await pool_a.close()
        await pool_b.close()


async def test_the_table_refuses_a_non_semantic_version(database_url) -> None:
    import asyncpg

    pool = PostgresPool(database_url, min_size=1, max_size=1)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await PostgresPromptPinStore(database_url, pool=pool).set(TEMPLATE, "latest")
    finally:
        await pool.close()
