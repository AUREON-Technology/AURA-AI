"""Postgres :class:`~aura_core.ports.model_settings.ModelSettingsStore`.

Reads and writes ``aura_user_model_settings``, which Alembic revision 0038 creates; the adapter issues
no DDL (one schema authority, CB-20). Shares the container's ``PostgresPool``. Every statement
carries the owner.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..domain.identifiers import UserId
from ..domain.model_settings import ModelSettings
from .postgres_store import PostgresPool

__all__ = ["PostgresModelSettingsStore"]

_COLUMNS = "routing_policy, require_local, temperature, max_output_tokens, updated_at"
_GET = f"SELECT {_COLUMNS} FROM aura_user_model_settings WHERE owner_id = $1"
_PUT = (
    "INSERT INTO aura_user_model_settings (owner_id, routing_policy, require_local, temperature, "
    "max_output_tokens, updated_at) VALUES ($1, $2, $3, $4, $5, $6) "
    "ON CONFLICT (owner_id) DO UPDATE SET routing_policy = EXCLUDED.routing_policy, "
    "require_local = EXCLUDED.require_local, temperature = EXCLUDED.temperature, "
    "max_output_tokens = EXCLUDED.max_output_tokens, updated_at = EXCLUDED.updated_at "
    f"RETURNING {_COLUMNS}"
)
_DELETE = "DELETE FROM aura_user_model_settings WHERE owner_id = $1"


def _row(row: Any) -> ModelSettings:
    return ModelSettings(
        routing_policy=row["routing_policy"], require_local=row["require_local"],
        temperature=float(row["temperature"]) if row["temperature"] is not None else None,
        max_output_tokens=row["max_output_tokens"], updated_at=row["updated_at"],
    )


class PostgresModelSettingsStore:
    def __init__(self, dsn: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(dsn)

    async def get(self, owner_id: UserId) -> ModelSettings | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_GET, owner_id.value)
        return _row(row) if row else None

    async def put(self, owner_id: UserId, settings: ModelSettings, *, at: datetime) -> ModelSettings:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                _PUT, owner_id.value, settings.routing_policy, settings.require_local,
                settings.temperature, settings.max_output_tokens, at,
            )
        return _row(row)

    async def delete(self, owner_id: UserId) -> bool:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(_DELETE, owner_id.value)
        return int(result.rsplit(" ", 1)[-1]) > 0
