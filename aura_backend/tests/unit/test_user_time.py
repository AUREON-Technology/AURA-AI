"""A user's day is their own (audit F-17).

Insights and the daily briefing computed "today" as the UTC day and "past 3pm" as the UTC hour,
while the client stores a day's health record at local midnight. These pin the pure half of the
fix: which zone names are accepted, where a local day starts and ends, and when each user's
06:00 briefing is due. The database half is in ``test_insights_integration.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services.daily_briefing import BRIEFING_LOCAL_HOUR, briefing_due
from services.user_time import local_day_bounds, normalize_timezone, resolve_timezone

UTC = timezone.utc


class TestNormalizeTimezone:
    @pytest.mark.parametrize("name", ["Asia/Riyadh", "America/Argentina/Buenos_Aires", "UTC",
                                      "Etc/GMT-3", "  Europe/Berlin  "])
    def test_real_zones_are_kept(self, name: str) -> None:
        assert normalize_timezone(name) == name.strip()

    @pytest.mark.parametrize("name", [
        None, "", "   ", "Mars/Olympus_Mons", "GMT+03:00", "+03:00", "../../etc/passwd",
        "/etc/localtime", "localtime", "posixrules", "Factory", "Asia/" + "x" * 80, 3,
    ])
    def test_anything_else_is_refused(self, name: object) -> None:
        assert normalize_timezone(name) is None

    def test_missing_or_invalid_resolves_to_utc(self) -> None:
        assert resolve_timezone(None) == ZoneInfo("UTC")
        assert resolve_timezone("Mars/Olympus_Mons") == ZoneInfo("UTC")
        assert resolve_timezone("Asia/Riyadh") == ZoneInfo("Asia/Riyadh")


class TestLocalDayBounds:
    def test_a_utc_plus_3_evening_is_still_that_local_day(self) -> None:
        """The F-17 case: 20:00 in Riyadh is 17:00 UTC, and that day began at 21:00 UTC the day
        before -- where the client's local-midnight health record sits."""
        start, end = local_day_bounds(datetime(2026, 10, 31, 17, 0, tzinfo=UTC),
                                      ZoneInfo("Asia/Riyadh"))
        assert start == datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
        assert end == datetime(2026, 10, 31, 21, 0, tzinfo=UTC)

    def test_after_local_midnight_the_day_has_moved_on(self) -> None:
        """22:00 UTC is already 01:00 the next day in Riyadh."""
        start, _ = local_day_bounds(datetime(2026, 10, 31, 22, 0, tzinfo=UTC),
                                    ZoneInfo("Asia/Riyadh"))
        assert start == datetime(2026, 10, 31, 21, 0, tzinfo=UTC)

    def test_a_west_of_utc_day_ends_after_the_utc_one(self) -> None:
        start, end = local_day_bounds(datetime(2026, 10, 31, 11, 0, tzinfo=UTC),
                                      ZoneInfo("America/Bogota"))
        assert start == datetime(2026, 10, 31, 5, 0, tzinfo=UTC)
        assert end == datetime(2026, 11, 1, 5, 0, tzinfo=UTC)

    def test_a_daylight_saving_day_is_not_24_hours(self) -> None:
        """Berlin leaves summer time on 2026-10-25: that local day is 25 hours long."""
        start, end = local_day_bounds(datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
                                      ZoneInfo("Europe/Berlin"))
        assert end - start == timedelta(hours=25)

    def test_utc_is_the_old_behaviour(self) -> None:
        start, end = local_day_bounds(datetime(2026, 10, 31, 17, 0, tzinfo=UTC), ZoneInfo("UTC"))
        assert (start, end) == (datetime(2026, 10, 31, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))


class TestBriefingDue:
    riyadh = ZoneInfo("Asia/Riyadh")

    def test_due_at_six_in_the_users_own_morning(self) -> None:
        assert BRIEFING_LOCAL_HOUR == 6
        # 03:00 UTC is 06:00 in Riyadh.
        assert briefing_due(datetime(2026, 10, 31, 3, 0, tzinfo=UTC), self.riyadh, None)

    def test_only_due_in_the_users_morning(self) -> None:
        assert not briefing_due(datetime(2026, 10, 31, 2, 59, tzinfo=UTC), self.riyadh, None)
        # 06:00 UTC -- the old global run -- is fine for UTC but 23:00 the night before in LA.
        assert briefing_due(datetime(2026, 10, 31, 6, 0, tzinfo=UTC), ZoneInfo("UTC"), None)
        assert not briefing_due(datetime(2026, 10, 31, 6, 0, tzinfo=UTC),
                                ZoneInfo("America/Los_Angeles"), None)
        # Noon local and later is not a morning briefing any more.
        assert not briefing_due(datetime(2026, 10, 31, 9, 0, tzinfo=UTC), self.riyadh, None)

    def test_once_per_local_day(self) -> None:
        sent = datetime(2026, 10, 31, 3, 0, tzinfo=UTC)
        assert not briefing_due(datetime(2026, 10, 31, 4, 0, tzinfo=UTC), self.riyadh, sent)
        assert not briefing_due(datetime(2026, 10, 31, 8, 59, tzinfo=UTC), self.riyadh, sent)
        # The next local morning it is due again.
        assert briefing_due(datetime(2026, 11, 1, 3, 0, tzinfo=UTC), self.riyadh, sent)

    def test_a_missed_hour_is_caught_up_later_that_morning(self) -> None:
        """A beat restart across 06:00 must not cost the user that day's briefing."""
        yesterday = datetime(2026, 10, 30, 3, 0, tzinfo=UTC)
        # 08:00 UTC is 11:00 in Riyadh.
        assert briefing_due(datetime(2026, 10, 31, 8, 0, tzinfo=UTC), self.riyadh, yesterday)
