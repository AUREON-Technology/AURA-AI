"""Tell a person's registered phones that something needs them (ADR-060).

Used for one thing today: a turn is waiting for their approval. The message is generic on purpose
(see ``domain/push.py``); with ``include_detail`` the held action's one-line headline is added, which
the operator must opt into because it will be shown on a lock screen.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.push import MAX_TOKEN_CHARS, PLATFORMS, PushDevice, PushMessage
from ..observability.logging import get_logger
from ..ports.push import PushDeviceStore, PushSender

__all__ = ["PushNotifier"]

logger = get_logger("push")

#: One approval push per user per this many seconds: a turn that holds five calls, or two turns
#: finishing together, is one nudge, not a burst.
MIN_INTERVAL_S = 20.0


class PushNotifier:
    def __init__(
        self, store: PushDeviceStore, sender: PushSender | None, *, include_detail: bool = False,
        clock: Callable[[], float] = time.monotonic, metrics: Any = None,
    ) -> None:
        self._store = store
        self._sender = sender
        self._detail = include_detail
        self._clock = clock
        self._metrics = metrics
        self._last: dict[str, float] = {}

    @property
    def delivering(self) -> bool:
        """Whether a sender is configured. Registration works either way (tokens wait for one)."""
        return self._sender is not None

    async def register(self, user_id: str, device_id: str, token: str, platform: str) -> PushDevice:
        device_id, token, platform = device_id.strip(), token.strip(), platform.strip().lower()
        if not device_id or len(device_id) > 128:
            raise ValueError("device_id must be 1-128 characters")
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise ValueError("token must be 1-%d characters" % MAX_TOKEN_CHARS)
        if platform not in PLATFORMS:
            raise ValueError("platform must be one of: " + ", ".join(PLATFORMS))
        device = PushDevice(user_id, device_id, token, platform, datetime.now(timezone.utc))
        await self._store.upsert(device)
        return device

    async def unregister(self, user_id: str, device_id: str) -> bool:
        return await self._store.remove(user_id, device_id)

    async def notify_approval_needed(self, user_id: str, *, headline: str = "", turn_id: str = "",
                                     session_id: str = "") -> int:
        """Push "something is waiting for you" to the user's devices; how many accepted it. Never
        raises: a push is a nicety and must not break the turn that triggered it."""
        if self._sender is None:
            return 0
        now = self._clock()
        last = self._last.get(user_id)
        if last is not None and now - last < MIN_INTERVAL_S:
            return 0
        self._last[user_id] = now
        if len(self._last) > 5000:  # bounded: forget the oldest half
            for key in sorted(self._last, key=self._last.get)[:2500]:
                self._last.pop(key, None)
        body = " ".join(headline.split())[:100] if (self._detail and headline.strip()) else "A step is waiting for your approval."
        message = PushMessage(
            title="AURA needs you", body=body, collapse_key="approval",
            data={"type": "approval_needed", "turn_id": turn_id, "session_id": session_id},
        )
        return await self._deliver(user_id, message)

    async def _deliver(self, user_id: str, message: PushMessage) -> int:
        delivered = 0
        try:
            devices = await self._store.for_user(user_id)
            for device in devices:
                outcome = await self._sender.send(device, message)
                if outcome.status == "delivered":
                    delivered += 1
                elif outcome.status == "invalid_token":
                    await self._store.remove_token(device.token)
                else:
                    logger.warning("push send failed (%s)", outcome.detail or "error")
        except Exception:
            logger.warning("push delivery failed", exc_info=False)
        return delivered
