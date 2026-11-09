"""Postgres :class:`~aura_core.ports.service_keys.ServiceKeyStore` (AR-4).

Shares the container's ``PostgresPool`` like every other ``postgres_*.py`` adapter. The table is
owned by Alembic revision 0013; the adapter issues no DDL (one schema authority, CB-20).

``get`` runs on every request a service key makes. It is one primary-key lookup with no cache in
front of it -- that is what makes a revocation take effect on the next call, on every worker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.errors import ConflictError
from ..domain.service_keys import ServiceKey
from .postgres_store import PostgresPool

__all__ = ["PostgresServiceKeyStore"]


_COLUMNS = "key_id, name, digest, routes, rate_per_minute, created_at, created_by, revoked_at, revoked_by"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class PostgresServiceKeyStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    @staticmethod
    def _key(row: Mapping[str, Any]) -> ServiceKey:
        return ServiceKey(
            key_id=row["key_id"], name=row["name"], digest=row["digest"], routes=frozenset(row["routes"]),
            rate_per_minute=int(row["rate_per_minute"]), created_at=_utc(row["created_at"]),
            created_by=row["created_by"], revoked_at=_utc(row["revoked_at"]), revoked_by=row["revoked_by"],
        )

    async def put(self, key: ServiceKey) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            status = await conn.execute(
                f"INSERT INTO aura_service_keys ({_COLUMNS}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                "ON CONFLICT (key_id) DO NOTHING",
                key.key_id, key.name, key.digest, sorted(key.routes), key.rate_per_minute,
                key.created_at, key.created_by, key.revoked_at, key.revoked_by,
            )
        if status.rsplit(" ", 1)[-1] != "1":
            raise ConflictError("a service key with that id already exists")

    async def get(self, key_id: str) -> ServiceKey | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM aura_service_keys WHERE key_id = $1", key_id)
        return self._key(row) if row else None

    async def list(self) -> list[ServiceKey]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT {_COLUMNS} FROM aura_service_keys ORDER BY created_at, key_id")
        return [self._key(r) for r in rows]

    async def revoke(self, key_id: str, *, at: datetime, by: str) -> ServiceKey | None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            # COALESCE keeps the first revocation's time and author: revoking twice is a no-op.
            row = await conn.fetchrow(
                "UPDATE aura_service_keys SET revoked_at = COALESCE(revoked_at, $2), "
                "revoked_by = COALESCE(revoked_by, $3) WHERE key_id = $1 "
                f"RETURNING {_COLUMNS}",
                key_id, at, by,
            )
        return self._key(row) if row else None
