"""The user's day, not the UTC day (audit F-17).

The client stores a daily health record at local midnight. Insights used to compute "today" as the
UTC day, so a UTC+3 user's record fell before "today" from 21:00 UTC onwards, and the user saw
"0 ml water logged today" every evening. The database half, with real rows, is in
``test_insights_integration.py``. These tests cover the day arithmetic and where the timezone
comes from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from aura_core.llm.cache import InMemoryCache
from services.user_timezone import (
    canonical_name,
    local_day,
    parse_timezone,
    remember_timezone,
    resolve_timezone,
    stored_timezone,
)

UTC = timezone.utc


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


class TestLocalDay:
    def test_a_utc_plus_3_evening_is_still_the_local_day(self) -> None:
        # 18:30 in Riyadh on 31 Oct is 15:30Z. The UTC day began at 00:00Z, but the user's day
        # began at 21:00Z the evening before, which is where the client put the record.
        day = local_day(_at("2026-10-31T15:30:00+00:00"), ZoneInfo("Asia/Riyadh"))
        assert day.start == _at("2026-10-30T21:00:00+00:00")
        assert day.end == _at("2026-10-31T21:00:00+00:00")
        assert day.now.hour == 18
        assert str(day.day) == "2026-10-31"

    def test_after_21z_a_utc_plus_3_user_is_already_on_the_next_day(self) -> None:
        day = local_day(_at("2026-10-31T22:00:00+00:00"), ZoneInfo("Asia/Riyadh"))
        assert str(day.day) == "2026-11-01"
        assert day.start == _at("2026-10-31T21:00:00+00:00")

    def test_a_utc_minus_5_evening_is_the_previous_utc_date(self) -> None:
        # 22:00 on 31 Oct in Bogota is 03:00Z on 1 Nov.
        day = local_day(_at("2026-11-01T03:00:00+00:00"), ZoneInfo("America/Bogota"))
        assert str(day.day) == "2026-10-31"
        assert day.start == _at("2026-10-31T05:00:00+00:00")
        assert day.end == _at("2026-11-01T05:00:00+00:00")
        assert day.now.hour == 22

    def test_a_fixed_offset_gives_the_same_day_as_the_zone(self) -> None:
        now = _at("2026-10-31T15:30:00+00:00")
        assert local_day(now, parse_timezone("+03:00")) == local_day(
            now, ZoneInfo("Asia/Riyadh")
        )

    @pytest.mark.parametrize(
        ("instant", "hours"),
        [
            ("2026-03-08T12:00:00+00:00", 23),  # US spring forward
            ("2026-11-01T12:00:00+00:00", 25),  # US fall back
            ("2026-10-31T12:00:00+00:00", 24),
        ],
    )
    def test_a_dst_day_runs_midnight_to_midnight(self, instant: str, hours: int) -> None:
        day = local_day(_at(instant), ZoneInfo("America/New_York"))
        assert day.end - day.start == timedelta(hours=hours)
        assert day.start.astimezone(ZoneInfo("America/New_York")).hour == 0
        assert day.end.astimezone(ZoneInfo("America/New_York")).hour == 0

    def test_a_dst_change_that_skips_midnight_starts_the_day_at_the_first_real_instant(
        self,
    ) -> None:
        """In Santiago, clocks jump from 00:00 to 01:00 on the first Sunday of September (in
        2026, 6 Sep): local midnight does not exist that day."""
        santiago = ZoneInfo("America/Santiago")
        day = local_day(_at("2026-09-06T18:00:00+00:00"), santiago)
        assert str(day.day) == "2026-09-06"
        assert day.start == _at("2026-09-06T04:00:00+00:00")
        assert day.start.astimezone(santiago).hour == 1  # 00:00 never happened
        assert day.end - day.start == timedelta(hours=23)

    def test_a_naive_now_is_refused(self) -> None:
        with pytest.raises(ValueError):
            local_day(datetime(2026, 10, 31, 12), UTC)


class TestParsing:
    @pytest.mark.parametrize(
        ("value", "minutes"),
        [("+03:00", 180), ("-0500", -300), ("+3", 180), ("UTC+05:30", 330),
         ("GMT-5", -300), ("+14:00", 840), ("-12:00", -720)],
    )
    def test_fixed_offsets(self, value: str, minutes: int) -> None:
        tz = parse_timezone(value)
        assert tz is not None
        assert tz.utcoffset(None) == timedelta(minutes=minutes)

    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "Mars/Olympus_Mons", "IST", "+15:00", "+03:75", "../../etc/passwd",
         "/etc/localtime", "x" * 200, 180, "Asia/Riyadh\x00"],
    )
    def test_an_invalid_or_unknown_timezone_falls_back_to_utc(self, value) -> None:
        assert parse_timezone(value) is None
        assert resolve_timezone(value) is UTC

    def test_iana_names_and_utc(self) -> None:
        assert parse_timezone("Asia/Riyadh") == ZoneInfo("Asia/Riyadh")
        assert parse_timezone(" America/New_York ") == ZoneInfo("America/New_York")
        assert parse_timezone("UTC") is UTC

    def test_canonical_names_round_trip(self) -> None:
        for value in ("Asia/Riyadh", "+03:00", "-05:00", "+05:30"):
            assert canonical_name(parse_timezone(value)) == value
        assert canonical_name(parse_timezone("-0500")) == "-05:00"


class TestRemembering:
    async def test_a_stored_timezone_round_trips(self) -> None:
        cache = InMemoryCache()
        await remember_timezone(cache, "u1", ZoneInfo("Asia/Riyadh"))
        assert await stored_timezone(cache, "u1") == ZoneInfo("Asia/Riyadh")
        assert await stored_timezone(cache, "u2") is None

    async def test_no_cache_and_a_broken_cache_mean_utc_not_an_error(self) -> None:
        class Broken:
            async def get(self, key):
                raise RuntimeError("down")

            async def set(self, key, value, *, ttl_s=None):
                raise RuntimeError("down")

        await remember_timezone(None, "u1", UTC)
        await remember_timezone(Broken(), "u1", UTC)
        assert await stored_timezone(None, "u1") is None
        assert await stored_timezone(Broken(), "u1") is None

    async def test_a_corrupt_stored_value_is_ignored(self) -> None:
        cache = InMemoryCache()
        await cache.set("user-tz:u1", b"\xff\xfe")
        assert await stored_timezone(cache, "u1") is None


class TestTheInsightsRoute:
    """``api/v2/insights.py``: the header wins and is remembered; otherwise the stored value."""

    @staticmethod
    def _request(cache, headers: dict[str, str]):
        from starlette.datastructures import Headers

        app = SimpleNamespace(state=SimpleNamespace(aura=SimpleNamespace(cache_backend=cache)))
        return SimpleNamespace(app=app, headers=Headers(headers))

    async def test_the_header_is_used_and_remembered_for_the_scheduled_jobs(self) -> None:
        from api.v2.insights import _user_timezone

        cache = InMemoryCache()
        tz = await _user_timezone(self._request(cache, {"X-Timezone": "Asia/Riyadh"}), "u1")
        assert tz == ZoneInfo("Asia/Riyadh")
        # A later request without the header (and the hourly job) still gets it.
        assert await _user_timezone(self._request(cache, {}), "u1") == ZoneInfo("Asia/Riyadh")
        assert await stored_timezone(cache, "u1") == ZoneInfo("Asia/Riyadh")

    async def test_an_invalid_header_neither_fails_nor_overwrites(self) -> None:
        from api.v2.insights import _user_timezone

        cache = InMemoryCache()
        await remember_timezone(cache, "u1", parse_timezone("-05:00"))
        tz = await _user_timezone(self._request(cache, {"X-Timezone": "Nowhere/Land"}), "u1")
        assert tz.utcoffset(None) == timedelta(hours=-5)

    async def test_nothing_known_means_utc(self) -> None:
        from api.v2.insights import _user_timezone

        assert await _user_timezone(self._request(InMemoryCache(), {}), "u1") is None
        no_container = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace()), headers={"X-Timezone": "Asia/Riyadh"}
        )
        assert await _user_timezone(no_container, "u1") == ZoneInfo("Asia/Riyadh")
