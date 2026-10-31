"""ADR-032 against a REAL Postgres: one personal-OS store for the agent and the app.

The schema here is built by ``alembic upgrade head`` (after a ``downgrade -1`` / ``upgrade head``
round trip of migration 0017), not ``create_all``, because what is under test depends on things
only the migrations carry: the column server defaults the agent's inserts rely on, the
``aura_user_data_keys`` foreign key to ``users(auth_subject)`` and its one-primary index.

What it proves, through the real ``/api/v3/sync`` endpoint:

* a task the agent creates is in that user's next pull -- and only theirs -- as ``gcm2``
  ciphertext that decrypts with the user's data key;
* a task the client pushes, encrypted with that key, is what the agent's ``list_tasks`` sees;
* the agent's update and delete are sync changes past the previous cursor, the delete a
  tombstone with its ciphertext blanked;
* a legacy device's ``gcm:`` text is never shown to the agent nor overwritten by its update;
* the old store's rows move idempotently; KEK rotation re-wraps keys; erasing the user row
  destroys their keys; racing first key requests leave one primary.

Skips itself cleanly when Docker isn't reachable, like the other integration tests here.
"""
import asyncio
import os
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def _alembic(url: str, *args: str) -> str:
    env = {**os.environ, "DATABASE_URL": url}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=HERE, env=env,
        capture_output=True, text=True, timeout=180,
    )
    if done.returncode != 0:
        raise AssertionError(f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}")
    return done.stdout + done.stderr


@unittest.skipUnless(_docker_available(), "Docker is not available -- needs a real Postgres 16.")
class TestPersonalOsSyncIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.fernet import Fernet
        from testcontainers.postgres import PostgresContainer

        cls.postgres = PostgresContainer("postgres:16-alpine")
        cls.postgres.start()
        raw = cls.postgres.get_connection_url()
        cls.async_url = raw.replace("postgresql+psycopg2", "postgresql+asyncpg")
        cls.dsn = raw.replace("postgresql+psycopg2", "postgresql")
        cls.kek = Fernet.generate_key().decode()

        _alembic(cls.async_url, "upgrade", "head")
        cls.migration_log = [_alembic(cls.async_url, "current")]
        _alembic(cls.async_url, "downgrade", "-1")
        cls.migration_log.append(_alembic(cls.async_url, "current"))
        cls.table_after_downgrade = asyncio.run(cls._scalar(
            "SELECT to_regclass('aura_user_data_keys') IS NOT NULL"
        ))
        _alembic(cls.async_url, "upgrade", "head")
        cls.migration_log.append(_alembic(cls.async_url, "current"))

        from config import settings

        settings.database_url = cls.async_url

    @classmethod
    def tearDownClass(cls):
        cls.postgres.stop()

    @classmethod
    async def _scalar(cls, sql, *args):
        import asyncpg

        conn = await asyncpg.connect(cls.dsn)
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.close()

    def setUp(self):
        import infrastructure.db.base as db_base

        db_base._engine = None
        db_base._session_factory = None

    # -- helpers ----------------------------------------------------------------------

    def _cipher(self, *keys):
        from aura_core.adapters.field_crypto import FieldCipher

        return FieldCipher(list(keys) or [self.kek])

    def _with_stores(self, work, *, cipher=None):
        """Run ``work(keys, store, pool)`` on a fresh pool on a fresh loop (asyncpg pools are
        bound to the loop that made them; TestClient runs its own)."""
        from aura_core.adapters.postgres_store import PostgresPool
        from aura_core.adapters.postgres_synced_personal_os import PostgresSyncedPersonalOsStore
        from aura_core.adapters.postgres_user_data_keys import PostgresUserDataKeyStore

        async def go():
            pool = PostgresPool(self.dsn, min_size=1, max_size=4)
            try:
                keys = PostgresUserDataKeyStore(self.dsn, pool=pool, cipher=cipher or self._cipher())
                store = PostgresSyncedPersonalOsStore(self.dsn, pool=pool, keys=keys)
                return await work(keys, store, pool)
            finally:
                await pool.close()

        return asyncio.run(go())

    def _tool(self, name, subject, **arguments):
        from aura_core.ports.runtime import ToolContext
        from aura_core.domain.identifiers import UserId
        from aura_core.tools.records import build_personal_os_crud_tools

        async def work(keys, store, pool):
            tools = {t.spec.name: t for t in build_personal_os_crud_tools(store)}
            context = ToolContext(user_id=UserId(subject), correlation_id="it", deadline_s=30)
            return await tools[name].execute(arguments, context)

        return self._with_stores(work)

    def _keyring(self, subject):
        from aura_core.domain.identifiers import UserId

        return self._with_stores(lambda keys, store, pool: keys.keyring(UserId(subject)))

    def _client(self, subject):
        from fastapi.testclient import TestClient

        import infrastructure.db.base as db_base
        from aura_service import app
        from services.jwt_auth import verify_access_token

        # A fresh engine per client: asyncpg connections belong to the loop that opened them,
        # and every TestClient runs its own.
        db_base._engine = None
        db_base._session_factory = None
        app.dependency_overrides[verify_access_token] = lambda: {
            "uid": subject, "email": f"{subject}@it.example", "name": subject,
            "picture": None, "role": "User",
        }
        self.addCleanup(app.dependency_overrides.clear)
        return self.enterContext(TestClient(app))

    @staticmethod
    def _rows(client, table, since=None):
        response = client.get("/api/v3/sync", params={"since": since} if since else None)
        assert response.status_code == 200, response.text
        body = response.json()
        return {r["id"]: r for r in body["data"][table]}, body["cursor"]

    # -- tests ------------------------------------------------------------------------

    def test_migration_0017_downgrades_and_upgrades(self):
        self.assertIn("0017", self.migration_log[0])
        self.assertIn("0016", self.migration_log[1])
        self.assertFalse(self.table_after_downgrade)
        self.assertIn("0017", self.migration_log[2])
        self.assertTrue(asyncio.run(self._scalar(
            "SELECT to_regclass('aura_user_data_keys') IS NOT NULL"
        )))

    def test_agent_created_task_reaches_only_that_users_pull_and_decrypts_with_their_key(self):
        from aura_core.adapters.personal_data_crypto import decrypt_field

        out = self._tool("create_task", "alice-1", title="Renew passport", priority="high",
                         due_date="2026-12-01")
        task_id = out.rsplit(" ", 1)[-1].rstrip(".")

        rows, _ = self._rows(self._client("alice-1"), "tasks")
        self.assertIn(task_id, rows)
        row = rows[task_id]
        self.assertTrue(row["title_enc"].startswith("gcm2:"))
        self.assertNotIn("passport", row["title_enc"])
        self.assertEqual(decrypt_field(self._keyring("alice-1"), row["title_enc"]), "Renew passport")
        self.assertEqual(row["priority"], "high")
        self.assertTrue(row["due_date"].startswith("2026-12-01"))

        other, _ = self._rows(self._client("bob-1"), "tasks")
        self.assertNotIn(task_id, other)

    def test_agent_sees_a_task_the_client_pushed_with_the_shared_key(self):
        from aura_core.adapters.personal_data_crypto import encrypt_field

        client = self._client("carol-1")
        client.get("/api/v3/sync")  # the users row, as a first sync creates it
        ring = self._keyring("carol-1")  # what GET /api/v3/keys/data hands the device
        task_id = str(uuid.uuid4())
        pushed = client.post("/api/v3/sync", json={"mutations": [{
            "table": "tasks", "op": "upsert", "id": task_id,
            "client_updated_at": datetime.now(timezone.utc).isoformat(),
            "payload": {"title_enc": encrypt_field(ring, "Call the plumber"),
                        "description_enc": None, "priority": "low", "category": "home"},
        }]})
        self.assertEqual(pushed.json()[0]["status"], "applied")

        listed = self._tool("list_tasks", "carol-1")
        self.assertIn("Call the plumber", listed)
        self.assertIn(task_id, listed)
        self.assertNotIn("gcm2:", listed)
        self.assertNotIn("Call the plumber", self._tool("list_tasks", "dave-1"))

    def test_agent_update_and_delete_are_sync_changes_and_tombstones(self):
        from aura_core.adapters.personal_data_crypto import decrypt_field

        out = self._tool("create_task", "erin-1", title="Draft report")
        task_id = out.rsplit(" ", 1)[-1].rstrip(".")
        client = self._client("erin-1")
        _, cursor = self._rows(client, "tasks")

        self.assertIn("Updated", self._tool("update_task", "erin-1", record_id=task_id,
                                            title="Send report", completed=True))
        changed, cursor = self._rows(client, "tasks", since=cursor)
        self.assertIn(task_id, changed)
        self.assertEqual(decrypt_field(self._keyring("erin-1"), changed[task_id]["title_enc"]),
                         "Send report")
        self.assertIsNotNone(changed[task_id]["completed_at"])

        # The client can still edit it under normal last-write-wins afterwards.
        later = client.post("/api/v3/sync", json={"mutations": [{
            "table": "tasks", "op": "upsert", "id": task_id,
            "client_updated_at": datetime.now(timezone.utc).isoformat(),
            "payload": {"priority": "high"},
        }]})
        self.assertEqual(later.json()[0]["status"], "applied")
        _, cursor = self._rows(client, "tasks", since=cursor)

        self.assertIn("Deleted", self._tool("delete_task", "erin-1", record_id=task_id))
        gone, _ = self._rows(client, "tasks", since=cursor)
        self.assertIsNotNone(gone[task_id]["deleted_at"])
        self.assertEqual(gone[task_id]["title_enc"], "")
        self.assertIsNone(gone[task_id]["description_enc"])
        self.assertIn("No task found", self._tool("delete_task", "erin-1", record_id=task_id))
        self.assertNotIn(task_id, self._tool("list_tasks", "erin-1"))

    def test_another_users_agent_cannot_touch_the_record(self):
        out = self._tool("create_task", "fay-1", title="Private")
        task_id = out.rsplit(" ", 1)[-1].rstrip(".")
        self.assertIn("No task found",
                      self._tool("update_task", "gus-1", record_id=task_id, title="pwned"))
        self.assertIn("No task found", self._tool("delete_task", "gus-1", record_id=task_id))
        self.assertIn("Private", self._tool("list_tasks", "fay-1"))

    def test_legacy_device_ciphertext_is_never_shown_nor_overwritten(self):
        client = self._client("hal-1")
        task_id = str(uuid.uuid4())
        legacy = "gcm:bGVnYWN5aXY=:bGVnYWN5Y3Q="
        client.post("/api/v3/sync", json={"mutations": [{
            "table": "tasks", "op": "upsert", "id": task_id,
            "client_updated_at": datetime.now(timezone.utc).isoformat(),
            "payload": {"title_enc": legacy, "priority": "low"},
        }]})

        listed = self._tool("list_tasks", "hal-1")
        self.assertIn(task_id, listed)
        self.assertNotIn(legacy, listed)
        self.assertIn("not readable", listed)

        self._tool("update_task", "hal-1", record_id=task_id, priority="high")
        rows, _ = self._rows(client, "tasks")
        self.assertEqual(rows[task_id]["title_enc"], legacy)
        self.assertEqual(rows[task_id]["priority"], "high")

    def test_a_health_record_for_an_existing_day_merges_like_a_sync_push(self):
        first = self._tool("create_health_record", "ivy-1", record_date="2026-10-30", water_ml=500)
        second = self._tool("create_health_record", "ivy-1", record_date="2026-10-30",
                            sleep_minutes=420)
        self.assertEqual(first.rsplit(" ", 1)[-1], second.rsplit(" ", 1)[-1])
        rows, _ = self._rows(self._client("ivy-1"), "health_records")
        self.assertEqual(len(rows), 1)
        (row,) = rows.values()
        self.assertEqual((row["water_ml"], row["sleep_minutes"]), (500, 420))

    def test_the_old_store_moves_idempotently(self):
        import importlib.util

        from aura_core.adapters.postgres_personal_os import PostgresPersonalOsStore
        from aura_core.domain.identifiers import PersonalOsRecordId, UserId
        from aura_core.domain.personal_os import PersonalOsRecord, RecordKind

        spec = importlib.util.spec_from_file_location(
            "move_personal_os_records", HERE / "scripts" / "move_personal_os_records.py"
        )
        mover = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mover
        spec.loader.exec_module(mover)
        now = datetime.now(timezone.utc)
        ids = [PersonalOsRecordId.new() for _ in range(2)]

        async def seed(keys, store, pool):
            legacy = PostgresPersonalOsStore(self.dsn, pool=pool, cipher=self._cipher())
            await legacy.put(PersonalOsRecord(
                id=ids[0], owner_id=UserId("jay-1"), kind=RecordKind.TASK,
                fields={"title": "Old agent task", "priority": "low"},
                created_at=now, updated_at=now,
            ))
            await legacy.put(PersonalOsRecord(
                id=ids[1], owner_id=UserId("jay-1"), kind=RecordKind.FINANCE_RECORD,
                fields={"type": "expense", "amount": 12.5, "description": "Lunch"},
                created_at=now, updated_at=now,
            ))

        self._with_stores(seed)

        def run(dry_run):
            async def work(keys, store, pool):
                return await mover.move(await pool._get_pool(), self._cipher(), store,
                                        batch=1, dry_run=dry_run)

            return self._with_stores(work)

        dry = run(True)
        self.assertGreaterEqual(dry["would_move"], 2)
        self.assertNotIn(ids[0].value, self._tool("list_tasks", "jay-1"))
        first = run(False)
        self.assertGreaterEqual(first["moved"], 2)
        self.assertEqual(first["failed"], 0)
        again = run(False)
        self.assertEqual(again.get("moved", 0), 0)
        self.assertGreaterEqual(again["already_moved"], 2)

        self.assertIn("Old agent task", self._tool("list_tasks", "jay-1"))
        self.assertIn("Lunch", self._tool("list_finance_records", "jay-1"))
        rows, _ = self._rows(self._client("jay-1"), "tasks")
        self.assertTrue(rows[ids[0].value]["title_enc"].startswith("gcm2:"))
        # The old table is left as it was.
        self.assertEqual(asyncio.run(self._scalar(
            "SELECT count(*) FROM aura_personal_os_records WHERE owner_id = 'jay-1'"
        )), 2)

    def test_kek_rotation_rewraps_every_data_key(self):
        import importlib.util

        from cryptography.fernet import Fernet

        before = self._keyring("kim-1").primary.key
        new_kek = Fernet.generate_key().decode()
        rotated = self._cipher(new_kek, self.kek)
        spec = importlib.util.spec_from_file_location(
            "reencrypt_fields", HERE / "scripts" / "reencrypt_fields.py"
        )
        script = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = script
        spec.loader.exec_module(script)

        async def rewrap(keys, store, pool):
            return await script._rewrap_user_data_keys(
                await pool._get_pool(), rotated, batch=2, dry_run=False
            )

        report = self._with_stores(rewrap)
        self.assertGreaterEqual(report.written, 1)
        self.assertEqual(report.failed_ids, [])
        from aura_core.domain.identifiers import UserId

        after = self._with_stores(
            lambda keys, store, pool: keys.keyring(UserId("kim-1"), create=False),
            cipher=self._cipher(new_kek),  # the old KEK is no longer needed
        )
        self.assertEqual(after.primary.key, before)
        self.assertEqual(asyncio.run(self._scalar(
            "SELECT count(*) FROM aura_user_data_keys WHERE kek_id <> $1",
            rotated.primary_key_id,
        )), 0)
        # Put the original KEK back in front for the other tests.
        restore = self._cipher(self.kek, new_kek)
        self._with_stores(lambda keys, store, pool: _rewrap_with(script, pool, restore))

    def test_dek_rotation_keeps_old_values_readable(self):
        from aura_core.adapters.personal_data_crypto import decrypt_field, encrypt_field
        from aura_core.domain.identifiers import UserId

        old = self._keyring("lee-1")
        sealed = encrypt_field(old, "before rotation")
        new = self._with_stores(lambda keys, store, pool: keys.rotate(UserId("lee-1")))
        self.assertNotEqual(new.primary.kid, old.primary.kid)
        self.assertEqual(decrypt_field(new, sealed), "before rotation")
        self.assertEqual(asyncio.run(self._scalar(
            "SELECT count(*) FROM aura_user_data_keys WHERE owner_id = 'lee-1' "
            "AND status = 'primary'"
        )), 1)

    def test_racing_first_requests_leave_exactly_one_primary(self):
        from aura_core.domain.identifiers import UserId

        async def race(keys, store, pool):
            return await asyncio.gather(*[keys.keyring(UserId("max-1")) for _ in range(6)])

        rings = self._with_stores(race)
        self.assertEqual(len({r.primary.kid for r in rings}), 1)

    def test_erasing_the_user_row_destroys_their_keys(self):
        self._keyring("ned-1")
        self.assertEqual(asyncio.run(self._scalar(
            "SELECT count(*) FROM aura_user_data_keys WHERE owner_id = 'ned-1'"
        )), 1)
        asyncio.run(self._scalar("DELETE FROM users WHERE auth_subject = 'ned-1' RETURNING 1"))
        self.assertEqual(asyncio.run(self._scalar(
            "SELECT count(*) FROM aura_user_data_keys WHERE owner_id = 'ned-1'"
        )), 0)

    def test_the_stored_key_is_wrapped(self):
        import base64

        ring = self._keyring("oli-1")
        stored = asyncio.run(self._scalar(
            "SELECT wrapped_key FROM aura_user_data_keys WHERE owner_id = 'oli-1'"
        ))
        self.assertTrue(stored.startswith("aura-enc:v1:"))
        self.assertNotIn(base64.b64encode(ring.primary.key).decode(), stored)


async def _rewrap_with(script, pool, cipher):
    return await script._rewrap_user_data_keys(
        await pool._get_pool(), cipher, batch=10, dry_run=False
    )


if __name__ == "__main__":
    unittest.main()
