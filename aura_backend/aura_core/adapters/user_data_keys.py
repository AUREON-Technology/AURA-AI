"""Per-user data keys: generation, wrapping, and the in-memory store (ADR-032).

A data key (DEK) is 32 bytes from the OS CSPRNG (``secrets.token_bytes``), generated on the
server the first time a user needs one. It is stored only *wrapped*: sealed by the server's
key-encryption key, which is the ``AURA_DATA_ENCRYPTION_KEYS`` :class:`FieldCipher` -- the same
keys, rotation rules and ``scripts/reencrypt_fields.py`` that already protect memories at rest.
The wrapped value is bound to ``aura_user_data_keys:<owner>:<kid>``, so a wrapped key copied onto
another user's row fails authentication instead of quietly giving that user someone else's key.

With no KEK configured (local development only; ``build_field_cipher`` refuses that in
staging/production) the cipher is a passthrough and the DEK is stored unwrapped, exactly as the
field cipher stores memory text unencrypted there.

Never log a :class:`~aura_core.domain.user_keys.DataKey`'s ``key``; its ``repr`` omits it.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from ..domain.errors import DataIntegrityError
from ..domain.identifiers import UserId
from ..domain.user_keys import DATA_KEY_BYTES, DataKey, UserKeyring
from .field_crypto import FieldCipher

__all__ = [
    "PRIMARY",
    "DECRYPT_ONLY",
    "WrappedKey",
    "DataKeyWrapper",
    "InMemoryUserDataKeyStore",
    "new_kid",
    "new_data_key",
]

PRIMARY = "primary"
DECRYPT_ONLY = "decrypt_only"


def new_kid() -> str:
    """8 random hex characters: not secret, unique per owner (the table's primary key)."""
    return secrets.token_hex(4)


def new_data_key() -> bytes:
    return secrets.token_bytes(DATA_KEY_BYTES)


@dataclass(frozen=True, slots=True)
class WrappedKey:
    """One stored row: what the database holds, never the key itself."""

    kid: str
    wrapped_key: str
    kek_id: str | None
    status: str
    created_at: datetime


class DataKeyWrapper:
    def __init__(self, cipher: FieldCipher) -> None:
        self._cipher = cipher

    @property
    def kek_id(self) -> str | None:
        return self._cipher.primary_key_id

    @staticmethod
    def context(owner_id: str, kid: str) -> str:
        return f"aura_user_data_keys:{owner_id}:{kid}"

    def wrap(self, owner_id: str, kid: str, key: bytes) -> str:
        return self._cipher.encrypt(
            base64.b64encode(key).decode("ascii"), context=self.context(owner_id, kid)
        )

    def unwrap(self, owner_id: str, kid: str, wrapped: str) -> bytes:
        encoded = self._cipher.decrypt(wrapped, context=self.context(owner_id, kid))
        try:
            key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            key = b""
        if len(key) != DATA_KEY_BYTES:
            raise DataIntegrityError(
                "a stored data key is malformed", details={"reason": "malformed_key", "kid": kid}
            )
        return key

    def new_row(self, owner_id: str, *, status: str = PRIMARY) -> WrappedKey:
        kid = new_kid()
        return WrappedKey(
            kid=kid, wrapped_key=self.wrap(owner_id, kid, new_data_key()),
            kek_id=self.kek_id, status=status, created_at=datetime.now(timezone.utc),
        )

    def keyring(self, owner_id: UserId, rows: list[WrappedKey]) -> UserKeyring:
        return UserKeyring(
            owner_id=owner_id,
            keys=tuple(
                DataKey(
                    kid=row.kid, key=self.unwrap(owner_id.value, row.kid, row.wrapped_key),
                    created_at=row.created_at, primary=row.status == PRIMARY,
                )
                for row in sorted(rows, key=lambda r: r.created_at)
            ),
        )


class InMemoryUserDataKeyStore:
    """The same wrapping as the Postgres store, kept in a dict: the default test substrate."""

    def __init__(self, cipher: FieldCipher | None = None) -> None:
        self._wrapper = DataKeyWrapper(cipher or FieldCipher.disabled())
        self._rows: dict[str, list[WrappedKey]] = {}

    async def keyring(self, owner_id: UserId, *, create: bool = True) -> UserKeyring | None:
        rows = self._rows.get(owner_id.value)
        if not rows:
            if not create:
                return None
            rows = self._rows[owner_id.value] = [self._wrapper.new_row(owner_id.value)]
        return self._wrapper.keyring(owner_id, rows)

    async def rotate(self, owner_id: UserId) -> UserKeyring:
        rows = self._rows.setdefault(owner_id.value, [])
        demoted = [
            WrappedKey(r.kid, r.wrapped_key, r.kek_id, DECRYPT_ONLY, r.created_at) for r in rows
        ]
        self._rows[owner_id.value] = demoted + [self._wrapper.new_row(owner_id.value)]
        return self._wrapper.keyring(owner_id, self._rows[owner_id.value])

    async def erase_owner(self, owner_id: UserId) -> int:
        return len(self._rows.pop(owner_id.value, []))
