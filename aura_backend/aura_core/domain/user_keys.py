"""Per-user data keys for the personal-OS fields of the synced tables (ADR-032).

A user's devices and the server share one AES-256 data key (DEK) per user. The server stores it
only wrapped by a server key-encryption key; this module is the unwrapped, in-process view of a
user's keys. The key bytes are deliberately left out of ``repr`` so a stray log line or traceback
cannot print one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .identifiers import UserId

__all__ = ["DATA_KEY_BYTES", "DataKey", "UserKeyring"]

#: AES-256.
DATA_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class DataKey:
    kid: str
    key: bytes = field(repr=False)
    created_at: datetime
    primary: bool

    def __post_init__(self) -> None:
        if len(self.key) != DATA_KEY_BYTES:
            raise ValueError("a data key must be 32 bytes")


@dataclass(frozen=True, slots=True)
class UserKeyring:
    """Every data key one user holds. Exactly one is primary: new values are sealed with it,
    and values sealed under a demoted (``decrypt_only``) key stay readable."""

    owner_id: UserId
    keys: tuple[DataKey, ...]

    def __post_init__(self) -> None:
        if sum(1 for k in self.keys if k.primary) != 1:
            raise ValueError("a keyring must have exactly one primary key")

    @property
    def primary(self) -> DataKey:
        return next(k for k in self.keys if k.primary)

    def get(self, kid: str) -> DataKey | None:
        return next((k for k in self.keys if k.kid == kid), None)
