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

* **F-11** -- constraint violations only surface at flush, inside Postgres; the per-row SAVEPOINT
  is what keeps one from aborting the batch.
* **F-12** -- the cursor race needs two real transactions: a push still open while a pull runs.
  The old cursor (application clock, compared with ``updated_at`` = the writer's transaction
  *start*) skipped that row for good; the transaction-id cursor delivers it on the next pull.
* **F-13** -- `fact_enc NOT NULL` is enforced by Postgres, not by the model; only a real insert
  shows the client's memory payload is accepted.
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


# -- F-11: per-mutation isolation ----------------------------------------------------------------


async def test_one_bad_row_does_not_fail_the_batch(migrated_url, monkeypatch) -> None:
    """[valid, unparseable date, valid] -> [applied, error, applied], and both valid rows land.

    Before, the `ValueError` escaped the loop: HTTP 500 for the batch, nothing committed, and the
    same 500 on every later sync of that device."""
    async with _Api(migrated_url, monkeypatch) as api:
        good_a, bad, good_b = (str(uuid.uuid4()) for _ in range(3))
        response = await api.push(_task(good_a), _task(bad, due_date="not-a-date"), _task(good_b))
        assert response.status_code == 200, response.text
        results = response.json()
        assert [r["status"] for r in results] == ["applied", "error", "applied"]
        assert results[1]["id"] == bad and results[1]["retryable"] is False
        assert sorted(_ids(await api.pull(), "tasks")) == sorted([good_a, good_b])


async def test_a_constraint_violation_fails_only_its_own_row(migrated_url, monkeypatch) -> None:
    """A goal naming a project that does not exist violates the foreign key at flush. It is
    reported as a retryable error -- the project may simply not have arrived yet."""
    async with _Api(migrated_url, monkeypatch) as api:
        before, goal, after = (str(uuid.uuid4()) for _ in range(3))
        orphan = {
            "table": "goals", "op": "upsert", "id": goal, "client_updated_at": "2026-01-01T00:00:00Z",
            "payload": {"title_enc": "gcm:a:b", "project_id": str(uuid.uuid4())},
        }
        response = await api.push(_task(before), orphan, _task(after))
        assert response.status_code == 200, response.text
        assert [r["status"] for r in response.json()] == ["applied", "error", "applied"]
        assert response.json()[1]["retryable"] is True
        assert sorted(_ids(await api.pull(), "tasks")) == sorted([before, after])


async def test_a_malformed_envelope_fails_only_its_own_row(migrated_url, monkeypatch) -> None:
    """Before, the request model validated every mutation up front, so one bad
    `client_updated_at` was a 422 for the batch."""
    async with _Api(migrated_url, monkeypatch) as api:
        good, bad = str(uuid.uuid4()), str(uuid.uuid4())
        response = await api.push(_task(good), {**_task(bad), "client_updated_at": "yesterday"})
        assert response.status_code == 200, response.text
        assert [r["status"] for r in response.json()] == ["applied", "error"]
        assert _ids(await api.pull(), "tasks") == [good]


def _health(record_id: str, *, at: str, water: int) -> dict:
    return {
        "table": "health_records", "op": "upsert", "id": record_id, "client_updated_at": at,
        "payload": {"record_date": "2026-10-26T21:00:00Z", "water_ml": water},
    }


async def test_two_devices_logging_the_same_day_merge_instead_of_failing(migrated_url, monkeypatch) -> None:
    """Each device mints its own id for "today"; the second insert hit
    `uq_health_records_user_date` and 500'd the batch forever. Now it merges onto the existing
    row under last-write-wins and tells the client which id the server kept."""
    async with _Api(migrated_url, monkeypatch) as api:
        phone, laptop = str(uuid.uuid4()), str(uuid.uuid4())
        assert (await api.push(_health(phone, at="2026-10-27T08:00:00Z", water=500))).json()[0]["status"] == "applied"

        response = await api.push(_health(laptop, at="2026-10-27T09:00:00Z", water=300), _task(str(uuid.uuid4())))
        assert response.status_code == 200, response.text
        merged, task = response.json()
        assert merged["status"] == "applied" and task["status"] == "applied"
        assert merged["server_id"] == phone
        assert merged["row"]["id"] == phone and merged["row"]["water_ml"] == 300

        rows = (await api.pull())["data"]["health_records"]
        assert [(r["id"], r["water_ml"]) for r in rows] == [(phone, 300)]

        stale = await api.push(_health(str(uuid.uuid4()), at="2026-10-27T07:00:00Z", water=1))
        assert stale.json()[0]["status"] == "conflict"
        assert stale.json()[0]["server_id"] == phone and stale.json()[0]["row"]["water_ml"] == 300


async def test_a_conflict_carries_the_server_row(migrated_url, monkeypatch) -> None:
    """So the client can converge without depending on a cursor the unchanged row is behind."""
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        await api.push(_task(task_id, at="2026-01-02T00:00:00Z", priority="high"))
        conflict = (await api.push(_task(task_id, at="2026-01-01T00:00:00Z", priority="low"))).json()[0]
        assert conflict["status"] == "conflict"
        assert conflict["row"]["id"] == task_id and conflict["row"]["priority"] == "high"


async def test_the_same_row_twice_in_one_batch(migrated_url, monkeypatch) -> None:
    """The second mutation reads a row the first one flushed in this same request; its
    server-generated columns must be reloaded, not lazily (and illegally) fetched under asyncio."""
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        response = await api.push(
            _task(task_id, at="2026-01-01T00:00:00Z"),
            _task(task_id, at="2026-01-02T00:00:00Z", priority="high"),
            _task(task_id, at="2026-01-01T12:00:00Z", priority="low"),
        )
        assert response.status_code == 200, response.text
        assert [r["status"] for r in response.json()] == ["applied", "applied", "conflict"]
        assert (await api.pull())["data"]["tasks"][0]["priority"] == "high"


# -- F-13: the memory payload --------------------------------------------------------------------


async def test_a_memory_as_the_client_now_sends_it_is_applied(migrated_url, monkeypatch) -> None:
    """`SyncPayloads.userMemory` under a UUID id: `{"fact_enc": ...}`."""
    async with _Api(migrated_url, monkeypatch) as api:
        memory_id = str(uuid.uuid4())
        mutation = {
            "table": "memories", "op": "upsert", "id": memory_id, "client_updated_at": "2026-10-28T09:00:00Z",
            "payload": {"fact_enc": "gcm:iv:ct"},
        }
        assert (await api.push(mutation)).json()[0]["status"] == "applied"
        rows = (await api.pull())["data"]["memories"]
        assert [(r["id"], r["fact_enc"]) for r in rows] == [(memory_id, "gcm:iv:ct")]


async def test_the_old_memory_payload_fails_only_itself(migrated_url, monkeypatch) -> None:
    """The pre-F-13 shape (`content_enc`, a key the server ignores) inserts NULL into
    `fact_enc NOT NULL`. The audit noted this would 500 every push the moment the id was fixed
    without the key; with per-row isolation it is one error row."""
    async with _Api(migrated_url, monkeypatch) as api:
        good = str(uuid.uuid4())
        old = {
            "table": "memories", "op": "upsert", "id": str(uuid.uuid4()), "client_updated_at": "2026-10-28T09:00:00Z",
            "payload": {"content_enc": "gcm:iv:ct"},
        }
        response = await api.push(old, _task(good))
        assert response.status_code == 200, response.text
        assert [r["status"] for r in response.json()] == ["error", "applied"]
        assert _ids(await api.pull(), "tasks") == [good]
