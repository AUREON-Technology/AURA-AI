"""Server-written text in the user's language (docs/plans/arabic-language_plan.md, stage 3;
owner decisions 9 and 15).

The daily briefing and the rule insights are written once, server-side, in the language the
user's app last reported (``users.language``). English must stay word-for-word what it was;
Arabic is Egyptian with the noun agreeing with the count; units and currency never change.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aura_core.domain.goals import GoalSnapshot
from aura_core.text import plural_ar
from aura_core.triggers.briefing import BriefingCounts, build_daily_briefing
from services.insights import _text


class TestPluralAr:
    @pytest.mark.parametrize(("n", "expected"), [
        (1, "مهمة واحدة"), (2, "مهمتين"), (3, "3 مهام"), (10, "10 مهام"), (11, "11 مهمة"),
        (99, "99 مهمة"), (100, "100 مهمة"), (103, "103 مهام"), (111, "111 مهمة"),
    ])
    def test_the_noun_agrees_with_the_count(self, n: int, expected: str) -> None:
        assert plural_ar(n, one="مهمة واحدة", two="مهمتين", few="{n} مهام", other="{n} مهمة") == expected


class TestBriefing:
    def test_english_is_unchanged_and_the_default(self) -> None:
        counts = BriefingCounts(overdue_tasks=2, due_today_tasks=1, events_today=3)
        assert build_daily_briefing(counts) == build_daily_briefing(counts, "en") == (
            "3 things to know about today.",
            "2 overdue tasks; 1 task due today; 3 calendar events today.",
        )
        assert build_daily_briefing(counts, "fr") == build_daily_briefing(counts)

    def test_arabic_briefing(self) -> None:
        goal = GoalSnapshot(id="g", category="health", status="active", priority="high",
                            target_date=datetime(2026, 11, 20, tzinfo=timezone.utc), progress_pct=80)
        message, explanation = build_daily_briefing(
            BriefingCounts(overdue_tasks=2, due_today_tasks=1, events_today=5, goals_near_target=(goal,)),
            "ar",
        )
        assert message == "4 حاجات لازم تعرفها النهارده."
        assert explanation == (
            "مهمتين متأخرين؛ مهمة واحدة مطلوبة النهارده؛ 5 مواعيد في التقويم النهارده؛ "
            "هدف أولويته عالية وصل لـ 80%."
        )

    def test_arabic_quiet_day(self) -> None:
        message, _ = build_daily_briefing(BriefingCounts(), "ar")
        assert message == "مفيش حاجة مستعجلة النهارده."


class TestInsightText:
    def test_english_is_word_for_word_what_it_was(self) -> None:
        assert _text("overdue_tasks", "en", count=1)["message"] == "You have 1 overdue task."
        assert _text("hydration", "en", current=300)["message"] == "Only 300ml of water logged today."
        assert _text("high_spend", "en", total=125.4, count=3) == {
            "message": "$125 spent in the last 7 days.",
            "explanation": "3 expense record(s) totaling $125.40 this week.",
        }

    def test_arabic_agrees_with_the_count_and_keeps_units_and_currency(self) -> None:
        assert _text("overdue_tasks", "ar", count=2)["message"] == "عندك مهمتين متأخرين."
        assert _text("overdue_tasks", "ar", count=4)["message"] == "عندك 4 مهام متأخرة."
        assert "300ml" in _text("hydration", "ar", current=300)["message"]
        spend = _text("high_spend", "ar", total=125.4, count=11)
        assert spend["message"] == "صرفت $125 في آخر 7 أيام."
        assert spend["explanation"].startswith("11 مصروف ") and "$125.40" in spend["explanation"]
