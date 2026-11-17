"""SaaS P1-F1: where billing email goes -- BillingContactStore, run against every adapter."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from aura_core.adapters.billing import InMemoryBillingContactStore
from aura_core.domain.tenancy import Membership, Workspace, WorkspaceKind, WorkspaceRole, new_workspace_id

T0 = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
_PG_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


class _Memory:
    async def __aenter__(self):
        self.workspace, self.tenancy = new_workspace_id(), None
        return InMemoryBillingContactStore()

    async def __aexit__(self, *exc):
        return None


class _Postgres:
    async def __aenter__(self):
        from aura_core.adapters.postgres_billing import PostgresBillingContactStore
        from aura_core.adapters.postgres_store import PostgresPool
        from aura_core.adapters.postgres_tenancy import PostgresTenancyStore
        from tests.postgres_migrations import migrated_database

        if not _PG_URL:
            pytest.skip("AURA_TEST_DATABASE_URL is not set")
        self._db = migrated_database(_PG_URL, "contacts")
        url = await self._db.__aenter__()
        self._pool = PostgresPool(url, min_size=1, max_size=2)
        self.tenancy = PostgresTenancyStore(url, pool=self._pool)
        ws = Workspace(new_workspace_id(), WorkspaceKind.TEAM, "T", "u1", T0)
        await self.tenancy.create_workspace(ws, Membership(ws.id, "u1", WorkspaceRole.OWNER, T0))
        self.workspace = ws.id
        return PostgresBillingContactStore(url, pool=self._pool)

    async def __aexit__(self, *exc):
        await self._pool.close()
        await self._db.__aexit__(*exc)


@pytest.fixture(params=[pytest.param(_Memory, id="memory"), pytest.param(_Postgres, id="postgres", marks=pytest.mark.load)])
async def env(request):
    backend = request.param()
    async with backend as contacts:
        yield contacts, backend


async def test_contact_roundtrip_and_replace(env) -> None:
    contacts, backend = env
    assert await contacts.get(backend.workspace) is None
    await contacts.put(backend.workspace, "own@example.com", T0)
    await contacts.put(backend.workspace, "new@example.com", T0, set_by="u2")
    assert await contacts.get(backend.workspace) == ("new@example.com", "u2")


async def test_contact_goes_with_the_workspace(env) -> None:
    contacts, backend = env
    if backend.tenancy is None:
        pytest.skip("cascade is a database property")
    await contacts.put(backend.workspace, "own@example.com", T0)
    await backend.tenancy.delete_workspace(backend.workspace)
    assert await contacts.get(backend.workspace) is None
