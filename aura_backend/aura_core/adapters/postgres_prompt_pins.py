"""Postgres :class:`~aura_core.ports.prompts.PromptPinStore` (INT-004).

Reads and writes ``aura_prompt_pins``, which Alembic revision 0021 creates; the adapter issues no
DDL (one schema authority, CB-20). Shares the container's ``PostgresPool``.

Read by each worker's background pin sync (every ``PIN_SYNC_INTERVAL_S``) and by
``/admin/status`` -- never per turn -- and the table holds one row per pinned template.
"""

from __future__ import annotations

from typing import Any, Mapping

from .postgres_store import PostgresPool

__all__ = ["PostgresPromptPinStore"]

_LOAD = "SELECT template_id, version FROM aura_prompt_pins"
_UPSERT = (
    "INSERT INTO aura_prompt_pins (template_id, version, pinned_by, pinned_at) "
    "VALUES ($1, $2, $3, now()) "
    "ON CONFLICT (template_id) DO UPDATE SET version = EXCLUDED.version, "
    "pinned_by = EXCLUDED.pinned_by, pinned_at = EXCLUDED.pinned_at"
)
_DELETE = "DELETE FROM aura_prompt_pins WHERE template_id = $1 RETURNING template_id"


class PostgresPromptPinStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    async def load_all(self) -> Mapping[str, str]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_LOAD)
        return {row["template_id"]: row["version"] for row in rows}

    async def set(self, template_id: str, version: str, *, pinned_by: str | None = None) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            await conn.execute(_UPSERT, template_id, version, pinned_by)

    async def delete(self, template_id: str) -> bool:
        pool = await self._conn()
        async with pool.acquire() as conn:
            removed = await conn.fetchval(_DELETE, template_id)
        return removed is not None
