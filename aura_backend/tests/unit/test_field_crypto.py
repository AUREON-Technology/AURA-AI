"""Server-side encryption at rest for memory content and personal-OS record fields (P1.2/P5.1).

Not end-to-end encryption: the server holds the key. What these tests pin is the contract
``adapters/field_crypto.py`` promises the two Postgres stores:

* a value written with a key configured is stored as an authenticated, versioned envelope and
  reads back unchanged;
* rotation works (newest key encrypts, every configured key decrypts);
* tampering, row-swapping, and a missing key surface as domain errors, never as ciphertext
  handed back as if it were data;
* rows written before a key existed (legacy plaintext) still read, and are flagged for
  re-encryption;
* the missing-key policy differs by environment: staging/production refuse to start, dev/test
  run in plaintext with a warning.

The live Postgres half of this (ciphertext actually on disk, legacy rows in a real table) is the
opt-in ``tests/load/test_field_encryption_postgres.py``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.field_crypto import (
    ENVELOPE_PREFIX,
    JSON_ENVELOPE_KEY,
    FieldCipher,
    key_id,
)
from aura_core.adapters.postgres_personal_os import PostgresPersonalOsStore
from aura_core.adapters.postgres_store import PostgresMemoryStore
from aura_core.config.settings import AuraSettings, data_encryption_keys
from aura_core.container import build_field_cipher
from aura_core.domain.errors import ConfigurationError, DataIntegrityError
from aura_core.domain.identifiers import MemoryId, PersonalOsRecordId, UserId
from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance
from aura_core.domain.personal_os import PersonalOsRecord, RecordKind

KEY_OLD = Fernet.generate_key().decode()
KEY_NEW = Fernet.generate_key().decode()


@pytest.fixture
def env(monkeypatch):
    for key in [k for k in os.environ if k.startswith("AURA_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ALLOW_MOCK_ATTESTATION", raising=False)
    monkeypatch.delenv("DEBUG_MODE", raising=False)
    return monkeypatch


# -- the cipher itself -----------------------------------------------------------------------


class TestRoundTrip:
    def test_text_round_trips_and_is_not_stored_in_the_clear(self) -> None:
        cipher = FieldCipher([KEY_NEW])
        stored = cipher.encrypt("I am allergic to penicillin", context="row-1")
        assert stored.startswith(f"{ENVELOPE_PREFIX}{key_id(KEY_NEW)}:")
        assert "penicillin" not in stored
        assert cipher.decrypt(stored, context="row-1") == "I am allergic to penicillin"

    def test_encryption_is_randomised(self) -> None:
        """Equal plaintexts must not produce equal ciphertexts, or the column leaks equality."""
        cipher = FieldCipher([KEY_NEW])
        assert cipher.encrypt("same", context="c") != cipher.encrypt("same", context="c")

    def test_unicode_and_empty_context_round_trip(self) -> None:
        cipher = FieldCipher([KEY_NEW])
        text = "موعد الطبيب يوم الخميس 🩺"
        assert cipher.decrypt(cipher.encrypt(text)) == text

    def test_mapping_round_trips_as_a_single_sealed_value(self) -> None:
        cipher = FieldCipher([KEY_NEW])
        fields = {"amount": 42.5, "type": "expense", "note": "rent", "tags": ["home"]}
        stored = cipher.encrypt_mapping(fields, context="rec-1")
        assert list(stored) == [JSON_ENVELOPE_KEY]
        assert "rent" not in json.dumps(stored)
        assert cipher.decrypt_mapping(stored, context="rec-1") == fields


class TestRotation:
    def test_newest_key_encrypts_and_every_key_decrypts(self) -> None:
        before = FieldCipher([KEY_OLD])
        sealed_under_old = before.encrypt("old fact", context="r")

        after = FieldCipher([KEY_NEW, KEY_OLD])
        assert after.primary_key_id == key_id(KEY_NEW)
        assert after.decrypt(sealed_under_old, context="r") == "old fact"
        assert after.needs_reencryption(sealed_under_old)

        resealed = after.encrypt(after.decrypt(sealed_under_old, context="r"), context="r")
        assert resealed.startswith(f"{ENVELOPE_PREFIX}{key_id(KEY_NEW)}:")
        assert not after.needs_reencryption(resealed)

    def test_retiring_a_key_before_reencrypting_makes_its_rows_unreadable(self) -> None:
        sealed_under_old = FieldCipher([KEY_OLD]).encrypt("old fact")
        with pytest.raises(DataIntegrityError) as excinfo:
            FieldCipher([KEY_NEW]).decrypt(sealed_under_old)
        assert excinfo.value.details["reason"] == "unknown_key"

    def test_needs_reencryption_sees_through_the_mapping_envelope(self) -> None:
        old_mapping = FieldCipher([KEY_OLD]).encrypt_mapping({"a": 1})
        rotated = FieldCipher([KEY_NEW, KEY_OLD])
        assert rotated.needs_reencryption(old_mapping)
        assert not rotated.needs_reencryption(rotated.encrypt_mapping({"a": 1}))


class TestTamperDetection:
    def test_a_modified_token_fails_authentication(self) -> None:
        cipher = FieldCipher([KEY_NEW])
        stored = cipher.encrypt("balance is 1200")
        # Flip one character in the middle of the Fernet token.
        middle = len(stored) - 20
        flipped = stored[:middle] + ("A" if stored[middle] != "A" else "B") + stored[middle + 1:]
        with pytest.raises(DataIntegrityError) as excinfo:
            cipher.decrypt(flipped)
        assert excinfo.value.details["reason"] == "authentication_failed"
        assert excinfo.value.kind == "data_integrity_error"

    def test_a_truncated_value_fails(self) -> None:
        cipher = FieldCipher([KEY_NEW])
        with pytest.raises(DataIntegrityError):
            cipher.decrypt(cipher.encrypt("x")[:-10])

    def test_a_value_moved_to_another_row_is_refused(self) -> None:
        """Someone with database write access but no key must not be able to move one user's
        memory into another user's row."""
        cipher = FieldCipher([KEY_NEW])
        stored = cipher.encrypt("alice's secret", context="aura_memories:m1:alice")
        with pytest.raises(DataIntegrityError) as excinfo:
            cipher.decrypt(stored, context="aura_memories:m1:mallory")
        assert excinfo.value.details["reason"] == "context_mismatch"

    @pytest.mark.parametrize("value", [
        f"{ENVELOPE_PREFIX}",
        f"{ENVELOPE_PREFIX}nothex!!:gAAAA",
        f"{ENVELOPE_PREFIX}abcdef01:",
    ])
    def test_a_malformed_envelope_is_refused(self, value: str) -> None:
        with pytest.raises(DataIntegrityError):
            FieldCipher([KEY_NEW]).decrypt(value)


class TestLegacyPlaintext:
    def test_plaintext_written_before_a_key_existed_still_reads(self) -> None:
        cipher = FieldCipher([KEY_NEW])
        assert cipher.decrypt("written in 2025, before encryption") == (
            "written in 2025, before encryption"
        )
        assert cipher.needs_reencryption("written in 2025, before encryption")

    def test_a_plaintext_mapping_still_reads(self) -> None:
        cipher = FieldCipher([KEY_NEW])
        assert cipher.decrypt_mapping({"title": "Dentist"}) == {"title": "Dentist"}
        assert cipher.decrypt_mapping({}) == {}
        assert cipher.needs_reencryption({"title": "Dentist"})


class TestPassthrough:
    """No key configured: the dev/test mode."""

    def test_writes_plaintext(self) -> None:
        cipher = FieldCipher.disabled()
        assert not cipher.enabled
        assert cipher.encrypt("hello") == "hello"
        assert cipher.encrypt_mapping({"a": 1}) == {"a": 1}
        assert not cipher.needs_reencryption("hello")

    def test_refuses_to_return_ciphertext_it_cannot_read(self) -> None:
        sealed = FieldCipher([KEY_NEW]).encrypt("secret")
        with pytest.raises(ConfigurationError):
            FieldCipher.disabled().decrypt(sealed)


class TestKeyValidation:
    def test_an_invalid_key_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError):
            FieldCipher(["not-a-fernet-key"])

    def test_a_duplicated_key_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError):
            FieldCipher([KEY_NEW, KEY_NEW])


# -- missing-key policy per environment ------------------------------------------------------


class TestMissingKeyPolicy:
    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_production_like_environments_refuse_to_build_a_plaintext_cipher(
        self, env, environment: str
    ) -> None:
        env.setenv("AURA_ENVIRONMENT", environment)
        with pytest.raises(ConfigurationError):
            build_field_cipher(AuraSettings.from_env())

    @pytest.mark.parametrize("environment", ["local", "development"])
    def test_dev_environments_run_in_plaintext_with_a_warning(
        self, env, environment: str, caplog
    ) -> None:
        env.setenv("AURA_ENVIRONMENT", environment)
        with caplog.at_level(logging.WARNING):
            cipher = build_field_cipher(AuraSettings.from_env())
        assert not cipher.enabled
        assert any("DISABLED" in r.getMessage() for r in caplog.records)

    def test_a_configured_key_enables_encryption_in_any_environment(self, env) -> None:
        env.setenv("AURA_ENVIRONMENT", "production")
        env.setenv("AURA_DATA_ENCRYPTION_KEYS", f"{KEY_NEW},{KEY_OLD}")
        cipher = build_field_cipher(AuraSettings.from_env())
        assert cipher.key_ids == (key_id(KEY_NEW), key_id(KEY_OLD))

    def test_keys_may_be_comma_or_newline_separated(self, env) -> None:
        env.setenv("AURA_DATA_ENCRYPTION_KEYS", f" {KEY_NEW}\n{KEY_OLD} ,\n")
        assert data_encryption_keys() == [KEY_NEW, KEY_OLD]

    def test_production_validation_names_the_missing_key(self, env) -> None:
        env.setenv("AURA_ENVIRONMENT", "production")
        with pytest.raises(ConfigurationError) as excinfo:
            AuraSettings.from_env().validate()
        assert any("AURA_DATA_ENCRYPTION_KEYS" in p for p in excinfo.value.details["problems"])

    def test_a_malformed_key_is_refused_even_in_development(self, env) -> None:
        env.setenv("AURA_DATA_ENCRYPTION_KEYS", "definitely-not-base64-32-bytes")
        with pytest.raises(ConfigurationError) as excinfo:
            AuraSettings.from_env().validate()
        assert any("not a valid Fernet key" in p for p in excinfo.value.details["problems"])

    def test_the_container_hands_both_postgres_stores_the_same_cipher(self, env) -> None:
        from aura_core.container import Container

        env.setenv("AURA_MEMORY_BACKEND", "postgres")
        env.setenv("AURA_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/unused")
        env.setenv("AURA_DATA_ENCRYPTION_KEYS", KEY_NEW)
        container = Container(AuraSettings.from_env())  # lazy: no connection is opened
        assert isinstance(container.memory_store, PostgresMemoryStore)
        # ADR-032: the agent's store is the synced tables, sealed with per-user data keys that
        # are themselves wrapped by this same cipher; the old table keeps it directly.
        assert isinstance(container.legacy_personal_os_store, PostgresPersonalOsStore)
        assert container.memory_store._cipher.enabled
        assert container.memory_store._cipher is container.legacy_personal_os_store._cipher
        assert container.user_data_keys._wrapper._cipher is container.memory_store._cipher
        assert container.personal_os_store._keys is container.user_data_keys


# -- the two stores, against a recording connection ------------------------------------------


class _RecordingConn:
    """Keeps the last row written so a later SELECT returns exactly what was stored."""

    def __init__(self, columns: tuple[str, ...]) -> None:
        self.columns = columns
        self.rows: dict[str, dict] = {}

    async def execute(self, sql: str, *args):
        if sql.lstrip().upper().startswith("INSERT"):
            row = dict(zip(self.columns, args))
            self.rows[row["id"]] = row
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args):
        return self.rows.get(args[0])

    async def fetch(self, sql: str, *args):
        return list(self.rows.values())


class _Pool:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def _get_pool(self):
        return self

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


_MEMORY_COLUMNS = (
    "id", "kind", "owner_id", "content", "subject", "confidence", "embedding_ref", "session_id",
    "created_at", "last_accessed_at", "access_count", "superseded_by", "expires_at",
    "prov_source", "prov_turn_id", "prov_session_id", "prov_extractor", "prov_extractor_ver",
    "prov_document_ref", "prov_recorded_at", "metadata",
)


def _memory(content: str = "prefers window seats", owner: str = "alice") -> MemoryRecord:
    return MemoryRecord(
        id=MemoryId("m1"), kind=MemoryKind.SEMANTIC, owner_id=UserId(owner), content=content,
        provenance=Provenance(source="test"), subject="travel",
    )


class TestMemoryStoreEncryptsContent:
    async def test_content_is_sealed_on_write_and_opened_on_read(self) -> None:
        conn = _RecordingConn(_MEMORY_COLUMNS)
        store = PostgresMemoryStore("postgresql://u:p@h/db", cipher=FieldCipher([KEY_NEW]))
        store._pool = _Pool(conn)

        await store.put(_memory())
        stored = conn.rows["m1"]
        assert FieldCipher.is_encrypted(stored["content"])
        assert "window" not in stored["content"]
        # Everything a query filters on stays in the clear.
        assert (stored["owner_id"], stored["kind"], stored["subject"]) == (
            "alice", "semantic", "travel"
        )

        got = await store.get(MemoryId("m1"))
        assert got is not None and got.content == "prefers window seats"
        listed = await store.list_for_owner(UserId("alice"))
        assert [r.content for r in listed] == ["prefers window seats"]

    async def test_a_legacy_plaintext_row_reads_and_is_sealed_on_its_next_write(self) -> None:
        conn = _RecordingConn(_MEMORY_COLUMNS)
        legacy = PostgresMemoryStore("postgresql://u:p@h/db")  # no key: plaintext era
        legacy._pool = _Pool(conn)
        await legacy.put(_memory("written before the key"))
        assert conn.rows["m1"]["content"] == "written before the key"

        store = PostgresMemoryStore("postgresql://u:p@h/db", cipher=FieldCipher([KEY_NEW]))
        store._pool = _Pool(conn)
        record = await store.get(MemoryId("m1"))
        assert record is not None and record.content == "written before the key"

        await store.update(record.touched())
        assert FieldCipher.is_encrypted(conn.rows["m1"]["content"])
        assert (await store.get(MemoryId("m1"))).content == "written before the key"

    async def test_a_row_whose_owner_was_rewritten_fails_closed(self) -> None:
        conn = _RecordingConn(_MEMORY_COLUMNS)
        store = PostgresMemoryStore("postgresql://u:p@h/db", cipher=FieldCipher([KEY_NEW]))
        store._pool = _Pool(conn)
        await store.put(_memory())
        conn.rows["m1"]["owner_id"] = "mallory"
        with pytest.raises(DataIntegrityError):
            await store.get(MemoryId("m1"))


_PERSONAL_OS_COLUMNS = ("id", "owner_id", "kind", "fields", "source", "created_at", "updated_at")


def _record(fields: dict) -> PersonalOsRecord:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return PersonalOsRecord(
        id=PersonalOsRecordId("r1"), owner_id=UserId("alice"), kind=RecordKind.HEALTH_RECORD,
        fields=fields, created_at=now, updated_at=now,
    )


class TestPersonalOsStoreEncryptsFields:
    async def test_fields_are_sealed_on_write_and_opened_on_read(self) -> None:
        conn = _RecordingConn(_PERSONAL_OS_COLUMNS)
        store = PostgresPersonalOsStore("postgresql://u:p@h/db", cipher=FieldCipher([KEY_NEW]))
        store._pool = _Pool(conn)
        store._schema_ready = True

        await store.put(_record({"blood_pressure": "120/80", "notes": "after run"}))
        stored = json.loads(conn.rows["r1"]["fields"])
        assert list(stored) == [JSON_ENVELOPE_KEY]
        assert "120/80" not in conn.rows["r1"]["fields"]
        assert conn.rows["r1"]["kind"] == "health_record"

        got = await store.get(PersonalOsRecordId("r1"), owner_id=UserId("alice"))
        assert got is not None
        assert got.fields == {"blood_pressure": "120/80", "notes": "after run"}

    async def test_a_legacy_plaintext_row_still_reads(self) -> None:
        conn = _RecordingConn(_PERSONAL_OS_COLUMNS)
        store = PostgresPersonalOsStore("postgresql://u:p@h/db", cipher=FieldCipher([KEY_NEW]))
        store._pool = _Pool(conn)
        store._schema_ready = True
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        conn.rows["r1"] = {
            "id": "r1", "owner_id": "alice", "kind": "task", "fields": '{"title": "Call mum"}',
            "source": "kernel", "created_at": now, "updated_at": now,
        }
        listed = await store.list_for_owner(UserId("alice"))
        assert [r.fields for r in listed] == [{"title": "Call mum"}]


class TestLegacyPlaintextReadsAreCounted:
    """Audit FSA-094: rows written before a key was configured are read silently, so nothing said
    how many remained or whether ``scripts/reencrypt_fields.py`` had ever run. Every plaintext
    read under a configured key is counted, on the cipher and in the metrics registry."""

    def test_each_plaintext_read_is_counted_when_a_key_is_configured(self) -> None:
        cipher = FieldCipher([Fernet.generate_key().decode()])
        assert cipher.decrypt("legacy value") == "legacy value"
        assert cipher.decrypt_mapping({"a": 1}) == {"a": 1}
        assert cipher.legacy_plaintext_reads == 2
        cipher.decrypt(cipher.encrypt("sealed"))
        cipher.decrypt_mapping(cipher.encrypt_mapping({"b": 2}))
        assert cipher.legacy_plaintext_reads == 2, "sealed values are not legacy reads"

    def test_a_disabled_cipher_counts_nothing(self) -> None:
        """Without a key every value is plaintext by design; counting would only be noise."""
        cipher = FieldCipher.disabled()
        cipher.decrypt("plain")
        cipher.decrypt_mapping({"a": 1})
        assert cipher.legacy_plaintext_reads == 0

    def test_the_count_reaches_the_metrics_registry_by_kind(self) -> None:
        from aura_core.observability.metrics import Metrics

        metrics = Metrics()
        cipher = FieldCipher([Fernet.generate_key().decode()], metrics=metrics)
        cipher.decrypt("legacy value")
        cipher.decrypt_mapping({"a": 1})
        cipher.decrypt_mapping({"b": 2})
        rendered = metrics.render().decode()
        assert 'aura_field_crypto_legacy_plaintext_reads_total{kind="text"} 1.0' in rendered
        assert 'aura_field_crypto_legacy_plaintext_reads_total{kind="mapping"} 2.0' in rendered

    def test_the_container_s_cipher_reports_to_the_container_s_metrics(self, monkeypatch) -> None:
        from aura_core.observability.metrics import Metrics

        monkeypatch.setenv("AURA_DATA_ENCRYPTION_KEYS", Fernet.generate_key().decode())
        metrics = Metrics()
        cipher = build_field_cipher(AuraSettings.from_env(), metrics=metrics)
        cipher.decrypt("legacy value")
        assert 'aura_field_crypto_legacy_plaintext_reads_total{kind="text"} 1.0' in metrics.render().decode()
