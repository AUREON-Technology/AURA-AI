"""SaaS P1-D2: store purchase bindings and the replay log, against every adapter."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

import pytest

from aura_core.adapters.billing import InMemoryStoreBindingStore, InMemoryStoreEventLog
from aura_core.domain.billing import StoreBinding, load_store_products
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.plans import DEFAULT_CATALOGUE
from aura_core.domain.tenancy import Membership, Workspace, WorkspaceKind, WorkspaceRole, new_workspace_id

T0 = datetime(2026, 11, 17, tzinfo=timezone.utc)
_PG_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


class _Memory:
    async def __aenter__(self):
        self.workspace = new_workspace_id()
        return InMemoryStoreBindingStore(), InMemoryStoreEventLog()

    async def __aexit__(self, *exc):
        return None


class _Postgres:
    async def __aenter__(self):
        from aura_core.adapters.postgres_billing import PostgresStoreBindingStore, PostgresStoreEventLog
        from aura_core.adapters.postgres_store import PostgresPool
        from aura_core.adapters.postgres_tenancy import PostgresTenancyStore
        from tests.postgres_migrations import migrated_database

        if not _PG_URL:
            pytest.skip("AURA_TEST_DATABASE_URL is not set")
        self._db = migrated_database(_PG_URL, "stores")
        url = await self._db.__aenter__()
        self._pool = PostgresPool(url, min_size=1, max_size=8)
        ws = Workspace(new_workspace_id(), WorkspaceKind.PERSONAL, "P", "u1", T0)
        await PostgresTenancyStore(url, pool=self._pool).create_workspace(ws, Membership(ws.id, "u1", WorkspaceRole.OWNER, T0))
        self.workspace = ws.id
        return PostgresStoreBindingStore(url, pool=self._pool), PostgresStoreEventLog(url, pool=self._pool)

    async def __aexit__(self, *exc):
        await self._pool.close()
        await self._db.__aexit__(*exc)


@pytest.fixture(params=[pytest.param(_Memory, id="memory"), pytest.param(_Postgres, id="postgres", marks=pytest.mark.load)])
async def stores(request):
    backend = request.param()
    async with backend as (bindings, events):
        yield bindings, events, backend.workspace


async def test_binding_roundtrip(stores) -> None:
    bindings, _events, ws = stores
    token = "0d6f9a0e-2f43-4c8a-9a7e-1b2c3d4e5f60"
    assert await bindings.get(token) is None
    await bindings.put(StoreBinding(token, ws, "u1", T0))
    got = await bindings.get(token)
    assert (got.workspace_id, got.created_by) == (ws, "u1")


async def test_event_recorded_once_even_concurrently(stores) -> None:
    _b, events, _ws = stores
    assert await events.seen("apple", "evt-1") is False
    results = await asyncio.gather(*(events.record("apple", "evt-1", T0) for _ in range(10)))
    assert results.count(True) == 1
    assert await events.seen("apple", "evt-1") is True and await events.seen("google", "evt-1") is False
    assert await events.record("google", "evt-1", T0) is True   # per provider


def test_store_products_are_personal_plans_only() -> None:
    products = load_store_products(json.dumps({"apple": {"com.aureon.pro.monthly": "pro"}, "google": {"pro_monthly": "pro"}}), DEFAULT_CATALOGUE)
    assert products["apple"]["com.aureon.pro.monthly"] == "pro"
    assert load_store_products("", DEFAULT_CATALOGUE) == {"apple": {}, "google": {}}
    for bad in ('{"apple": {"x": "team"}}', '{"apple": {"x": "gold"}}', '{"amazon": {}}', "[]", "{bad"):
        with pytest.raises(ConfigurationError):
            load_store_products(bad, DEFAULT_CATALOGUE)
