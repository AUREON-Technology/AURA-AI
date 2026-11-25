"""The Postgres ``CloudSandboxStore`` on a real server, built by ``alembic upgrade head``.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a server where the role may
create databases.
"""

from __future__ import annotations

import os

import pytest

from aura_core.adapters.postgres_cloud_sandboxes import PostgresCloudSandboxStore
from aura_core.adapters.postgres_workspaces import PostgresWorkspaceStore
from aura_core.domain.engineering import AgentWorkspace, WorkspaceStatus
from aura_core.domain.identifiers import UserId, WorkspaceId

from tests.cloud_sandbox_store_cases import NOW, CloudSandboxStoreCases
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


@pytest.fixture
async def database():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "cloudsandbox") as url:
        yield url


class TestPostgresCloudSandboxStore(CloudSandboxStoreCases):
    @pytest.fixture
    async def store(self, database):
        store = PostgresCloudSandboxStore(database)
        yield store
        await store.close()


async def test_a_workspace_row_remembers_its_provider(database) -> None:
    store = PostgresWorkspaceStore(database)
    row = AgentWorkspace(
        id=WorkspaceId("w1"), user_id=UserId("alice"), repo="acme/widgets", workspace_path="w1",
        status=WorkspaceStatus.HOT, created_at=NOW, last_active_at=NOW, provider="railway",
    )
    try:
        await store.upsert(row)
        assert (await store.get_by_id(row.id)).provider == "railway"
    finally:
        await store._pool.close()
