"""A bounded, TTL'd, per-owner append-only log built on nothing but the ``Cache`` port.

Why this exists: ADR-043 adds three small histories (a user's usage records, their agent tasks,
the run-goal previews) and may not add a table (Alembic is the only schema authority, and a new
table needs a real-Postgres round trip). The cache port has ``get``/``set``/``incr``/
``get_many`` and no list or scan, so the log is a *sequence counter plus numbered slots*:

* ``append`` takes the next number with ``incr`` -- atomic across gateway workers on Redis, which
  is the property a read-modify-write list would lose -- and writes the entry under that number
  with the TTL;
* ``recent`` reads the counter and fetches the last ``limit`` numbers with one ``get_many``.

An entry that has expired, or one whose writer has taken its number but not yet stored it, is
simply absent from ``recent``: a log is a best-effort history, never a source of truth for
anything that must not be lost (and nothing here decides money or access).

Keys carry a digest of the owner, never the owner's id, so a key in a Redis dump names nobody and
one owner's numbers can never collide with another's.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..observability.logging import get_logger

__all__ = ["CacheLog", "owner_digest"]

logger = get_logger("history.cache_log")

#: The counter outlives every slot: ``incr`` applies its TTL only when it creates the key, so a
#: counter that expired *before* its slots would restart at 1 and overwrite live entries.
_COUNTER_TTL_S = 400 * 86_400


def owner_digest(owner: str) -> str:
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:24]


class CacheLog:
    def __init__(self, cache: Any, *, namespace: str, ttl_s: int, window: int) -> None:
        self._cache = cache
        self._ns = namespace
        self._ttl_s = int(ttl_s)
        self._window = max(1, int(window))

    def _seq_key(self, owner: str) -> str:
        return f"{self._ns}:{owner_digest(owner)}:seq"

    def _slot_key(self, owner: str, number: int) -> str:
        return f"{self._ns}:{owner_digest(owner)}:{number}"

    async def append(self, owner: str, entry: Mapping[str, Any]) -> int:
        """Store ``entry`` as this owner's newest and return its number. Raises on a cache
        failure (the Redis adapter's ``incr`` does) -- callers that must not fail wrap it."""
        number = await self._cache.incr(self._seq_key(owner), ttl_s=_COUNTER_TTL_S)
        blob = json.dumps(dict(entry), separators=(",", ":"), default=str).encode("utf-8")
        await self._cache.set(self._slot_key(owner, number), blob, ttl_s=self._ttl_s)
        return int(number)

    async def recent(self, owner: str, limit: int | None = None) -> list[dict[str, Any]]:
        """This owner's newest entries, newest first, at most ``limit`` (default the window)."""
        want = min(self._window, int(limit)) if limit is not None else self._window
        raw = await self._cache.get(self._seq_key(owner))
        if not raw:
            return []
        try:
            newest = int(raw)
        except (TypeError, ValueError):
            return []
        numbers = list(range(newest, max(0, newest - want), -1))
        keys = [self._slot_key(owner, n) for n in numbers]
        found = await self._cache.get_many(keys)
        out: list[dict[str, Any]] = []
        for key in keys:
            blob = found.get(key)
            if blob is None:
                continue
            try:
                decoded = json.loads(blob)
            except ValueError:
                logger.debug("skipping an unreadable log entry")
                continue
            if isinstance(decoded, dict):
                out.append(decoded)
        return out

    @property
    def window(self) -> int:
        return self._window
