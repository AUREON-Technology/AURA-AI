"""The user's timezone, so that "today" means the user's day (audit F-17).

The client stores a daily record (``health_records.record_date``) at *local* midnight, converted
to UTC: for a user in UTC+3, "31 October" is ``2026-10-30T21:00Z``. Insights and the daily briefing
used to compute "today" as the UTC day, so for that user the record fell before "today" from
21:00 UTC onwards, and the hydration rule said "0 ml of water logged today" every evening. The
3 pm cut-off it applies was also 3 pm UTC.

**Where the timezone comes from.** No column holds it. ``users`` has no settings or preferences
field, and adding one is a migration, which this change deliberately avoids. So:

1. The client sends it on the insights requests it already makes, in the ``X-Timezone`` header.
   That request's own computation uses it directly.
2. The value is also stored in the shared cache (Redis in both deployments) under the user's id,
   with a long TTL refreshed on every request. The scheduled hourly and daily jobs read it from
   there.
3. With neither, or with a value that does not parse, the day is the UTC day, as before.

Losing the cache entry therefore costs nothing worse than the old behaviour, and only until the
client next opens the Today screen. A ``users.timezone`` column (one nullable ``VARCHAR(64)``)
would make step 2 durable. That is the follow-up migration; ``remember_timezone`` and
``stored_timezone`` are the only two places that would change.

Accepted values are an IANA name (``"Asia/Riyadh"``), which is DST-correct on every date, or a
fixed UTC offset (``"+03:00"``, ``"-0500"``, ``"UTC+3"``). The offset form exists because the
client has no IANA source without a new plugin dependency, while ``DateTime.timeZoneOffset`` is
always available. A fixed offset is exact for the day it was sent on, and the client refreshes it
on every insights request, so it is wrong for at most the hours between a DST change and the next
request.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("aura_backend.user_timezone")

__all__ = [
    "TIMEZONE_HEADER",
    "LocalDay",
    "canonical_name",
    "local_day",
    "parse_timezone",
    "remember_timezone",
    "resolve_timezone",
    "stored_timezone",
]

TIMEZONE_HEADER = "X-Timezone"

_MAX_LENGTH = 64
# "+03:00", "-0500", "+3", "UTC+03:00", "GMT-5". Real offsets span -12:00 to +14:00.
_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)
_CACHE_PREFIX = "user-tz:"
_CACHE_TTL_S = 60 * 24 * 3600


def parse_timezone(value: Any) -> tzinfo | None:
    """An IANA name or a fixed offset, or ``None`` if ``value`` is neither.

    Abbreviations such as ``"IST"`` are refused rather than guessed: one abbreviation can name
    several unrelated zones. That is also why ``DateTime.timeZoneName`` is never sent.
    """
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > _MAX_LENGTH:
        return None
    match = _OFFSET.match(name)
    if match:
        sign, hours, minutes = match.group(1), int(match.group(2)), int(match.group(3) or 0)
        if minutes >= 60 or hours * 60 + minutes > 14 * 60:
            return None
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(-delta if sign == "-" else delta)
    if name.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        # ValueError covers path-like keys ("../etc/passwd"), which zoneinfo refuses itself.
        return None


def resolve_timezone(value: Any) -> tzinfo:
    """``parse_timezone``, with UTC for anything missing or invalid."""
    return parse_timezone(value) or timezone.utc


def canonical_name(tz: tzinfo) -> str:
    """The value to store: the IANA key, or the offset as ``+HH:MM``."""
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    offset = tz.utcoffset(None) or timedelta(0)
    total = int(offset.total_seconds() // 60)
    sign = "-" if total < 0 else "+"
    hours, minutes = divmod(abs(total), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


@dataclass(frozen=True, slots=True)
class LocalDay:
    """One calendar day in the user's timezone.

    ``start`` and ``end`` are UTC instants, ``end`` exclusive. They are the local midnights, so a
    DST day is 23 or 25 hours long, not 24.
    """

    now: datetime
    start: datetime
    end: datetime

    @property
    def day(self) -> date:
        return self.now.date()


def _midnight(day: date, tz: tzinfo) -> datetime:
    # Where a DST change skips midnight (America/Santiago, for one), zoneinfo resolves the
    # missing 00:00 with the offset in force before the change. That instant is the first moment
    # that exists on the new day, which is the day's real start.
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)


def local_day(now: datetime, tz: tzinfo) -> LocalDay:
    """The user's current day, containing ``now``, which must be timezone-aware."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(tz)
    today = local_now.date()
    return LocalDay(
        now=local_now,
        start=_midnight(today, tz),
        end=_midnight(today + timedelta(days=1), tz),
    )


def _key(user_id: Any) -> str:
    return f"{_CACHE_PREFIX}{user_id}"


async def remember_timezone(cache: Any, user_id: Any, tz: tzinfo) -> None:
    """Best-effort: a cache that is down costs the scheduled jobs the user's day, nothing more."""
    if cache is None:
        return
    try:
        await cache.set(_key(user_id), canonical_name(tz).encode("utf-8"), ttl_s=_CACHE_TTL_S)
    except Exception:
        logger.warning("could not store the user's timezone", exc_info=True)


async def stored_timezone(cache: Any, user_id: Any) -> tzinfo | None:
    if cache is None:
        return None
    try:
        raw = await cache.get(_key(user_id))
    except Exception:
        logger.warning("could not read the user's timezone", exc_info=True)
        return None
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except UnicodeDecodeError:
        return None
    return parse_timezone(text)
