"""``build_daily_briefing`` (Phase E2): pure computation over counts, same design as Phase D3's
tools -- no title or description ever appears here because none can: those fields are
zero-knowledge ciphertext this process cannot read (see ``services/daily_briefing.py``, which
supplies the counts from the real, plaintext-only columns of the legacy tables)."""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.domain.goals import GoalSnapshot
from aura_core.triggers.briefing import BriefingCounts, build_daily_briefing


def _goal(**overrides) -> GoalSnapshot:
    base = dict(
        id="g1", category="fitness", status="active", priority="high",
        target_date=datetime(2026, 9, 1, tzinfo=timezone.utc), progress_pct=40,
    )
    base.update(overrides)
    return GoalSnapshot(**base)


class TestBuildDailyBriefing:
    def test_nothing_urgent_when_all_counts_are_zero(self) -> None:
        message, explanation = build_daily_briefing(BriefingCounts())
        assert message == "Nothing urgent today."
        assert "No overdue" in explanation

    def test_overdue_tasks_are_named_with_correct_pluralisation(self) -> None:
        _message, explanation = build_daily_briefing(BriefingCounts(overdue_tasks=1))
        assert "1 overdue task " not in explanation  # no stray trailing space before pluraliser
        assert "1 overdue task" in explanation and "1 overdue tasks" not in explanation

        _message, explanation = build_daily_briefing(BriefingCounts(overdue_tasks=3))
        assert "3 overdue tasks" in explanation

    def test_message_counts_the_number_of_distinct_items(self) -> None:
        message, _explanation = build_daily_briefing(
            BriefingCounts(overdue_tasks=1, due_today_tasks=1, events_today=1)
        )
        assert message.startswith("3 things")

    def test_a_goal_near_target_is_named_by_priority_and_progress(self) -> None:
        _message, explanation = build_daily_briefing(
            BriefingCounts(goals_near_target=(_goal(priority="high", progress_pct=40),))
        )
        assert "high-priority goal at 40% progress" in explanation

    def test_multiple_goals_each_get_their_own_item(self) -> None:
        goals = (_goal(priority="high"), _goal(priority="low", progress_pct=10))
        message, explanation = build_daily_briefing(BriefingCounts(goals_near_target=goals))
        assert message.startswith("2 things")
        assert "high-priority" in explanation and "low-priority" in explanation

    def test_no_task_or_event_title_ever_appears_because_the_type_carries_none(self) -> None:
        """Structural guarantee, not a string check: BriefingCounts has no field a title could
        occupy, so there is nothing for this function to leak even if it wanted to."""
        assert not hasattr(BriefingCounts, "task_titles")
        assert not hasattr(BriefingCounts, "event_titles")

    def test_singular_task_due_today_is_not_pluralised(self) -> None:
        _message, explanation = build_daily_briefing(BriefingCounts(due_today_tasks=1))
        assert "1 task due today" in explanation

    def test_single_event_today_is_not_pluralised(self) -> None:
        _message, explanation = build_daily_briefing(BriefingCounts(events_today=1))
        assert "1 calendar event today" in explanation
