"""Per-user data keys and the gcm2 field format (ADR-032).

What must hold for the owner's Option A to be safe enough to ship:

* a data key is only ever stored wrapped by the KEK, bound to its owner and kid, and survives a
  KEK rotation (old key still listed) and a re-wrap under the new one;
* a DEK rotation keeps every older key readable;
* ``gcm2`` round-trips, is byte-compatible with the Flutter client (a vector the Dart side
  produced), and reports tampering instead of returning garbage -- while a legacy ``gcm:`` value
  or an unknown key id is *unreadable* (``None``), never handed on as text;
* the key endpoint returns only the caller's own key, uncached, and refuses services and
  unauthenticated callers.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.field_crypto import ENVELOPE_PREFIX, FieldCipher
from aura_core.adapters.personal_data_crypto import decrypt_field, encrypt_field
from aura_core.adapters.postgres_synced_personal_os import TABLES, from_row, to_columns
from aura_core.adapters.user_data_keys import (
    DataKeyWrapper,
    InMemoryUserDataKeyStore,
    new_data_key,
)
from aura_core.domain.errors import DataIntegrityError
from aura_core.domain.identifiers import UserId
from aura_core.domain.personal_os import RecordKind
from aura_core.domain.user_keys import DataKey, UserKeyring

OWNER = UserId("user-a")

#: Produced by aura_client's EncryptionService (Dart `encrypt` package, AES-GCM) for
#: `aura_client/test/data_key_crypto_test.dart`: the same key and plaintext the Dart test pins.
_FIXED_KEY = bytes(range(32))
_DART_VECTOR = "gcm2:0a1b2c3d:ZGVmZ2hpamtsbW5v:Cm6nRhSAOvUegN98+rzvJfMaq9IjtFTMqQ5qMV+mBS4tZv5K706K"
_DART_PLAINTEXT = "Buy milk — مرحبا"


def _keyring(*keys: tuple[str, bytes, bool], owner: UserId = OWNER) -> UserKeyring:
    now = datetime.now(timezone.utc)
    return UserKeyring(
        owner_id=owner,
        keys=tuple(DataKey(kid=kid, key=key, created_at=now, primary=p) for kid, key, p in keys),
    )


class TestWrapping:
    def test_a_wrapped_key_round_trips_and_never_holds_the_key_in_the_clear(self) -> None:
        wrapper = DataKeyWrapper(FieldCipher([Fernet.generate_key().decode()]))
        key = new_data_key()

        wrapped = wrapper.wrap("user-a", "0a1b2c3d", key)

        assert wrapped.startswith(ENVELOPE_PREFIX)
        assert base64.b64encode(key).decode() not in wrapped
        assert wrapper.unwrap("user-a", "0a1b2c3d", wrapped) == key

    def test_a_wrapped_key_moved_to_another_owner_fails_authentication(self) -> None:
        wrapper = DataKeyWrapper(FieldCipher([Fernet.generate_key().decode()]))
        wrapped = wrapper.wrap("user-a", "0a1b2c3d", new_data_key())

        with pytest.raises(DataIntegrityError):
            wrapper.unwrap("user-b", "0a1b2c3d", wrapped)
        with pytest.raises(DataIntegrityError):
            wrapper.unwrap("user-a", "ffffffff", wrapped)

    def test_kek_rotation_keeps_old_wraps_readable_and_rewraps_under_the_new_key(self) -> None:
        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        before = DataKeyWrapper(FieldCipher([old]))
        key = new_data_key()
        wrapped = before.wrap("user-a", "0a1b2c3d", key)

        rotated_cipher = FieldCipher([new, old])  # newest first, as AURA_DATA_ENCRYPTION_KEYS
        after = DataKeyWrapper(rotated_cipher)
        assert after.unwrap("user-a", "0a1b2c3d", wrapped) == key
        assert rotated_cipher.needs_reencryption(wrapped)

        rewrapped = after.wrap("user-a", "0a1b2c3d", after.unwrap("user-a", "0a1b2c3d", wrapped))
        assert not rotated_cipher.needs_reencryption(rewrapped)
        assert after.kek_id == rotated_cipher.primary_key_id
        # The old KEK alone can no longer open it: dropping it after re-wrapping is safe.
        assert DataKeyWrapper(FieldCipher([new])).unwrap("user-a", "0a1b2c3d", rewrapped) == key
        with pytest.raises(DataIntegrityError):
            DataKeyWrapper(FieldCipher([old])).unwrap("user-a", "0a1b2c3d", rewrapped)

    def test_a_keyring_repr_never_contains_key_material(self) -> None:
        key = new_data_key()
        ring = _keyring(("0a1b2c3d", key, True))
        assert key.hex() not in repr(ring)
        assert repr(key) not in repr(ring)


class TestInMemoryStore:
    async def test_first_use_creates_one_primary_key_and_reuses_it(self) -> None:
        store = InMemoryUserDataKeyStore(FieldCipher([Fernet.generate_key().decode()]))

        assert await store.keyring(OWNER, create=False) is None
        first = await store.keyring(OWNER)
        again = await store.keyring(OWNER)

        assert len(first.keys) == 1 and first.primary.key == again.primary.key
        assert len(first.primary.key) == 32

    async def test_each_user_gets_their_own_key(self) -> None:
        store = InMemoryUserDataKeyStore()
        a = await store.keyring(UserId("user-a"))
        b = await store.keyring(UserId("user-b"))
        assert a.primary.key != b.primary.key

    async def test_rotation_keeps_the_old_key_for_decryption(self) -> None:
        store = InMemoryUserDataKeyStore()
        before = await store.keyring(OWNER)
        sealed = encrypt_field(before, "old secret")

        after = await store.rotate(OWNER)

        assert after.primary.kid != before.primary.kid
        assert {k.kid for k in after.keys} == {before.primary.kid, after.primary.kid}
        assert decrypt_field(after, sealed) == "old secret"
        assert encrypt_field(after, "x").split(":")[1] == after.primary.kid


class TestGcm2:
    def test_round_trip(self) -> None:
        ring = _keyring(("0a1b2c3d", new_data_key(), True))
        sealed = encrypt_field(ring, "Dentist at 9")

        assert sealed.startswith("gcm2:0a1b2c3d:")
        assert "Dentist" not in sealed
        assert decrypt_field(ring, sealed) == "Dentist at 9"

    def test_decrypts_what_the_flutter_client_wrote(self) -> None:
        ring = _keyring(("0a1b2c3d", _FIXED_KEY, True))
        assert decrypt_field(ring, _DART_VECTOR) == _DART_PLAINTEXT

    def test_legacy_device_ciphertext_and_unknown_keys_are_unreadable_not_text(self) -> None:
        ring = _keyring(("0a1b2c3d", new_data_key(), True))
        other = _keyring(("deadbeef", new_data_key(), True))

        assert decrypt_field(ring, "gcm:aXY=:Y3Q=") is None
        assert decrypt_field(ring, encrypt_field(other, "someone else's")) is None
        assert decrypt_field(ring, "plain text in an _enc column") is None
        assert decrypt_field(ring, "") == ""  # a tombstone's blanked column

    def test_tampering_is_reported(self) -> None:
        ring = _keyring(("0a1b2c3d", new_data_key(), True))
        prefix, kid, iv, ct = encrypt_field(ring, "pay rent").split(":")
        flipped = base64.b64encode(bytes([base64.b64decode(ct)[0] ^ 1]) + base64.b64decode(ct)[1:])

        with pytest.raises(DataIntegrityError):
            decrypt_field(ring, f"{prefix}:{kid}:{iv}:{flipped.decode()}")
        with pytest.raises(DataIntegrityError):
            decrypt_field(ring, f"{prefix}:{kid}:not-base64!:{ct}")


class TestColumnMapping:
    def test_a_task_maps_onto_the_synced_columns_with_ciphertext_text(self) -> None:
        ring = _keyring(("0a1b2c3d", new_data_key(), True))
        now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)

        cols = to_columns(RecordKind.TASK, {
            "title": "File taxes", "priority": "high", "due_date": "2026-11-15",
            "completed": False, "note": "ignored", "unknown": 1,
        }, ring, now=now)

        assert set(cols) == {"title_enc", "priority", "due_date", "completed_at"}
        assert cols["title_enc"].startswith("gcm2:")
        assert cols["completed_at"] is None
        assert cols["due_date"] == datetime(2026, 11, 15, tzinfo=timezone.utc)

        row = {c.column: None for c in TABLES[RecordKind.TASK].columns} | cols
        fields = from_row(RecordKind.TASK, row, ring)
        assert fields == {
            "title": "File taxes", "priority": "high", "due_date": "2026-11-15",
            "completed": False,
        }

    def test_a_field_the_server_cannot_read_is_named_never_returned(self) -> None:
        ring = _keyring(("0a1b2c3d", new_data_key(), True))
        row = {c.column: None for c in TABLES[RecordKind.TASK].columns} | {
            "title_enc": "gcm:aXY=:Y3Q=", "priority": "low",
        }

        fields = from_row(RecordKind.TASK, row, ring)

        assert "title" not in fields
        assert "gcm:" not in repr(fields)
        assert "title" in fields["note"]
        assert fields["priority"] == "low"
