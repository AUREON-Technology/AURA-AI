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


# -- F-07: deletes ---------------------------------------------------------------------------


async def test_a_forgotten_memory_is_a_tombstone_without_its_text(migrated_url, monkeypatch) -> None:
    """The tombstone reaches other devices; the encrypted fact does not stay on the server."""
    import asyncpg

    async with _Api(migrated_url, monkeypatch) as api:
        memory_id = str(uuid.uuid4())
        await api.push({
            "table": "memories", "op": "upsert", "id": memory_id, "client_updated_at": "2026-10-28T09:00:00Z",
            "payload": {"fact_enc": "gcm:iv:secret"},
        })
        cursor = (await api.pull())["cursor"]
        forget = {"table": "memories", "op": "delete", "id": memory_id, "client_updated_at": "2026-10-28T09:05:00Z"}
        assert (await api.push(forget)).json()[0]["status"] == "applied"

        rows = (await api.pull(cursor))["data"]["memories"]
        assert [r["id"] for r in rows] == [memory_id]
        assert rows[0]["deleted_at"] is not None and rows[0]["fact_enc"] == ""

        conn = await asyncpg.connect(migrated_url)
        try:
            stored = await conn.fetchval("SELECT fact_enc FROM memories WHERE id = $1", uuid.UUID(memory_id))
        finally:
            await conn.close()
        assert stored == ""


async def test_a_deleted_message_keeps_no_ciphertext(migrated_url, monkeypatch) -> None:
    async with _Api(migrated_url, monkeypatch) as api:
        message_id = str(uuid.uuid4())
        await api.push({
            "table": "messages", "op": "upsert", "id": message_id, "client_updated_at": "2026-10-28T09:00:00Z",
            "payload": {"conversation_id": "s1", "role": "user", "content_enc": "gcm:iv:hello",
                        "citations_enc": "gcm:iv:cites", "tool_calls_enc": None},
        })
        await api.push({"table": "messages", "op": "delete", "id": message_id, "client_updated_at": "2026-10-28T09:01:00Z"})
        row = (await api.pull())["data"]["messages"][0]
        assert row["deleted_at"] is not None
        assert row["content_enc"] == "" and row["citations_enc"] is None


# -- INT-001: two changes to one row in the same second ------------------------------------------


async def test_a_same_second_upsert_then_delete_in_one_batch_ends_deleted(migrated_url, monkeypatch) -> None:
    """Save, then forget, within one second. The client's clock is stored in whole seconds, so
    both mutations carry the same `client_updated_at`. Ties against another writer still lose,
    but a later mutation in the same batch is the same device's newer change -- the outbox is
    sent in the order the user made them -- so it must apply. Before, the delete came back
    `conflict`, the client dropped it, and the row (for a memory, its ciphertext) stayed alive."""
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        same = "2026-11-17T09:00:00Z"
        delete = {"table": "tasks", "op": "delete", "id": task_id, "client_updated_at": same}
        response = await api.push(_task(task_id, at=same), delete)
        assert response.status_code == 200, response.text
        assert [r["status"] for r in response.json()] == ["applied", "applied"]
        rows = (await api.pull())["data"]["tasks"]
        assert [r["id"] for r in rows] == [task_id]
        assert rows[0]["deleted_at"] is not None and rows[0]["title_enc"] == ""


async def test_a_same_second_double_edit_in_one_batch_keeps_the_second(migrated_url, monkeypatch) -> None:
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        same = "2026-11-17T09:00:00Z"
        response = await api.push(_task(task_id, at=same, priority="low"), _task(task_id, at=same, priority="high"))
        assert [r["status"] for r in response.json()] == ["applied", "applied"]
        assert (await api.pull())["data"]["tasks"][0]["priority"] == "high"


async def test_a_tie_with_a_row_from_an_earlier_request_still_loses(migrated_url, monkeypatch) -> None:
    """The batch rule must not become "ties win": across requests the server cannot tell the
    same device's newer change from another device's concurrent one, so `>=` holds there (the
    client keeps its own clock strictly increasing per row instead)."""
    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        same = "2026-11-17T09:00:00Z"
        assert (await api.push(_task(task_id, at=same, priority="low"))).json()[0]["status"] == "applied"
        assert (await api.push(_task(task_id, at=same, priority="high"))).json()[0]["status"] == "conflict"


# -- INT-005: an edit that arrives after a delete ------------------------------------------------


async def test_an_upsert_onto_a_tombstone_is_a_conflict_carrying_the_tombstone(migrated_url, monkeypatch) -> None:
    """Device A deletes; device B, offline, edits later by its own clock. A deleted id is gone
    for good -- the client never re-inserts one (`undoable_deletes.dart` defers the delete
    instead) -- so the edit is refused as a conflict carrying the tombstone, which the client
    applies as a local delete. Before, the server answered `applied` (the client dropped the edit
    as saved), kept `deleted_at` (the next pull deleted it locally anyway), and wrote the edit's
    ciphertext back onto the tombstone, undoing F-07."""
    import asyncpg

    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        await api.push(_task(task_id, at="2026-01-01T00:00:00Z"))
        delete = {"table": "tasks", "op": "delete", "id": task_id, "client_updated_at": "2026-01-02T00:00:00Z"}
        assert (await api.push(delete)).json()[0]["status"] == "applied"

        late = (await api.push(_task(task_id, at="2026-01-03T00:00:00Z", description_enc="gcm:iv:new"))).json()[0]
        assert late["status"] == "conflict"
        assert late["row"]["id"] == task_id and late["row"]["deleted_at"] is not None
        assert late["row"]["title_enc"] == "" and late["row"]["description_enc"] is None

        conn = await asyncpg.connect(migrated_url)
        try:
            stored = await conn.fetchrow(
                "SELECT title_enc, description_enc, deleted_at FROM tasks WHERE id = $1", uuid.UUID(task_id),
            )
        finally:
            await conn.close()
        assert stored["deleted_at"] is not None
        assert stored["title_enc"] == "" and stored["description_enc"] is None


async def test_a_new_record_on_a_tombstoned_natural_key_still_revives_it(migrated_url, monkeypatch) -> None:
    """The natural-key merge is a *new* id for the key, not an edit of the deleted one: it keeps
    reviving the row, as the personal-OS adapter's `revive` does."""
    async with _Api(migrated_url, monkeypatch) as api:
        first = str(uuid.uuid4())
        await api.push(_health(first, at="2026-10-27T08:00:00Z", water=500))
        await api.push({"table": "health_records", "op": "delete", "id": first, "client_updated_at": "2026-10-27T09:00:00Z"})
        merged = (await api.push(_health(str(uuid.uuid4()), at="2026-10-27T10:00:00Z", water=200))).json()[0]
        assert merged["status"] == "applied" and merged["server_id"] == first
        assert merged["row"]["deleted_at"] is None and merged["row"]["water_ml"] == 200


# -- INT-013: a paged pull -----------------------------------------------------------------------


async def _pull_all_pages(api: _Api, *, since: str | None, limit: int) -> tuple[dict[str, list[str]], str, int]:
    params: dict = {"limit": limit}
    if since:
        params["since"] = since
    seen: dict[str, list[str]] = {}
    pages = 0
    while True:
        response = await api.http.get("/api/v3/sync", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        pages += 1
        assert sum(len(rows) for rows in body["data"].values()) <= limit
        for table, rows in body["data"].items():
            seen.setdefault(table, []).extend(r["id"] for r in rows)
        if not body["has_more"]:
            return seen, body["cursor"], pages
        params = {"limit": limit, "page": body["next_page"]}
        assert pages < 100


def _message(message_id: str) -> dict:
    return {
        "table": "messages", "op": "upsert", "id": message_id, "client_updated_at": "2026-10-28T09:00:00Z",
        "payload": {"conversation_id": "s1", "role": "user", "content_enc": "gcm:iv:x"},
    }


async def test_a_full_pull_pages_through_every_row(migrated_url, monkeypatch) -> None:
    async with _Api(migrated_url, monkeypatch) as api:
        tasks = [str(uuid.uuid4()) for _ in range(7)]
        messages = [str(uuid.uuid4()) for _ in range(5)]
        await api.push(*(_task(t) for t in tasks))
        await api.push(*(_message(m) for m in messages))

        seen, cursor, pages = await _pull_all_pages(api, since=None, limit=3)
        assert pages >= 4
        assert sorted(seen["tasks"]) == sorted(tasks)
        assert sorted(seen["messages"]) == sorted(messages)
        assert cursor.startswith("txid:")
        assert all(not rows for rows in (await api.pull(cursor))["data"].values())


async def test_a_write_during_paging_reaches_the_next_pull(migrated_url, monkeypatch) -> None:
    """The final cursor is the first page's snapshot xmin, so anything written while the pages
    were being fetched is at or past it."""
    async with _Api(migrated_url, monkeypatch) as api:
        await api.push(*(_task(str(uuid.uuid4())) for _ in range(4)))
        first = (await api.http.get("/api/v3/sync", params={"limit": 2})).json()
        assert first["has_more"] is True
        late = str(uuid.uuid4())
        await api.push(_task(late))
        params = {"limit": 2, "page": first["next_page"]}
        while True:
            body = (await api.http.get("/api/v3/sync", params=params)).json()
            if not body["has_more"]:
                break
            params = {"limit": 2, "page": body["next_page"]}
        assert late in _ids(await api.pull(body["cursor"]), "tasks")


async def test_a_delta_pull_pages_too(migrated_url, monkeypatch) -> None:
    async with _Api(migrated_url, monkeypatch) as api:
        await api.push(*(_task(str(uuid.uuid4())) for _ in range(3)))
        cursor = (await api.pull())["cursor"]
        newer = [str(uuid.uuid4()) for _ in range(5)]
        await api.push(*(_task(t, at="2026-02-01T00:00:00Z") for t in newer))
        seen, _, pages = await _pull_all_pages(api, since=cursor, limit=2)
        assert pages >= 3 and sorted(seen["tasks"]) == sorted(newer)


async def test_a_pull_without_a_limit_is_one_response_for_older_clients(migrated_url, monkeypatch) -> None:
    """An older client ignores `has_more`; it must keep getting everything in one response."""
    async with _Api(migrated_url, monkeypatch) as api:
        tasks = [str(uuid.uuid4()) for _ in range(5)]
        await api.push(*(_task(t) for t in tasks))
        body = await api.pull()
        assert sorted(_ids(body, "tasks")) == sorted(tasks)
        assert body["has_more"] is False and body.get("next_page") is None


# -- F31: two pushes racing for the same row -----------------------------------------------------


def _clock(row) -> datetime:
    value = row["client_updated_at"]
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _wait_until_blocked(conn, database: str) -> None:
    """Until some session on ``database`` is waiting on a row lock."""
    import asyncio

    for _ in range(500):
        waiting = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = $1 AND wait_event_type = 'Lock'", database,
        )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the push never waited on the concurrent writer's lock")


async def test_an_older_push_racing_a_newer_write_does_not_overwrite_it(migrated_url, monkeypatch) -> None:
    """The race the audit described (F31), made deterministic: another push's newer edit has been
    written but not yet committed when an older edit of the same row arrives.

    Without a row lock the older push read the committed (oldest) version, judged itself newer,
    waited on the UPDATE, and then overwrote the newer edit as soon as it committed."""
    import asyncio

    import asyncpg

    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        assert (await api.push(_task(task_id, at="2026-03-01T10:00:00Z"))).status_code == 200
        conn = await asyncpg.connect(migrated_url)
        newer = await asyncpg.connect(migrated_url)
        try:
            transaction = newer.transaction()
            await transaction.start()
            await newer.execute(
                "UPDATE tasks SET title_enc = 'gcm:newer:x', client_updated_at = '2026-03-01T10:00:20Z' "
                "WHERE id = $1", uuid.UUID(task_id),
            )
            older = asyncio.create_task(api.push(_task(task_id, at="2026-03-01T10:00:10Z", title_enc="gcm:older:x")))
            await _wait_until_blocked(conn, urlsplit(migrated_url).path.lstrip("/"))
            await transaction.commit()
            response = await older

            assert response.status_code == 200, response.text
            assert response.json()[0]["status"] == "conflict"
            row = await conn.fetchrow("SELECT title_enc, client_updated_at FROM tasks WHERE id = $1", uuid.UUID(task_id))
            assert row["title_enc"] == "gcm:newer:x"
            assert _clock(row) == datetime(2026, 3, 1, 10, 0, 20, tzinfo=timezone.utc)
        finally:
            await newer.close()
            await conn.close()


async def test_concurrent_pushes_of_one_row_leave_the_newest_edit(migrated_url, monkeypatch) -> None:
    """Many devices pushing the same row at once: whatever order they commit in, the row ends at
    the newest clock and every older push is told it lost."""
    import asyncio

    import asyncpg

    async with _Api(migrated_url, monkeypatch) as api:
        task_id = str(uuid.uuid4())
        await api.push(_task(task_id, at="2026-03-01T10:00:00Z"))
        base = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        seconds = [7, 3, 12, 1, 9, 4, 11, 2, 8, 5, 10, 6]
        responses = await asyncio.gather(*(
            api.push(_task(task_id, at=(base + timedelta(seconds=s)).isoformat(), title_enc=f"gcm:{s}:x"))
            for s in seconds
        ))
        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
        conn = await asyncpg.connect(migrated_url)
        try:
            row = await conn.fetchrow("SELECT title_enc, client_updated_at FROM tasks WHERE id = $1", uuid.UUID(task_id))
        finally:
            await conn.close()
        assert row["title_enc"] == "gcm:12:x"
        assert _clock(row) == base + timedelta(seconds=12)


# -- INT-110: a batch's messages keep their order ------------------------------------------------


def _chat(message_id: str, *, at: datetime, created: datetime | None = None, text: str = "x") -> dict:
    payload = {"conversation_id": "c1", "role": "user", "content_enc": f"gcm:iv:{text}"}
    if created is not None:
        payload["created_at"] = created.isoformat()
    return {"table": "messages", "op": "upsert", "id": message_id, "client_updated_at": at.isoformat(),
            "payload": payload}


def _created(body: dict, ids: list[str]) -> list[datetime]:
    by_id = {row["id"]: datetime.fromisoformat(row["created_at"]) for row in body["data"]["messages"]}
    return [by_id[i] for i in ids]


async def test_a_batch_of_messages_keeps_its_order_on_another_device(migrated_url, monkeypatch) -> None:
    """Today's client sends no `created_at`; the envelope clock it queued each row with orders them."""
    async with _Api(migrated_url, monkeypatch) as api:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        clocks = [now - timedelta(minutes=3 - i) for i in range(3)]
        results = (await api.push(*(_chat(i, at=c) for i, c in zip(ids, clocks)))).json()
        assert [r["status"] for r in results] == ["applied"] * 3

        created = _created(await api.pull(), ids)
        assert created == clocks
        assert created == sorted(created) and len(set(created)) == 3


async def test_a_sent_creation_time_is_kept_and_never_rewritten(migrated_url, monkeypatch) -> None:
    async with _Api(migrated_url, monkeypatch) as api:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        created_at = [now - timedelta(hours=5 - i) for i in range(3)]
        # Queued in the opposite order to their creation: `created_at` wins.
        await api.push(*(
            _chat(i, at=now - timedelta(seconds=10 - n), created=c) for n, (i, c) in enumerate(zip(ids, created_at))
        ))
        assert _created(await api.pull(), ids) == created_at

        # A later edit cannot move it.
        await api.push(_chat(ids[0], at=now + timedelta(seconds=1), created=now, text="edited"))
        assert _created(await api.pull(), ids)[0] == created_at[0]


async def test_an_implausible_creation_time_is_replaced_by_the_envelope_or_server_clock(migrated_url, monkeypatch) -> None:
    async with _Api(migrated_url, monkeypatch) as api:
        message_id, other = str(uuid.uuid4()), str(uuid.uuid4())
        before = (datetime.now(timezone.utc) - timedelta(seconds=5)).replace(microsecond=0)
        far = datetime(2031, 1, 1, tzinfo=timezone.utc)
        await api.push(_chat(message_id, at=before, created=far))
        # The payload value is refused; the (plausible) envelope clock is used instead.
        assert _created(await api.pull(), [message_id])[0] == before

        ancient = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await api.push(_chat(other, at=ancient, created=ancient))
        stamped = _created(await api.pull(), [other])[0]
        assert abs(stamped - datetime.now(timezone.utc)) < timedelta(minutes=5)
