"""Postgres implementation of the ``UserDataKeyStore`` port (ADR-032).

Reads and writes ``aura_user_data_keys``, which Alembic migration ``0019`` creates; like
``postgres_goals.py`` this adapter never issues DDL. ``owner_id`` references
``users(auth_subject) ON DELETE CASCADE``, so erasing a user destroys their keys with the row.

Only wrapped keys are stored (see ``user_data_keys.py``). At most one ``primary`` per owner is
enforced by a partial unique index, so two first requests racing to create a key cannot both
succeed: the loser's insert does nothing and it reads the winner's key.
"""

from __future__ import annotations

import logging
from typing import Any

from ..domain.errors import AuraError, DependencyUnavailableError
from ..domain.identifiers import UserId
from ..domain.user_keys import UserKeyring
from .field_crypto import FieldCipher
from .postgres_app_users import ensure_user_id
from .postgres_store import PostgresPool
from .user_data_keys import DECRYPT_ONLY, PRIMARY, DataKeyWrapper, WrappedKey

logger = logging.getLogger(__name__)

__all__ = ["PostgresUserDataKeyStore"]

_SELECT = (
    "SELECT kid, wrapped_key, kek_id, status, created_at FROM aura_user_data_keys "
    "WHERE owner_id = $1 ORDER BY created_at"
)
_INSERT = (
    "INSERT INTO aura_user_data_keys (owner_id, kid, wrapped_key, kek_id, status, created_at) "
    "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING"
)


def _unavailable(message: str) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres")


class PostgresUserDataKeyStore:
    def __init__(
        self, url: str, *, pool: PostgresPool | None = None, cipher: FieldCipher | None = None,
    ) -> None:
        self._pool = pool or PostgresPool(url)
        self._wrapper = DataKeyWrapper(cipher or FieldCipher.disabled())

    @staticmethod
    def _rows(records: list[Any]) -> list[WrappedKey]:
        return [
            WrappedKey(
                kid=r["kid"], wrapped_key=r["wrapped_key"], kek_id=r["kek_id"],
                status=r["status"], created_at=r["created_at"],
            )
            for r in records
        ]

    async def _insert(self, conn: Any, owner: str, row: WrappedKey) -> bool:
        status = await conn.execute(
            _INSERT, owner, row.kid, row.wrapped_key, row.kek_id, row.status, row.created_at
        )
        return status.endswith(" 1")

    async def keyring(self, owner_id: UserId, *, create: bool = True) -> UserKeyring | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = self._rows(await conn.fetch(_SELECT, owner_id.value))
                if not rows and create:
                    async with conn.transaction():
                        await ensure_user_id(conn, owner_id.value)
                        await self._insert(conn, owner_id.value, self._wrapper.new_row(owner_id.value))
                    rows = self._rows(await conn.fetch(_SELECT, owner_id.value))
        except AuraError:
            raise
        except Exception as exc:
            raise _unavailable(f"could not read the user's data keys: {type(exc).__name__}") from exc
        if not rows:
            return None
        return self._wrapper.keyring(owner_id, rows)

    async def rotate(self, owner_id: UserId) -> UserKeyring:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await ensure_user_id(conn, owner_id.value)
                    await conn.execute(
                        "UPDATE aura_user_data_keys SET status = $2 "
                        "WHERE owner_id = $1 AND status = $3",
                        owner_id.value, DECRYPT_ONLY, PRIMARY,
                    )
                    # A kid is 32 random bits, unique per owner; retry the vanishing collision.
                    for _ in range(3):
                        if await self._insert(conn, owner_id.value, self._wrapper.new_row(owner_id.value)):
                            break
                    else:
                        raise RuntimeError("could not allocate a new data key id")
                rows = self._rows(await conn.fetch(_SELECT, owner_id.value))
        except AuraError:
            raise
        except Exception as exc:
            raise _unavailable(f"could not rotate the user's data key: {type(exc).__name__}") from exc
        return self._wrapper.keyring(owner_id, rows)
