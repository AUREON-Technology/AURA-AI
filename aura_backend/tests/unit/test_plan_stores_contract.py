"""SaaS P1-C: the EntitlementStore and UsageMeter contract, run against every adapter."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from aura_core.adapters.plans import InMemoryEntitlementStore, InMemoryUsageMeter
from aura_core.domain.plans import Entitlement
from aura_core.domain.tenancy import Membership, Workspace, WorkspaceKind, WorkspaceRole, new_workspace_id

T0 = datetime(2026, 11, 17, tzinfo=timezone.utc)
_PG_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


class _Postgres:
    """A migrated throwaway database with two workspaces the counters can reference."""

    async def __aenter__(self):
        import asyncpg

        from aura_core.adapters.postgres_plans import PostgresEntitlementStore, PostgresUsageMeter
        from aura_core.adapters.postgres_store import PostgresPool
        from aura_core.adapters.postgres_tenancy import PostgresTenancyStore
        from tests.postgres_migrations import migrated_database

        if not _PG_URL:
            pytest.skip("AURA_TEST_DATABASE_URL is not set")
        self._db = migrated_database(_PG_URL, "plans")
        self.url = await self._db.__aenter__()
        self._pool = PostgresPool(self.url, min_size=1, max_size=8)
        tenancy = PostgresTenancyStore(self.url, pool=self._pool)
        self.workspaces = []
        for owner in ("u1", "u2"):
            ws = Workspace(new_workspace_id(), WorkspaceKind.TEAM, "T", owner, T0)
            await tenancy.create_workspace(ws, Membership(ws.id, owner, WorkspaceRole.OWNER, T0))
            self.workspaces.append(ws.id)
        return PostgresEntitlementStore(self.url, pool=self._pool), PostgresUsageMeter(self.url, pool=self._pool)

    async def __aexit__(self, *exc):
        await self._pool.close()
        await self._db.__aexit__(*exc)


class _Memory:
    async def __aenter__(self):
        self.workspaces = [new_workspace_id(), new_workspace_id()]
        return InMemoryEntitlementStore(), InMemoryUsageMeter()

    async def __aexit__(self, *exc):
        return None


@pytest.fixture(params=[pytest.param(_Memory, id="memory"), pytest.param(_Postgres, id="postgres", marks=pytest.mark.load)])
async def stores(request):
    backend = request.param()
    async with backend as (entitlements, meter):
        yield entitlements, meter, backend.workspaces


async def test_entitlement_upsert(stores) -> None:
    entitlements, _meter, (ws, _) = stores
    assert await entitlements.get(ws) is None
    await entitlements.put(Entitlement(ws, "team", 3, "manual", T0, "sa@example.com"))
    await entitlements.put(Entitlement(ws, "team", 5, "subscription", T0, "billing"))
    got = await entitlements.get(ws)
    assert (got.plan_id, got.seats, got.source) == ("team", 5, "subscription")


async def test_try_consume_admits_until_limit(stores) -> None:
    _e, meter, (ws, _) = stores
    assert await meter.try_consume(ws, "messages", "2026-11", 1, 2) == (True, 1)
    assert await meter.try_consume(ws, "messages", "2026-11", 1, 2) == (True, 2)
    assert await meter.try_consume(ws, "messages", "2026-11", 1, 2) == (False, 2)
    assert await meter.get(ws, "messages", "2026-11") == 2
    assert await meter.try_consume(ws, "messages", "2026-12", 5, 2) == (False, 0)


async def test_try_consume_concurrent_last_unit(stores) -> None:
    _e, meter, (ws, _) = stores
    results = await asyncio.gather(*(meter.try_consume(ws, "messages", "2026-11", 1, 10) for _ in range(20)))
    assert sum(1 for admitted, _ in results if admitted) == 10
    assert await meter.get(ws, "messages", "2026-11") == 10


async def test_release_never_negative(stores) -> None:
    _e, meter, (ws, _) = stores
    await meter.try_consume(ws, "messages", "2026-11", 1, 5)
    assert await meter.release(ws, "messages", "2026-11", 3) == 0
    assert await meter.release(ws, "messages", "2026-11", 1) == 0


async def test_add_and_floor(stores) -> None:
    _e, meter, (ws, _) = stores
    assert await meter.add(ws, "storage_bytes", "all", 1000) == 1000
    assert await meter.add(ws, "storage_bytes", "all", -400) == 600
    assert await meter.add(ws, "storage_bytes", "all", -5000) == 0


async def test_workspaces_and_periods_are_independent(stores) -> None:
    _e, meter, (ws1, ws2) = stores
    await meter.try_consume(ws1, "messages", "2026-11", 2, 5)
    assert await meter.get(ws2, "messages", "2026-11") == 0
    assert await meter.get(ws1, "messages", "2026-10") == 0


@pytest.mark.load
async def test_workspace_delete_cascades() -> None:
    import asyncpg

    backend = _Postgres()
    async with backend as (entitlements, meter):
        ws = backend.workspaces[0]
        await entitlements.put(Entitlement(ws, "team", 2, "manual", T0, "sa"))
        await meter.try_consume(ws, "messages", "2026-11", 1, 5)
        conn = await asyncpg.connect(backend.url)
        try:
            await conn.execute("DELETE FROM aura_workspaces WHERE id = $1", ws)
        finally:
            await conn.close()
        assert await entitlements.get(ws) is None
        assert await meter.get(ws, "messages", "2026-11") == 0
