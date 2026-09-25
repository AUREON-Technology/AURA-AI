"""``SecretManager`` must actually encrypt (GAP-009, docs/production/gap_register.yaml).

Its original "AES-256-CTR" derived the keystream from the counter alone and never used the key,
so every ciphertext decrypted under any key. These pin the properties any real cipher has.
"""

from __future__ import annotations

import base64

import pytest

from aura_core.security.secret_manager import SecretManager, ValidationError


def test_round_trip() -> None:
    sm = SecretManager()
    entry = sm.encrypt_secret("github-token", "s3cr3t")
    assert sm.decrypt_secret(entry, expected_secret_id="github-token") == "s3cr3t"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    entry = SecretManager().encrypt_secret("k", "plaintext-marker")
    assert b"plaintext-marker" not in base64.b64decode(entry["encrypted"])


def test_a_different_key_cannot_decrypt() -> None:
    owner = SecretManager()
    entry = owner.encrypt_secret("k", "s3cr3t")
    attacker = SecretManager()
    attacker._ensure_initialized()
    attacker._keys["default"]["key"] = bytes(32)
    with pytest.raises(ValidationError):
        attacker.decrypt_secret(entry)


def test_tampered_ciphertext_is_rejected() -> None:
    sm = SecretManager()
    entry = sm.encrypt_secret("k", "s3cr3t")
    raw = bytearray(base64.b64decode(entry["encrypted"]))
    raw[0] ^= 0x01
    with pytest.raises(ValidationError):
        sm.decrypt_secret({**entry, "encrypted": base64.b64encode(bytes(raw)).decode()})


def test_an_entry_presented_under_another_secret_id_is_rejected() -> None:
    sm = SecretManager()
    entry = sm.encrypt_secret("a", "s3cr3t")
    with pytest.raises(ValidationError):
        sm.decrypt_secret({**entry, "secret_id": "b"})


def test_rotation_re_encrypts_under_a_new_key_and_old_entries_stay_readable() -> None:
    sm = SecretManager()
    old = sm.encrypt_secret("k", "s3cr3t")
    new = sm.rotate_key("k")
    assert new["key_id"] != old["key_id"]
    assert sm.decrypt_secret(new, expected_secret_id="k") == "s3cr3t"
    assert sm.decrypt_secret(old, expected_secret_id="k") == "s3cr3t"
