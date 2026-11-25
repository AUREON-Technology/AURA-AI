"""Postgres notification stores (``aura_notifications`` and ``aura_notification_settings``, migration 0040).

The adapters issue no DDL (one schema authority, CB-20) and share the container's ``PostgresPool``.
Every statement carries the owner. ``add`` inserts and trims the inbox to its cap in one transaction.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from ..domain.identifiers import UserId
from ..domain.notifications import Notification, NotificationSettings
from ..ports.notifications import MAX_PER_OWNER
from .postgres_store import PostgresPool

__all__ = ["PostgresNotificationStore", "PostgresNotificationSettingsStore"]

_COLUMNS = "id, owner_id, kind, params, created_at, read_at, dedupe_key"
_INSERT = (
    "INSERT INTO aura_notifications (id, owner_id, kind, params, created_at, read_at, dedupe_key) "
    "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) ON CONFLICT DO NOTHING"
)
# Read rows go first, then the oldest: the same order the in-memory store trims in.
_TRIM = (
    "DELETE FROM aura_notifications WHERE id IN ("
    "SELECT id FROM aura_notifications WHERE owner_id = $1 "
    "ORDER BY (read_at IS NULL), created_at, id OFFSET 0 LIMIT GREATEST("
    "(SELECT count(*) FROM aura_notifications WHERE owner_id = $1) - $2, 0))"
)
_GET = f"SELECT {_COLUMNS} FROM aura_notifications WHERE owner_id = $1 AND id = $2"
_UNREAD = "SELECT count(*) FROM aura_notifications WHERE owner_id = $1 AND read_at IS NULL"
_DELETE = "DELETE FROM aura_notifications WHERE owner_id = $1 AND id = $2"
_DELETE_OWNER = "DELETE FROM aura_notifications WHERE owner_id = $1"


def _row(row: Any) -> Notification:
    params = row["params"]
    if isinstance(params, str):
        params = json.loads(params)
    return Notification(
        id=row["id"], owner_id=UserId(row["owner_id"]), kind=row["kind"], params=dict(params),
        created_at=row["created_at"], read_at=row["read_at"], dedupe_key=row["dedupe_key"],
    )


class PostgresNotificationStore:
    def __init__(self, dsn: str, *, pool: PostgresPool | None = None, max_per_owner: int = MAX_PER_OWNER) -> None:
        self._pool = pool or PostgresPool(dsn)
        self._max = max_per_owner

    async def add(self, notification: Notification) -> bool:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    _INSERT, notification.id, notification.owner_id.value, notification.kind,
                    json.dumps(dict(notification.params), default=str), notification.created_at,
                    notification.read_at, notification.dedupe_key,
                )
                if int(result.rsplit(" ", 1)[-1]) == 0:
                    return False
                await conn.execute(_TRIM, notification.owner_id.value, self._max)
        return True

    async def get(self, owner_id: UserId, notification_id: str) -> Notification | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_GET, owner_id.value, notification_id)
        return _row(row) if row else None

    async def list(
        self,
        owner_id: UserId,
        *,
        unread_only: bool = False,
        kind: str | None = None,
        before: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> Sequence[Notification]:
        clauses, args = ["owner_id = $1"], [owner_id.value]
        if unread_only:
            clauses.append("read_at IS NULL")
        if kind is not None:
            args.append(kind)
            clauses.append(f"kind = ${len(args)}")
        if before is not None:
            args.extend(before)
            clauses.append(f"(created_at, id) < (${len(args) - 1}, ${len(args)})")
        args.append(limit)
        sql = (
            f"SELECT {_COLUMNS} FROM aura_notifications WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at DESC, id DESC LIMIT ${len(args)}"
        )
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row(r) for r in rows]

    async def unread_count(self, owner_id: UserId) -> int:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            return int(await conn.fetchval(_UNREAD, owner_id.value))

    async def mark_read(self, owner_id: UserId, ids: Sequence[str] | None, *, at: datetime) -> int:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            if ids is None:
                result = await conn.execute(
                    "UPDATE aura_notifications SET read_at = $2 WHERE owner_id = $1 AND read_at IS NULL",
                    owner_id.value, at,
                )
            else:
                unique = list(dict.fromkeys(ids))
                if not unique:
                    return 0
                result = await conn.execute(
                    "UPDATE aura_notifications SET read_at = $3 "
                    "WHERE owner_id = $1 AND id = ANY($2::text[]) AND read_at IS NULL",
                    owner_id.value, unique, at,
                )
        return int(result.rsplit(" ", 1)[-1])

    async def delete(self, owner_id: UserId, notification_id: str) -> bool:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(_DELETE, owner_id.value, notification_id)
        return int(result.rsplit(" ", 1)[-1]) > 0

    async def delete_for_owner(self, owner_id: UserId) -> int:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(_DELETE_OWNER, owner_id.value)
        return int(result.rsplit(" ", 1)[-1])


class PostgresNotificationSettingsStore:
    def __init__(self, dsn: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(dsn)

    async def get(self, owner_id: UserId) -> NotificationSettings | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT disabled_kinds, updated_at FROM aura_notification_settings WHERE owner_id = $1",
                owner_id.value,
            )
        if row is None:
            return None
        kinds = row["disabled_kinds"]
        if isinstance(kinds, str):
            kinds = json.loads(kinds)
        return NotificationSettings(disabled_kinds=frozenset(kinds), updated_at=row["updated_at"])

    async def put(self, owner_id: UserId, settings: NotificationSettings, *, at: datetime) -> NotificationSettings:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO aura_notification_settings (owner_id, disabled_kinds, updated_at) VALUES ($1, $2::jsonb, $3) "
                "ON CONFLICT (owner_id) DO UPDATE SET disabled_kinds = EXCLUDED.disabled_kinds, updated_at = EXCLUDED.updated_at",
                owner_id.value, json.dumps(sorted(settings.disabled_kinds)), at,
            )
        return NotificationSettings(disabled_kinds=settings.disabled_kinds, updated_at=at)

    async def delete(self, owner_id: UserId) -> bool:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM aura_notification_settings WHERE owner_id = $1", owner_id.value)
        return int(result.rsplit(" ", 1)[-1]) > 0
