"""Where a person's notification inbox and its settings are kept (``domain/notifications.py``)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from ..domain.identifiers import UserId
from ..domain.notifications import Notification, NotificationSettings

__all__ = ["NotificationStore", "NotificationSettingsStore", "MAX_PER_OWNER"]

#: The most notifications one person's inbox holds; adding past it drops the oldest (read ones first).
MAX_PER_OWNER = 500


@runtime_checkable
class NotificationStore(Protocol):
    """Every statement is scoped to the owner: another person's id answers as a missing one."""

    async def add(self, notification: Notification) -> bool:
        """Store it. ``False`` when its ``dedupe_key`` is already stored for this owner (nothing
        added). Keeps the inbox within :data:`MAX_PER_OWNER`."""
        ...

    async def get(self, owner_id: UserId, notification_id: str) -> Notification | None:
        ...

    async def list(
        self,
        owner_id: UserId,
        *,
        unread_only: bool = False,
        kind: str | None = None,
        before: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> Sequence[Notification]:
        """Newest first by ``(created_at, id)``; ``before`` is the last row of the previous page."""
        ...

    async def unread_count(self, owner_id: UserId) -> int:
        ...

    async def mark_read(self, owner_id: UserId, ids: Sequence[str] | None, *, at: datetime) -> int:
        """Mark the owner's listed notifications read (``None`` = all of them); returns how many
        changed. One already read keeps its first ``read_at``."""
        ...

    async def delete(self, owner_id: UserId, notification_id: str) -> bool:
        ...

    async def delete_for_owner(self, owner_id: UserId) -> int:
        """Account erasure."""
        ...


@runtime_checkable
class NotificationSettingsStore(Protocol):
    async def get(self, owner_id: UserId) -> NotificationSettings | None:
        ...

    async def put(self, owner_id: UserId, settings: NotificationSettings, *, at: datetime) -> NotificationSettings:
        ...

    async def delete(self, owner_id: UserId) -> bool:
        ...
