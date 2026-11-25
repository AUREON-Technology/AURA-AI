"""Postgres :class:`~aura_core.ports.push.PushDeviceStore` over ``aura_push_devices`` (revision 0035;
no DDL here, CB-20). Shares the container's ``PostgresPool``."""

from __future__ import annotations

from typing import Any, Sequence

from ..domain.push import MAX_DEVICES_PER_USER, PushDevice
from .postgres_store import PostgresPool

__all__ = ["PostgresPushDeviceStore"]

_DROP_TOKEN_ELSEWHERE = "DELETE FROM aura_push_devices WHERE token = $1 AND NOT (user_id = $2 AND device_id = $3)"
_UPSERT = (
    "INSERT INTO aura_push_devices (user_id, device_id, token, platform, updated_at) "
    "VALUES ($1, $2, $3, $4, $5) "
    "ON CONFLICT (user_id, device_id) DO UPDATE SET token = EXCLUDED.token, "
    "platform = EXCLUDED.platform, updated_at = EXCLUDED.updated_at"
)
_TRIM = (
    "DELETE FROM aura_push_devices WHERE user_id = $1 AND device_id IN ("
    "SELECT device_id FROM aura_push_devices WHERE user_id = $1 ORDER BY updated_at DESC OFFSET $2)"
)
_REMOVE = "DELETE FROM aura_push_devices WHERE user_id = $1 AND device_id = $2"
_REMOVE_TOKEN = "DELETE FROM aura_push_devices WHERE token = $1"
_FOR_USER = (
    "SELECT user_id, device_id, token, platform, updated_at FROM aura_push_devices "
    "WHERE user_id = $1 ORDER BY updated_at DESC"
)


class PostgresPushDeviceStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    async def upsert(self, device: PushDevice) -> None:
        pool = await self._conn()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(_DROP_TOKEN_ELSEWHERE, device.token, device.user_id, device.device_id)
                await conn.execute(_UPSERT, device.user_id, device.device_id, device.token,
                                   device.platform, device.updated_at)
                await conn.execute(_TRIM, device.user_id, MAX_DEVICES_PER_USER)

    async def remove(self, user_id: str, device_id: str) -> bool:
        pool = await self._conn()
        async with pool.acquire() as conn:
            return str(await conn.execute(_REMOVE, user_id, device_id)).endswith(" 1")

    async def remove_token(self, token: str) -> int:
        pool = await self._conn()
        async with pool.acquire() as conn:
            tag = str(await conn.execute(_REMOVE_TOKEN, token))
        try:
            return int(tag.rsplit(" ", 1)[-1])
        except ValueError:
            return 0

    async def for_user(self, user_id: str) -> Sequence[PushDevice]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_FOR_USER, user_id)
        return [PushDevice(r["user_id"], r["device_id"], r["token"], r["platform"], r["updated_at"]) for r in rows]
