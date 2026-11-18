"""SaaS P1-F1: SharedConversationStore, run against every adapter."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from aura_core.adapters.sharing import InMemorySharedConversationStore
from aura_core.domain.sharing import SharedConversation, SharedMessage
from aura_core.domain.tenancy import Membership, Workspace, WorkspaceKind, WorkspaceRole, new_workspace_id

T0 = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
_PG_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


class _Memory:
    async def __aenter__(self):
        self.workspace, self.tenancy = new_workspace_id(), None
        return InMemorySharedConversationStore()

    async def __aexit__(self, *exc):
        return None


class _Postgres:
    async def __aenter__(self):
        from aura_core.adapters.postgres_sharing import PostgresSharedConversationStore
        from aura_core.adapters.postgres_store import PostgresPool
        from aura_core.adapters.postgres_tenancy import PostgresTenancyStore
        from tests.postgres_migrations import migrated_database

        if not _PG_URL:
            pytest.skip("AURA_TEST_DATABASE_URL is not set")
        self._db = migrated_database(_PG_URL, "sharing")
        url = await self._db.__aenter__()
        self._pool = PostgresPool(url, min_size=1, max_size=2)
        self.tenancy = PostgresTenancyStore(url, pool=self._pool)
        ws = Workspace(new_workspace_id(), WorkspaceKind.TEAM, "T", "u1", T0)
        await self.tenancy.create_workspace(ws, Membership(ws.id, "u1", WorkspaceRole.OWNER, T0))
        self.workspace = ws.id
        return PostgresSharedConversationStore(url, pool=self._pool)

    async def __aexit__(self, *exc):
        await self._pool.close()
        await self._db.__aexit__(*exc)


@pytest.fixture(params=[pytest.param(_Memory, id="memory"), pytest.param(_Postgres, id="postgres", marks=pytest.mark.load)])
async def env(request):
    backend = request.param()
    async with backend as store:
        yield store, backend


def _conv(ws, cid="c1", source="s1", by="u1", title="Plan", text="hi") -> SharedConversation:
    return SharedConversation(id=cid, workspace_id=ws, shared_by=by, source_id=source, title=title,
                              messages=(SharedMessage("user", text), SharedMessage("assistant", "ok")), created_at=T0)


async def test_put_get_list_delete(env) -> None:
    store, backend = env
    ws = backend.workspace
    stored = await store.put(_conv(ws))
    got = await store.get(ws, stored.id)
    assert got.title == "Plan" and [m.role for m in got.messages] == ["user", "assistant"]
    assert [c.id for c in await store.list(ws, 10)] == [stored.id]
    assert await store.get("0" * 32, stored.id) is None  # another workspace sees nothing
    assert await store.delete(ws, stored.id) is True and await store.delete(ws, stored.id) is False


async def test_resharing_the_same_source_replaces(env) -> None:
    store, backend = env
    ws = backend.workspace
    first = await store.put(_conv(ws, cid="c1", text="v1"))
    second = await store.put(_conv(ws, cid="c2", text="v2"))
    assert second.id == first.id
    [only] = await store.list(ws, 10)
    assert (await store.get(ws, only.id)).messages[0].text == "v2"
    other = await store.put(_conv(ws, cid="c3", by="u2"))
    assert other.id != first.id and len(await store.list(ws, 10)) == 2


async def test_shares_go_with_the_workspace(env) -> None:
    store, backend = env
    if backend.tenancy is None:
        pytest.skip("cascade is a database property")
    stored = await store.put(_conv(backend.workspace))
    await backend.tenancy.delete_workspace(backend.workspace)
    assert await store.get(backend.workspace, stored.id) is None
