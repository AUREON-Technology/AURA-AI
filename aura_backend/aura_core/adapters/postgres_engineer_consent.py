"""Postgres :class:`~aura_core.ports.coding.EngineerConsentStore` (module readiness WP1).

Reads and writes ``aura_engineer_consents``, which Alembic revision 0022 creates; the adapter
issues no DDL (one schema authority, CB-20). Shares the container's ``PostgresPool``.

Read once per Engineer turn by primary key. A withdrawal keeps the row (``withdrawn_at`` set) so
the withdrawal is itself on record; only account erasure deletes it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..domain.engineering import EngineerConsent
from ..domain.identifiers import UserId
from .postgres_store import PostgresPool

__all__ = ["PostgresEngineerConsentStore"]

_GET = (
    "SELECT user_id, notice_version, accepted_at, withdrawn_at "
    "FROM aura_engineer_consents WHERE user_id = $1"
)
_ACCEPT = (
    "INSERT INTO aura_engineer_consents (user_id, notice_version, accepted_at, withdrawn_at) "
    "VALUES ($1, $2, $3, NULL) "
    "ON CONFLICT (user_id) DO UPDATE SET notice_version = EXCLUDED.notice_version, "
    "accepted_at = EXCLUDED.accepted_at, withdrawn_at = NULL "
    "RETURNING user_id, notice_version, accepted_at, withdrawn_at"
)
# COALESCE keeps the first withdrawal time: withdrawing twice must not rewrite when it happened.
_WITHDRAW = (
    "UPDATE aura_engineer_consents SET withdrawn_at = COALESCE(withdrawn_at, $2) "
    "WHERE user_id = $1 RETURNING user_id, notice_version, accepted_at, withdrawn_at"
)
_DELETE = "DELETE FROM aura_engineer_consents WHERE user_id = $1"


def _row(row: Any) -> EngineerConsent | None:
    if row is None:
        return None
    return EngineerConsent(
        user_id=UserId(row["user_id"]),
        notice_version=row["notice_version"],
        accepted_at=row["accepted_at"],
        withdrawn_at=row["withdrawn_at"],
    )


class PostgresEngineerConsentStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    async def get(self, user_id: UserId) -> EngineerConsent | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            return _row(await conn.fetchrow(_GET, user_id.value))

    async def accept(self, user_id: UserId, notice_version: str, *, at: datetime) -> EngineerConsent:
        pool = await self._conn()
        async with pool.acquire() as conn:
            consent = _row(await conn.fetchrow(_ACCEPT, user_id.value, notice_version, at))
        assert consent is not None  # RETURNING after an upsert always yields the row
        return consent

    async def withdraw(self, user_id: UserId, *, at: datetime) -> EngineerConsent | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            return _row(await conn.fetchrow(_WITHDRAW, user_id.value, at))

    async def delete_for_owner(self, user_id: UserId) -> int:
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute(_DELETE, user_id.value)
        # asyncpg reports "DELETE <n>".
        return int(status.split()[-1]) if status else 0
