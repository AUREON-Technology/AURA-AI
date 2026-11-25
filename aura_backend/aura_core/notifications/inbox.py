"""Puts notifications in a person's inbox (``domain/notifications.py``).

``notify`` is what the producers call -- the approval hold, billing, the plan meter. It **never
raises**: an inbox that is down, a settings read that fails or a bad parameter must not fail the
approval, the payment or the turn that triggered it (the same rule as the email ``Notifier``). It
checks the person's settings first: a kind they switched off is not stored at all.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from ..domain.identifiers import UserId
from ..domain.notifications import Notification, NotificationSettings

__all__ = ["Inbox"]

logger = logging.getLogger("aura.notifications.inbox")


class Inbox:
    def __init__(self, store: Any, settings: Any, clock: Any) -> None:
        self._store = store
        self._settings = settings
        self._clock = clock

    async def notify(
        self, owner_id: UserId | str, kind: str, params: Mapping[str, Any] | None = None, *,
        dedupe_key: str | None = None,
    ) -> bool:
        """Store one notification; ``True`` when it was stored (not switched off, not a duplicate)."""
        try:
            owner = owner_id if isinstance(owner_id, UserId) else UserId(str(owner_id))
            preferences = await self._settings.get(owner) or NotificationSettings()
            if not preferences.enabled(kind):
                return False
            notification = Notification(
                id=uuid.uuid4().hex, owner_id=owner, kind=kind, params=dict(params or {}),
                created_at=self._clock.now(), dedupe_key=dedupe_key,
            )
            return await self._store.add(notification)
        except Exception:  # noqa: BLE001 -- see the module notes
            logger.warning("could not add a notification (%s)", kind, exc_info=True)
            return False
