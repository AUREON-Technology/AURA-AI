"""Symmetric encryption for third-party secrets that must survive a restart in Postgres.

Nothing else in this codebase persists a third-party credential to a database -- every existing
secret (LLM provider API keys, the admin GitHub PAT in ``config/settings.py``'s
``GitHubSettings``) lives only in an environment variable and is never written to a table. A
per-user GitHub OAuth token (``adapters/postgres_github_identity.py``) is the first secret that
must outlive a process restart somewhere other than the environment, so this is new
infrastructure, not a reuse of an existing helper.

Fernet (AES-128-CBC + HMAC-SHA256, from the already-required ``cryptography`` package) rather
than hand-rolled AES-GCM: it is authenticated, versioned, and time-stamped out of the box, and
adding no new dependency matters more here than shaving a few bytes of ciphertext.

The key itself follows the same rule ``GitHubSettings.token`` does -- named by an env var, read
directly from the environment, never stored on a dataclass a settings dump could serialize.
"""

from __future__ import annotations

import os
from typing import Any

from ..domain.errors import ConfigurationError

__all__ = ["encrypt_secret", "decrypt_secret"]

_KEY_ENV = "AURA_SECRETS_ENCRYPTION_KEY"


def _fernet() -> Any:
    from cryptography.fernet import Fernet

    key = os.environ.get(_KEY_ENV, "")
    if not key:
        raise ConfigurationError(
            f"{_KEY_ENV} must be set before any secret can be encrypted or decrypted "
            "(generate one with: python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())")',
            details={"setting": _KEY_ENV},
        )
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise ConfigurationError(
            f"{_KEY_ENV} is not a valid Fernet key", details={"setting": _KEY_ENV}
        ) from exc


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ConfigurationError(
            f"a stored secret could not be decrypted -- {_KEY_ENV} may have changed since it "
            "was written",
            details={"setting": _KEY_ENV},
        ) from exc
