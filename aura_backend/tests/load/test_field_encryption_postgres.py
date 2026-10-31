"""Encryption at rest (P1.2/P5.1) against a real Postgres.

Opt-in, like everything under ``tests/load``: the ``load`` marker keeps it out of the default
run, and it skips unless ``AURA_TEST_DATABASE_URL`` names a database. It needs *only* Postgres --
no Qdrant, Redis, or model -- so a throwaway local cluster is enough::

    initdb -D /tmp/pg/data -U aura --auth=trust
    pg_ctl -D /tmp/pg/data -o "-p 55433 -k /tmp/pg -c listen_addresses=''" -l /tmp/pg/log start
    createdb -h /tmp/pg -p 55433 -U aura aura_enc
    AURA_TEST_DATABASE_URL='postgresql://aura@/aura_enc?host=/tmp/pg&port=55433' \\
        pytest tests/load/test_field_encryption_postgres.py -m load -q

Every test runs in its own freshly created schema (selected through the DSN's ``search_path``)
which is dropped afterwards, so pointing this at a shared database never touches its real
``aura_memories``/``aura_personal_os_records`` tables.

What the in-memory unit tests (``tests/unit/test_field_crypto.py``) cannot show and this does:
the ciphertext is what actually lands in the column (read back with raw SQL, not through the
adapter), asyncpg's JSONB round-trip of the envelope, a legacy plaintext row written into a real
table, the SQL filters (owner/kind/subject/expiry/provenance) still working with ``content``
sealed, and the re-encryption script's keyset pagination and conditional updates.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.field_crypto import ENVELOPE_PREFIX, JSON_ENVELOPE_KEY, FieldCipher, key_id
from aura_core.adapters.postgres_personal_os import PostgresPersonalOsStore
from aura_core.adapters.postgres_store import PostgresMemoryStore, PostgresPool
from aura_core.domain.errors import DataIntegrityError
from aura_core.domain.identifiers import MemoryId, PersonalOsRecordId, UserId
from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance
from aura_core.domain.personal_os import PersonalOsRecord, RecordKind

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
KEY_OLD = Fernet.generate_key().decode()
KEY_NEW = Fernet.generate_key().decode()


def _with_search_path(url: str, schema: str) -> str:
    """asyncpg passes unrecognised DSN query parameters through as server settings."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema
    return urlunsplit(parts._replace(query=urlencode(query)))


@pytest.fixture
async def schema_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    schema = f"enc_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield _with_search_path(_URL, schema)
    finally:
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.fixture
async def raw(schema_url):
    import asyncpg

    conn = await asyncpg.connect(schema_url)
    yield conn
    await conn.close()


def _memory(mid: str, content: str, *, owner: str = "alice", subject: str | None = "travel",
            kind: MemoryKind = MemoryKind.SEMANTIC, source: str = "test",
            expires_at: datetime | None = None) -> MemoryRecord:
    return MemoryRecord(
        id=MemoryId(mid), kind=kind, owner_id=UserId(owner), content=content,
        provenance=Provenance(source=source), subject=subject, expires_at=expires_at,
    )


def _pos(rid: str, kind: RecordKind, fields: dict, *, owner: str = "alice") -> PersonalOsRecord:
    now = datetime.now(timezone.utc)
    return PersonalOsRecord(
        id=PersonalOsRecordId(rid), owner_id=UserId(owner), kind=kind, fields=fields,
        created_at=now, updated_at=now,
    )


class TestMemoryStore:
    async def test_crud_and_every_query_path_with_content_sealed(self, schema_url, raw) -> None:
        pool = PostgresPool(schema_url, min_size=1, max_size=2)
        store = PostgresMemoryStore(schema_url, pool=pool, cipher=FieldCipher([KEY_NEW]))
        try:
            await store.put(_memory("m1", "Alice is allergic to penicillin", subject="health"))
            await store.put(_memory("m2", "Alice prefers window seats"))
            await store.put(_memory("m3", "Alice's badge number is 4471",
                                    kind=MemoryKind.ENTITY, source="doc:hr"))
            await store.put(_memory("m4", "Bob likes tea", owner="bob"))
            await store.put(_memory("m5", "an expired note",
                                    expires_at=datetime.now(timezone.utc) - timedelta(days=1)))

            # On disk: every content value is an envelope under the primary key, no plaintext.
            rows = await raw.fetch("SELECT id, content, owner_id, kind, subject FROM aura_memories")
            assert len(rows) == 5
            for row in rows:
                assert row["content"].startswith(f"{ENVELOPE_PREFIX}{key_id(KEY_NEW)}:")
                for word in ("penicillin", "window", "4471", "tea", "expired"):
                    assert word not in row["content"]
            # ...while every filter column stays readable.
            assert {r["owner_id"] for r in rows} == {"alice", "bob"}
            assert await raw.fetchval(
                "SELECT count(*) FROM aura_memories WHERE content LIKE '%penicillin%'"
            ) == 0

            got = await store.get(MemoryId("m1"))
            assert got is not None and got.content == "Alice is allergic to penicillin"
            many = await store.get_many([MemoryId("m2"), MemoryId("m1")])
            assert [r.content for r in many] == [
                "Alice prefers window seats", "Alice is allergic to penicillin",
            ]
            active = await store.list_for_owner(UserId("alice"))
            assert {r.id.value for r in active} == {"m1", "m2", "m3"}  # m5 expired, m4 is bob's
            by_subject = await store.list_for_owner(UserId("alice"), subject="health")
            assert [r.content for r in by_subject] == ["Alice is allergic to penicillin"]
            by_kind = await store.list_for_owner(UserId("alice"), kinds=[MemoryKind.ENTITY])
            assert [r.content for r in by_kind] == ["Alice's badge number is 4471"]
            assert await store.count(UserId("alice")) == 4

            touched = got.touched()
            await store.update(touched)
            stored = await raw.fetchval("SELECT content FROM aura_memories WHERE id = 'm1'")
            assert FieldCipher.is_encrypted(stored) and "penicillin" not in stored
            assert (await store.get(MemoryId("m1"))).access_count == 1

            assert await store.delete_by_provenance(owner_id=UserId("alice"), source="doc:hr") == 1
            assert await store.delete(MemoryId("m2"), owner_id=UserId("bob")) is False
            assert await store.delete(MemoryId("m2"), owner_id=UserId("alice")) is True
            assert {r.id.value for r in await store.list_for_owner(UserId("alice"))} == {"m1"}
        finally:
            await pool.close()

    async def test_legacy_plaintext_reads_then_is_sealed_on_next_write(self, schema_url, raw) -> None:
        pool = PostgresPool(schema_url, min_size=1, max_size=2)
        try:
            legacy = PostgresMemoryStore(schema_url, pool=pool)  # no key: pre-encryption era
            await legacy.put(_memory("old", "written before encryption existed"))
            assert await raw.fetchval(
                "SELECT content FROM aura_memories WHERE id = 'old'"
            ) == "written before encryption existed"

            store = PostgresMemoryStore(schema_url, pool=pool, cipher=FieldCipher([KEY_NEW]))
            record = await store.get(MemoryId("old"))
            assert record is not None and record.content == "written before encryption existed"
            assert [r.content for r in await store.list_for_owner(UserId("alice"))] == [
                "written before encryption existed"
            ]

            await store.update(record.touched())
            assert FieldCipher.is_encrypted(
                await raw.fetchval("SELECT content FROM aura_memories WHERE id = 'old'")
            )
        finally:
            await pool.close()

    async def test_tampered_or_moved_ciphertext_fails_closed(self, schema_url, raw) -> None:
        pool = PostgresPool(schema_url, min_size=1, max_size=2)
        store = PostgresMemoryStore(schema_url, pool=pool, cipher=FieldCipher([KEY_NEW]))
        try:
            await store.put(_memory("a1", "alice's secret"))
            await store.put(_memory("b1", "bob's note", owner="bob"))
            # Copy alice's ciphertext into bob's row: authenticates as Fernet, but is bound to
            # alice's row, so it must not decrypt as bob's.
            await raw.execute(
                "UPDATE aura_memories SET content = "
                "(SELECT content FROM aura_memories WHERE id = 'a1') WHERE id = 'b1'"
            )
            with pytest.raises(DataIntegrityError):
                await store.get(MemoryId("b1"))
            # Flip a byte inside the token.
            await raw.execute(
                "UPDATE aura_memories SET content = overlay(content placing "
                "CASE WHEN substr(content, 60, 1) = 'A' THEN 'B' ELSE 'A' END from 60 for 1) "
                "WHERE id = 'a1'"
            )
            with pytest.raises(DataIntegrityError):
                await store.get(MemoryId("a1"))
        finally:
            await pool.close()

    async def test_touch_many_is_a_field_level_update_of_the_current_row(
        self, schema_url, raw
    ) -> None:
        """F-16: recall reinforcement used the full-row UPSERT from the copy recall had read, so
        a supersede, an edit or a delete in between was reverted. ``touch_many`` is a single
        ``UPDATE ... SET access_count = access_count + 1``: it changes nothing else, re-creates
        nothing, never re-encrypts ``content``, and its increments do not lose to each other."""
        import asyncio
        from dataclasses import replace

        pool = PostgresPool(schema_url, min_size=1, max_size=10)
        store = PostgresMemoryStore(schema_url, pool=pool, cipher=FieldCipher([KEY_NEW]))
        try:
            await store.put(_memory("m1", "Alice lives in Cairo"))
            await store.put(_memory("m2", "Alice likes tea"))
            await store.put(_memory("b1", "Bob likes coffee", owner="bob"))
            snapshot = await store.get(MemoryId("m1"))  # what a recall would have read

            # Meanwhile: consolidation supersedes m1, and m2 is deleted.
            await store.update(replace(snapshot, superseded_by=MemoryId("m9"), confidence=0.9))
            assert await store.delete(MemoryId("m2"), owner_id=UserId("alice"))
            sealed = await raw.fetchval("SELECT content FROM aura_memories WHERE id = 'm1'")

            at = datetime.now(timezone.utc) + timedelta(hours=1)
            touched = await store.touch_many(
                [MemoryId("m1"), MemoryId("m2"), MemoryId("m1"), MemoryId("b1")],
                owner_id=UserId("alice"), at=at,
            )
            assert touched == 1  # m2 is gone, b1 is bob's, the duplicate m1 counts once

            m1 = await store.get(MemoryId("m1"))
            assert m1.superseded_by == MemoryId("m9"), "reinforcement resurrected m1"
            assert m1.confidence == pytest.approx(0.9)
            assert m1.access_count == 1 and m1.last_accessed_at == at
            assert await store.get(MemoryId("m2")) is None, "reinforcement re-created m2"
            assert (await store.get(MemoryId("b1"))).access_count == 0
            assert await raw.fetchval(
                "SELECT content FROM aura_memories WHERE id = 'm1'"
            ) == sealed  # content untouched: not even re-encrypted

            # An older clock never moves last_accessed_at backwards.
            await store.touch_many([MemoryId("m1")], owner_id=UserId("alice"),
                                   at=at - timedelta(days=30))
            m1 = await store.get(MemoryId("m1"))
            assert m1.access_count == 2 and m1.last_accessed_at == at

            # Concurrent recalls, each on its own connection: no increment is lost.
            await asyncio.gather(*(
                store.touch_many([MemoryId("m1")], owner_id=UserId("alice"), at=at)
                for _ in range(25)
            ))
            assert (await store.get(MemoryId("m1"))).access_count == 27
            assert await store.touch_many([], owner_id=UserId("alice"), at=at) == 0
        finally:
            await pool.close()


class TestPersonalOsStore:
    async def test_crud_with_fields_sealed(self, schema_url, raw) -> None:
        pool = PostgresPool(schema_url, min_size=1, max_size=2)
        store = PostgresPersonalOsStore(schema_url, pool=pool, cipher=FieldCipher([KEY_NEW]))
        try:
            await store.put(_pos("r1", RecordKind.HEALTH_RECORD, {"blood_pressure": "130/85"}))
            await store.put(_pos("r2", RecordKind.FINANCE_RECORD,
                                 {"amount": 1200.5, "type": "expense", "note": "rent"}))
            await store.put(_pos("r3", RecordKind.TASK, {"title": "Call mum"}, owner="bob"))

            rows = await raw.fetch("SELECT id, kind, fields::text AS f FROM aura_personal_os_records")
            for row in rows:
                parsed = json.loads(row["f"])
                assert list(parsed) == [JSON_ENVELOPE_KEY]
                assert FieldCipher.is_encrypted(parsed[JSON_ENVELOPE_KEY])
                for word in ("130/85", "rent", "mum"):
                    assert word not in row["f"]

            got = await store.get(PersonalOsRecordId("r2"), owner_id=UserId("alice"))
            assert got is not None and got.fields == {
                "amount": 1200.5, "type": "expense", "note": "rent",
            }
            assert await store.get(PersonalOsRecordId("r2"), owner_id=UserId("bob")) is None
            finance = await store.list_for_owner(UserId("alice"), kind=RecordKind.FINANCE_RECORD)
            assert [r.id.value for r in finance] == ["r2"]
            assert len(await store.list_for_owner(UserId("alice"))) == 2

            await store.update(got.with_fields({"note": "rent + utilities"},
                                               updated_at=datetime.now(timezone.utc)))
            again = await store.get(PersonalOsRecordId("r2"), owner_id=UserId("alice"))
            assert again.fields["note"] == "rent + utilities"
            assert "utilities" not in await raw.fetchval(
                "SELECT fields::text FROM aura_personal_os_records WHERE id = 'r2'"
            )

            assert await store.delete(PersonalOsRecordId("r2"), owner_id=UserId("alice")) is True
            assert await store.get(PersonalOsRecordId("r2"), owner_id=UserId("alice")) is None
        finally:
            await pool.close()

    async def test_legacy_plaintext_fields_still_read(self, schema_url, raw) -> None:
        pool = PostgresPool(schema_url, min_size=1, max_size=2)
        try:
            await PostgresPersonalOsStore(schema_url, pool=pool).put(
                _pos("legacy", RecordKind.TASK, {"title": "Renew passport"})
            )
            assert json.loads(await raw.fetchval(
                "SELECT fields::text FROM aura_personal_os_records WHERE id = 'legacy'"
            )) == {"title": "Renew passport"}

            store = PostgresPersonalOsStore(schema_url, pool=pool, cipher=FieldCipher([KEY_NEW]))
            got = await store.get(PersonalOsRecordId("legacy"), owner_id=UserId("alice"))
            assert got is not None and got.fields == {"title": "Renew passport"}
        finally:
            await pool.close()


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "reencrypt_fields.py"
    spec = importlib.util.spec_from_file_location("reencrypt_fields", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


class TestReencryptionScript:
    async def test_dry_run_then_encrypt_then_rotate_is_idempotent(
        self, schema_url, raw, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv("AURA_ENVIRONMENT", "local")
        script = _load_script()
        pool = PostgresPool(schema_url, min_size=1, max_size=2)
        try:
            plain_mem = PostgresMemoryStore(schema_url, pool=pool)
            plain_pos = PostgresPersonalOsStore(schema_url, pool=pool)
            for i in range(7):
                await plain_mem.put(_memory(f"p{i}", f"plaintext fact {i}", owner=f"u{i % 3}"))
            for i in range(3):
                await plain_pos.put(_pos(f"t{i}", RecordKind.TASK, {"title": f"task {i}"},
                                         owner=f"u{i}"))
            old_mem = PostgresMemoryStore(schema_url, pool=pool, cipher=FieldCipher([KEY_OLD]))
            await old_mem.put(_memory("o1", "sealed under the old key"))

            def last_report() -> dict:
                out = capsys.readouterr().out
                return json.loads(out[out.index("{"):])

            def mem_pos(report: dict) -> tuple[dict, dict]:
                # By name: the report also covers aura_user_data_keys since ADR-032.
                by_table = {t["table"]: t for t in report["tables"]}
                return by_table["aura_memories"], by_table["aura_personal_os_records"]

            # 1. Dry run with the old key only: counts, writes nothing.
            monkeypatch.setenv("AURA_DATA_ENCRYPTION_KEYS", KEY_OLD)
            assert await script.run(dry_run=True, batch=3, tables="all",
                                     database_url=schema_url) == 0
            report = last_report()
            mem, pos = mem_pos(report)
            assert (mem["plaintext"], mem["written"], pos["plaintext"]) == (7, 0, 3)
            assert await raw.fetchval(
                "SELECT count(*) FROM aura_memories WHERE content NOT LIKE 'aura-enc:%'"
            ) == 7

            # 2. Real run, batch smaller than the row count to exercise keyset pagination.
            assert await script.run(dry_run=False, batch=3, tables="all",
                                    database_url=schema_url) == 0
            mem, pos = mem_pos(last_report())
            assert (mem["written"], pos["written"]) == (7, 3)
            assert await raw.fetchval(
                "SELECT count(*) FROM aura_memories WHERE content NOT LIKE 'aura-enc:%'"
            ) == 0

            # 3. Idempotent: nothing left under the primary key.
            assert await script.run(dry_run=False, batch=3, tables="all",
                                    database_url=schema_url) == 0
            mem, pos = mem_pos(last_report())
            assert (mem["examined"], pos["examined"]) == (0, 0)

            # 4. Rotation: new key first, old key still configured; everything moves to new.
            monkeypatch.setenv("AURA_DATA_ENCRYPTION_KEYS", f"{KEY_NEW},{KEY_OLD}")
            assert await script.run(dry_run=False, batch=3, tables="all",
                                    database_url=schema_url) == 0
            mem, pos = mem_pos(last_report())
            assert (mem["old_key"], mem["written"], pos["written"]) == (8, 8, 3)

            # 5. The old key can now be dropped: the new key alone reads every row.
            only_new = FieldCipher([KEY_NEW])
            mem_store = PostgresMemoryStore(schema_url, pool=pool, cipher=only_new)
            pos_store = PostgresPersonalOsStore(schema_url, pool=pool, cipher=only_new)
            contents = {r.content for owner in ("u0", "u1", "u2", "alice")
                        for r in await mem_store.list_for_owner(UserId(owner))}
            assert contents == {f"plaintext fact {i}" for i in range(7)} | {
                "sealed under the old key"
            }
            assert (await pos_store.get(PersonalOsRecordId("t1"), owner_id=UserId("u1"))).fields \
                == {"title": "task 1"}

            # 6. A tampered row is reported by id (not content) and fails the run.
            await raw.execute("UPDATE aura_memories SET content = 'aura-enc:v1:deadbeef:xyz' "
                              "WHERE id = 'p0'")
            assert await script.run(dry_run=True, batch=3, tables="memories",
                                    database_url=schema_url) == 1
            assert last_report()["tables"][0]["failed_ids"] == ["p0"]
        finally:
            await pool.close()
