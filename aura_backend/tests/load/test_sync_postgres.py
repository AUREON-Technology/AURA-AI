"""`/api/v3/sync` against a real, alembic-migrated Postgres (audit F-07, F-11, F-12, F-13).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN, exactly like
``test_service_keys_postgres.py``. Each test gets its own database, migrated with
``alembic upgrade head`` -- not ``create_all`` -- so the column defaults the cursor depends on are
the migration's, not the model's. A throwaway cluster is enough::

    initdb -D /tmp/pg/data -U postgres --auth=trust
    pg_ctl -D /tmp/pg/data -o "-p 55437 -k /tmp/pg" -l /tmp/pg/log start
    AURA_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55437/postgres \\
        pytest tests/load/test_sync_postgres.py -m load -q

What only a real database shows, and why each test is here:

* **F-12** -- the cursor race needs two real transactions: a push still open while a pull runs.
  The old cursor (application clock, compared with ``updated_at`` = the writer's transaction
  *start*) skipped that row for good; the transaction-id cursor delivers it on the next pull.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
async def migrated_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"sync_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = urlunsplit(urlsplit(_URL)._replace(path=f"/{name}"))
    try:
        sqlalchemy_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        env = {**os.environ, "DATABASE_URL": sqlalchemy_url, "AURA_DATABASE_URL": sqlalchemy_url}
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=_BACKEND, env=env,
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stderr[-3000:]
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


class _Api:
    """The sync router alone, on its own engine, authenticated as whichever uid a call names."""

    def __init__(self, url: str, monkeypatch) -> None:
        from fastapi import FastAPI
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        import api.v2.sync as sync
        from infrastructure.db.base import get_db
        from services.jwt_auth import verify_access_token

        self.url = url
        self.uid = f"user_{uuid.uuid4().hex[:8]}"
        self.engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1))
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        monkeypatch.setattr(sync, "is_configured", lambda: True)

        async def _db():
            async with factory() as session:
                yield session

        app = FastAPI()
        app.include_router(sync.router)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[verify_access_token] = lambda: {
            "uid": self.uid, "email": f"{self.uid}@aura.test", "name": "", "picture": None, "role": "User",
        }
        self.app = app

    async def __aenter__(self):
        import httpx

        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.http.aclose()
        await self.engine.dispose()

    async def push(self, *mutations: dict):
        response = await self.http.post("/api/v3/sync", json={"mutations": list(mutations)})
        return response

    async def pull(self, since: str | None = None) -> dict:
        response = await self.http.get("/api/v3/sync", params={"since": since} if since else None)
        assert response.status_code == 200, response.text
        return response.json()

    async def user_id(self, conn) -> uuid.UUID:
        return await conn.fetchval("SELECT id FROM users WHERE auth_subject = $1", self.uid)


def _task(task_id: str, *, at: str = "2026-01-01T00:00:00Z", **payload) -> dict:
    return {
        "table": "tasks", "op": "upsert", "id": task_id, "client_updated_at": at,
        "payload": {"title_enc": "gcm:iv:ct", "priority": "medium", **payload},
    }


def _ids(body: dict, table: str) -> list[str]:
    return [row["id"] for row in body["data"][table]]


# -- F-12: the cursor ----------------------------------------------------------------------------


async def test_a_row_committed_during_a_pull_is_delivered_by_the_next_pull(migrated_url, monkeypatch) -> None:
    """The race the audit described: device A's push is open while device B pulls.

    Under the old cursor this row was stamped with the writer's transaction *start* time, which
    is earlier than the cursor B got back, so B never saw it.
    """
    import asyncpg

    async with _Api(migrated_url, monkeypatch) as api:
        first = await api.pull()
        conn = await asyncpg.connect(migrated_url)
        writer = await asyncpg.connect(migrated_url)
        try:
            user_id = await api.user_id(conn)
            task_id = uuid.uuid4()
            transaction = writer.transaction()
            await transaction.start()
            await writer.execute(
                "INSERT INTO tasks (id, user_id, title_enc, priority) VALUES ($1, $2, 'gcm:a:b', 'medium')",
                task_id, user_id,
            )
            during = await api.pull(first["cursor"])
            assert str(task_id) not in _ids(during, "tasks")  # not committed yet: invisible
            await transaction.commit()

            after = await api.pull(during["cursor"])
            assert str(task_id) in _ids(after, "tasks")
        finally:
            await writer.close()
            await conn.close()


async def test_an_update_moves_a_row_past_the_cursor(migrated_url, monkeypatch) -> None:
    """`sync_txid` is reassigned on UPDATE, not only on INSERT: a delete (an UPDATE setting
    `deleted_at`) must reach a device that already holds the row."""
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        assert (await api.push(_task(task_id))).json()[0]["status"] == "applied"
        cursor = (await api.pull())["cursor"]
        assert _ids(await api.pull(cursor), "tasks") == []  # nothing new since

        delete = {"table": "tasks", "op": "delete", "id": task_id, "client_updated_at": "2026-01-02T00:00:00Z"}
        assert (await api.push(delete)).json()[0]["status"] == "applied"
        rows = (await api.pull(cursor))["data"]["tasks"]
        assert [r["id"] for r in rows] == [task_id]
        assert rows[0]["deleted_at"] is not None


async def test_a_timestamp_cursor_from_an_older_client_gets_one_full_sync(migrated_url, monkeypatch) -> None:
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        await api.push(_task(task_id))
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        body = await api.pull(future)  # an old app-clock cursor, even one that is ahead
        assert _ids(body, "tasks") == [task_id]
        assert body["cursor"].startswith("txid:")


async def test_a_cursor_from_another_cluster_gets_a_full_sync(migrated_url, monkeypatch) -> None:
    """A cursor ahead of the database's own next transaction id can only come from a cluster
    whose counter was higher (data moved by dump and restore); resuming from it would skip
    every new write."""
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        await api.push(_task(task_id))
        assert _ids(await api.pull("txid:999999999999"), "tasks") == [task_id]


async def test_a_far_future_client_clock_is_clamped(migrated_url, monkeypatch) -> None:
    """A device whose clock runs a day fast must not lock its rows against every other device
    for a day."""
    import asyncpg

    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        ahead = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        assert (await api.push(_task(task_id, at=ahead))).json()[0]["status"] == "applied"
        conn = await asyncpg.connect(migrated_url)
        try:
            stored = await conn.fetchval("SELECT client_updated_at FROM tasks WHERE id = $1", uuid.UUID(task_id))
        finally:
            await conn.close()
        assert stored <= datetime.now(timezone.utc) + timedelta(minutes=5, seconds=5)
