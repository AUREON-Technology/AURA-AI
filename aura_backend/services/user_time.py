"""A user's own clock: which IANA timezone they live in, and where their day starts and ends.

"Today" in the insights and the daily briefing used to be the UTC day, and "past 3pm" the UTC
hour (audit F-17). The client stores a day's health record at *local* midnight, so for anyone
east of UTC that record sits before the UTC day's start: a UTC+3 user got "0ml of water logged
today" every evening, with the record right there. Every day boundary those two services compute
now comes from here, in the user's own zone.

The zone is the ``users.timezone`` column (migration 0018), written from the client's
``X-Client-Timezone`` header (``api/v2/insights.py``). Anything missing or unusable resolves to
UTC, which is exactly the behaviour before this module existed, so a user whose client never
sent a zone sees no change.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: The header the client sends its IANA zone name in (``aura_client``'s ``InsightsClient``).
TIMEZONE_HEADER = "X-Client-Timezone"

#: ``users.timezone`` is VARCHAR(64); the longest real IANA name is ~32 characters.
MAX_TIMEZONE_LENGTH = 64

#: Region/City shaped names only. This is also what keeps a header value from naming a path:
#: ``ZoneInfo`` resolves keys against files on disk.
_IANA_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*$")

#: Names a tz database can contain that are not a place: ``localtime`` is whatever the *server's*
#: zone is, ``posixrules`` is an implementation file, ``Factory`` means "unset".
_NOT_A_PLACE = frozenset({"localtime", "posixrules", "Factory"})


def normalize_timezone(name: object) -> str | None:
    """The IANA zone name to store for ``name``, or None when it is not one.

    None for anything that would not resolve to a real zone, so a caller can treat None as
    "keep what we have" rather than overwriting a good value with junk.
    """
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or len(name) > MAX_TIMEZONE_LENGTH or name in _NOT_A_PLACE:
        return None
    if not _IANA_NAME.match(name):
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return name


def resolve_timezone(name: str | None) -> ZoneInfo:
    """The zone to compute in: the user's when it is valid, UTC otherwise."""
    valid = normalize_timezone(name)
    return ZoneInfo(valid) if valid else ZoneInfo("UTC")


def local_now(now: datetime, tz: ZoneInfo) -> datetime:
    """``now`` (an aware instant) as the user's wall-clock time."""
    return now.astimezone(tz)


def local_day_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """``[start, end)`` of the user's current calendar day, as UTC instants.

    The end is the *next date's* midnight rather than start + 24h: a day that changes to or
    from daylight saving time is 23 or 25 hours long.
    """
    local = local_now(now, tz)
    start = datetime(local.year, local.month, local.day, tzinfo=tz)
    tomorrow = local.date() + timedelta(days=1)
    end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
