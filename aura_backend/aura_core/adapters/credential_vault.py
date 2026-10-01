"""The connector credential vault: encrypted at rest, addressed by opaque refs (ADR-021).

Connector credentials are third-party secrets, the same class ``secrets_crypto.py`` protects, so
they are sealed under ``AURA_SECRETS_ENCRYPTION_KEY`` (comma-separated, newest first, for
rotation) rather than the data key that protects memories. The primitive is
:class:`~aura_core.adapters.field_crypto.FieldCipher`, which brings three properties for free:

* **Context binding.** Each ciphertext is sealed with ``connector_credential:<secret_ref>`` in
  its authenticated plaintext, so a ciphertext copied onto another ref -- by someone with write
  access to the table but no key -- fails authentication instead of handing connector B the
  credential of connector A.
* **Rotation.** The first key encrypts, every configured key decrypts.
* **Tamper detection.** A modified or truncated value raises ``DataIntegrityError``.

What this module adds on top is the refusal to ever work without a key. ``FieldCipher`` in its
passthrough mode would store plaintext and return a stored plaintext value unchanged; the vault
refuses both, so a missing key can make connectors unhealthy but can never put a credential on
disk in the clear or read one back from a row that was.
"""

from __future__ import annotations

import secrets
from typing import Any, Protocol

from ..domain.connectors import SECRET_REF_PATTERN, Credential
from ..domain.errors import ConfigurationError, DataIntegrityError, NotFoundError, ValidationError
from .field_crypto import FieldCipher

__all__ = [
    "SECRETS_KEYS_ENV",
    "CiphertextStore",
    "InMemoryCiphertextStore",
    "EncryptedCredentialVault",
]

SECRETS_KEYS_ENV = "AURA_SECRETS_ENCRYPTION_KEY"


class CiphertextStore(Protocol):
    """Where sealed credentials rest. Only ever sees ciphertext."""

    async def put(self, secret_ref: str, ciphertext: str) -> None: ...

    async def get(self, secret_ref: str) -> str | None: ...

    async def delete(self, secret_ref: str) -> bool: ...


class InMemoryCiphertextStore:
    def __init__(self) -> None:
        self._rows: dict[str, str] = {}

    async def put(self, secret_ref: str, ciphertext: str) -> None:
        self._rows[secret_ref] = ciphertext

    async def get(self, secret_ref: str) -> str | None:
        return self._rows.get(secret_ref)

    async def delete(self, secret_ref: str) -> bool:
        return self._rows.pop(secret_ref, None) is not None

    def raw_rows(self) -> dict[str, str]:
        """Test seam: what is actually at rest."""
        return dict(self._rows)


class EncryptedCredentialVault:
    def __init__(self, *, cipher: FieldCipher, store: CiphertextStore) -> None:
        self._cipher = cipher
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._cipher.enabled

    async def put(self, secret: Credential) -> str:
        self._require_key()
        secret_ref = f"cred_{secrets.token_hex(16)}"
        sealed = self._cipher.encrypt(secret.reveal(), context=_context(secret_ref))
        await self._store.put(secret_ref, sealed)
        return secret_ref

    async def get(self, secret_ref: str) -> Credential:
        _check_ref(secret_ref)
        self._require_key()
        stored = await self._store.get(secret_ref)
        if stored is None:
            raise NotFoundError("no credential is stored under that reference")
        if not FieldCipher.is_encrypted(stored):
            # Something wrote a credential around the vault. Refuse it rather than use it.
            raise DataIntegrityError(
                "a stored connector credential is not encrypted", details={"reason": "plaintext_at_rest"}
            )
        return Credential(self._cipher.decrypt(stored, context=_context(secret_ref)))

    async def delete(self, secret_ref: str) -> bool:
        _check_ref(secret_ref)
        return await self._store.delete(secret_ref)

    def _require_key(self) -> None:
        if not self._cipher.enabled:
            raise ConfigurationError(
                f"{SECRETS_KEYS_ENV} must be set before connector credentials can be stored or "
                "used; the vault never falls back to plaintext",
                details={"setting": SECRETS_KEYS_ENV},
            )


def _context(secret_ref: str) -> str:
    return f"connector_credential:{secret_ref}"


def _check_ref(secret_ref: Any) -> None:
    if not isinstance(secret_ref, str) or not SECRET_REF_PATTERN.match(secret_ref):
        raise ValidationError("not a vault reference")
