"""Postgres :class:`~aura_core.ports.service_keys.ServiceKeyStore` (AR-4).

Shares the container's ``PostgresPool`` like every other ``postgres_*.py`` adapter. The table is
owned by Alembic revision 0013; ``SERVICE_KEYS_SCHEMA`` is kept textually identical and run with
``IF NOT EXISTS`` on first use, so a migrated database and a self-created one are the same shape.

``get`` runs on every request a service key makes. It is one primary-key lookup with no cache in
front of it -- that is what makes a revocation take effect on the next call, on every worker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.errors import ConflictError, DependencyUnavailableError
from ..domain.service_keys import ServiceKey
from .postgres_store import PostgresPool

__all__ = ["PostgresServiceKeyStore", "SERVICE_KEYS_SCHEMA"]

#: Kept identical to ``alembic/versions/0013_service_keys.py``.
SERVICE_KEYS_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_service_keys (
        key_id           TEXT        PRIMARY KEY CHECK (key_id ~ '^[0-9a-f]{16}$'),
        name             TEXT        NOT NULL,
        digest           TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        routes           TEXT[]      NOT NULL,
        rate_per_minute  INTEGER     NOT NULL CHECK (rate_per_minute BETWEEN 1 AND 600),
        created_at       TIMESTAMPTZ NOT NULL,
        created_by       TEXT        NOT NULL,
        revoked_at       TIMESTAMPTZ NULL,
        revoked_by       TEXT        NULL
    )
    """,
)

_COLUMNS = "key_id, name, digest, routes, rate_per_minute, created_at, created_by, revoked_at, revoked_by"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class PostgresServiceKeyStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)
        self._schema_ready = False

    async def _conn(self) -> Any:
        pool = await self._pool._get_pool()
        if not self._schema_ready:
            try:
                async with pool.acquire() as conn:
                    for statement in SERVICE_KEYS_SCHEMA:
                        await conn.execute(statement)
            except Exception as exc:
                raise DependencyUnavailableError(
                    f"could not create the service key schema: {type(exc).__name__}", dependency="postgres"
                ) from exc
            self._schema_ready = True
        return pool

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
