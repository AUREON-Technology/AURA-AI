"""Which paired computer is connected right now, visible to every gateway worker.

A desktop daemon's socket lives in exactly one worker (``adapters/host_agent.py``); the registry's
relay lets *calls* reach it from any worker, but says nothing about **which device** the socket
belongs to or since when. That is recorded here, in the shared cache, by the worker that holds the
socket: one small record per person, written when the daemon connects, refreshed while it stays,
removed when it leaves, and expiring on its own if the worker dies before it can remove it.

**It is a hint, never the truth about being connected.** ``connected`` always comes from the
registry (a real session); this only adds *which device* and *since when*. The cache is best-effort
by contract (``ports/storage.py::Cache``), so a miss means "unknown", not "not connected". The
record holds an opaque device id and a timestamp: nothing about what the computer is doing.

A newer connection of the same person replaces the record, and the older socket's cleanup must not
delete the newer one's: each connection carries its own id, and refresh and clear only act on a
record that still carries it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..observability.logging import get_logger

logger = get_logger("execution.runtime_presence")

__all__ = ["RuntimePresence", "PRESENCE_TTL_S"]

_PREFIX = "runtime_presence:"
#: Longer than the socket's credential recheck (60 s by default), which is what refreshes it, so one
#: missed refresh does not make a connected computer look gone. A worker that dies leaves the record
#: to expire after this.
PRESENCE_TTL_S = 180


class RuntimePresence:
    def __init__(self, cache: Any = None, *, ttl_s: int = PRESENCE_TTL_S) -> None:
        self._cache = cache
        self._ttl_s = ttl_s

    @staticmethod
    def _key(user_id: str) -> str:
        return f"{_PREFIX}{user_id}"

    async def announce(
        self, user_id: str, *, connection_id: str, device_id: str | None, connected_at: datetime,
    ) -> None:
        """Record that this person's daemon connected (replacing any earlier record)."""
        record = {"k": connection_id, "d": device_id, "c": connected_at.isoformat()}
        await self._set(self._key(user_id), record)

    async def refresh(self, user_id: str, connection_id: str) -> None:
        """Keep the record alive while the socket stays up. Does nothing if a newer connection has
        taken the record over."""
        key = self._key(user_id)
        record = await self._read(key)
        if record is not None and record.get("k") == connection_id:
            await self._set(key, record)

    async def clear(self, user_id: str, connection_id: str) -> None:
        """Remove the record, but only if it is still this connection's."""
        key = self._key(user_id)
        record = await self._read(key)
        if record is None or record.get("k") != connection_id or self._cache is None:
            return
        try:
            await self._cache.delete(key)
        except Exception:  # noqa: BLE001 -- the record expires on its own
            logger.debug("could not clear runtime presence", exc_info=True)

    async def get(self, user_id: str) -> dict[str, Any] | None:
        """``{"device_id", "connected_since"}`` for this person's connected computer, or ``None``
        when nothing is recorded (which does *not* mean it is not connected: see the module notes)."""
        record = await self._read(self._key(user_id))
        if record is None:
            return None
        return {"device_id": record.get("d"), "connected_since": record.get("c")}

    async def _set(self, key: str, record: dict[str, Any]) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set(key, json.dumps(record).encode("utf-8"), ttl_s=self._ttl_s)
        except Exception:  # noqa: BLE001 -- presence is a hint; a cache blip changes nothing
            logger.debug("could not write runtime presence", exc_info=True)

    async def _read(self, key: str) -> dict[str, Any] | None:
        if self._cache is None:
            return None
        try:
            raw = await self._cache.get(key)
            if not raw:
                return None
            record = json.loads(raw)
        except Exception:  # noqa: BLE001 -- unreadable means unknown
            logger.debug("could not read runtime presence", exc_info=True)
            return None
        return record if isinstance(record, dict) else None
