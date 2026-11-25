"""In-memory notification stores (real implementations: the default test run and a single-process
deployment use them; ``tests/load/test_notifications_postgres.py`` holds Postgres to the same outcomes)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Sequence

from ..domain.identifiers import UserId
from ..domain.notifications import Notification, NotificationSettings
from ..ports.notifications import MAX_PER_OWNER

__all__ = ["InMemoryNotificationStore", "InMemoryNotificationSettingsStore"]


class InMemoryNotificationStore:
    def __init__(self, *, max_per_owner: int = MAX_PER_OWNER) -> None:
        self._rows: dict[str, Notification] = {}
        self._max = max_per_owner
        self._lock = asyncio.Lock()

    async def add(self, notification: Notification) -> bool:
        async with self._lock:
            if notification.dedupe_key is not None and any(
                r.owner_id == notification.owner_id and r.dedupe_key == notification.dedupe_key
                for r in self._rows.values()
            ):
                return False
            self._rows[notification.id] = notification
            mine = sorted(
                (r for r in self._rows.values() if r.owner_id == notification.owner_id),
                key=lambda r: (r.read_at is None, r.created_at, r.id),  # read first, then oldest
            )
            for row in mine[: max(0, len(mine) - self._max)]:
                del self._rows[row.id]
            return True

    async def get(self, owner_id: UserId, notification_id: str) -> Notification | None:
        row = self._rows.get(notification_id)
        return row if row is not None and row.owner_id == owner_id else None

    async def list(
        self,
        owner_id: UserId,
        *,
        unread_only: bool = False,
        kind: str | None = None,
        before: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> Sequence[Notification]:
        rows = [
            r for r in self._rows.values()
            if r.owner_id == owner_id
            and (not unread_only or r.read_at is None)
            and (kind is None or r.kind == kind)
            and (before is None or (r.created_at, r.id) < before)
        ]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return rows[:limit]

    async def unread_count(self, owner_id: UserId) -> int:
        return sum(1 for r in self._rows.values() if r.owner_id == owner_id and r.read_at is None)

    async def mark_read(self, owner_id: UserId, ids: Sequence[str] | None, *, at: datetime) -> int:
        changed = 0
        async with self._lock:
            wanted = None if ids is None else set(ids)
            for row in list(self._rows.values()):
                if row.owner_id != owner_id or row.read_at is not None:
                    continue
                if wanted is None or row.id in wanted:
                    self._rows[row.id] = replace(row, read_at=at)
                    changed += 1
        return changed

    async def delete(self, owner_id: UserId, notification_id: str) -> bool:
        async with self._lock:
            row = self._rows.get(notification_id)
            if row is None or row.owner_id != owner_id:
                return False
            del self._rows[notification_id]
            return True

    async def delete_for_owner(self, owner_id: UserId) -> int:
        async with self._lock:
            mine = [k for k, r in self._rows.items() if r.owner_id == owner_id]
            for key in mine:
                del self._rows[key]
        return len(mine)


class InMemoryNotificationSettingsStore:
    def __init__(self) -> None:
        self._rows: dict[str, NotificationSettings] = {}

    async def get(self, owner_id: UserId) -> NotificationSettings | None:
        return self._rows.get(owner_id.value)

    async def put(self, owner_id: UserId, settings: NotificationSettings, *, at: datetime) -> NotificationSettings:
        stored = replace(settings, updated_at=at)
        self._rows[owner_id.value] = stored
        return stored

    async def delete(self, owner_id: UserId) -> bool:
        return self._rows.pop(owner_id.value, None) is not None
