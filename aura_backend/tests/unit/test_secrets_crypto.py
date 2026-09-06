"""``adapters/secrets_crypto.py`` -- the only place a third-party credential (a per-user GitHub
OAuth token) is ever written to Postgres in the clear-on-read, encrypted-at-rest form."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.secrets_crypto import decrypt_secret, encrypt_secret
from aura_core.domain.errors import ConfigurationError

_KEY_ENV = "AURA_SECRETS_ENCRYPTION_KEY"


@pytest.fixture(autouse=True)
def _clear_key(monkeypatch):
    monkeypatch.delenv(_KEY_ENV, raising=False)


def test_round_trips(monkeypatch) -> None:
    monkeypatch.setenv(_KEY_ENV, Fernet.generate_key().decode())
    ciphertext = encrypt_secret("ghu_supersecrettoken")
    assert ciphertext != "ghu_supersecrettoken"
    assert decrypt_secret(ciphertext) == "ghu_supersecrettoken"


def test_missing_key_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match=_KEY_ENV):
        encrypt_secret("x")


def test_invalid_key_raises_configuration_error(monkeypatch) -> None:
    monkeypatch.setenv(_KEY_ENV, "not-a-valid-fernet-key")
    with pytest.raises(ConfigurationError, match=_KEY_ENV):
        encrypt_secret("x")


def test_ciphertext_from_a_different_key_fails_to_decrypt(monkeypatch) -> None:
    monkeypatch.setenv(_KEY_ENV, Fernet.generate_key().decode())
    ciphertext = encrypt_secret("ghu_supersecrettoken")

    monkeypatch.setenv(_KEY_ENV, Fernet.generate_key().decode())
    with pytest.raises(ConfigurationError):
        decrypt_secret(ciphertext)
