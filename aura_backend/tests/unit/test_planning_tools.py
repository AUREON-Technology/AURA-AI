"""D3's prioritisation and calendar slot-finding tools: pure computation over caller-supplied
descriptors, no storage in the loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.tools.planning import find_calendar_slots, prioritize_tasks


def _iso(days_from_now: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat()


class TestPrioritizeTasks:
    async def test_empty_task_list(self) -> None:
        result = await prioritize_tasks.execute({"tasks": []}, context=None)
        assert "No tasks" in result

    async def test_sooner_deadline_ranks_first(self) -> None:
        tasks = [
            {"id": "far", "due_date": _iso(20)},
            {"id": "soon", "due_date": _iso(1)},
        ]
        result = await prioritize_tasks.execute({"tasks": tasks}, context=None)
        lines = result.splitlines()
        assert lines[0].split()[1] == "soon"
        assert lines[1].split()[1] == "far"

    async def test_overdue_task_outranks_a_task_due_soon(self) -> None:
        tasks = [
            {"id": "upcoming", "due_date": _iso(1)},
            {"id": "overdue", "due_date": _iso(-3)},
        ]
        result = await prioritize_tasks.execute({"tasks": tasks}, context=None)
        lines = result.splitlines()
        assert lines[0].split()[1] == "overdue"

    async def test_goal_alignment_breaks_a_tie(self) -> None:
        tasks = [
            {"id": "unaligned"},
            {"id": "aligned", "goal_id": "g1"},
        ]
        goals = [{"id": "g1", "priority": "high"}]
        result = await prioritize_tasks.execute({"tasks": tasks, "goals": goals}, context=None)
        lines = result.splitlines()
        assert lines[0].split()[1] == "aligned"

    async def test_quick_win_edges_out_a_slog_at_equal_urgency(self) -> None:
        tasks = [
            {"id": "slog", "effort_minutes": 240},
            {"id": "quick", "effort_minutes": 10},
        ]
        result = await prioritize_tasks.execute({"tasks": tasks}, context=None)
        lines = result.splitlines()
        assert lines[0].split()[1] == "quick"

    async def test_task_blocked_by_an_unmet_dependency_is_held_out_and_listed(self) -> None:
        tasks = [
            {"id": "foundation"},
            {"id": "depends", "depends_on": ["foundation"]},
        ]
        result = await prioritize_tasks.execute({"tasks": tasks}, context=None)
        assert "1. foundation" in result
        assert "Blocked" in result
        assert "depends waiting on foundation" in result

    async def test_a_task_that_unblocks_others_is_boosted(self) -> None:
        tasks = [
            {"id": "standalone"},
            {"id": "blocker"},
            {"id": "waits_on_blocker", "depends_on": ["blocker"]},
        ]
        result = await prioritize_tasks.execute({"tasks": tasks}, context=None)
        lines = [l for l in result.splitlines() if l[0].isdigit()]
        assert lines[0].split()[1] == "blocker"

    async def test_missing_id_is_rejected(self) -> None:
        with pytest.raises(ToolExecutionError):
            await prioritize_tasks.execute({"tasks": [{"due_date": _iso(1)}]}, context=None)


class TestFindCalendarSlots:
    async def test_finds_the_only_gap_in_a_busy_day(self) -> None:
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) \
            + timedelta(days=1)
        result = await find_calendar_slots.execute(
            {
                "search_start": day.isoformat(),
                "search_end": (day + timedelta(hours=12)).isoformat(),
                "duration_minutes": 60,
                "busy": [
                    {"start": day.isoformat(), "end": (day + timedelta(hours=9)).isoformat()},
                    {
                        "start": (day + timedelta(hours=10)).isoformat(),
                        "end": (day + timedelta(hours=12)).isoformat(),
                    },
                ],
            },
            context=None,
        )
        assert "No open slots" not in result
        assert (day + timedelta(hours=9)).isoformat() in result

    async def test_no_gap_returns_a_clear_no_slots_message(self) -> None:
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) \
            + timedelta(days=1)
        result = await find_calendar_slots.execute(
            {
                "search_start": day.isoformat(),
                "search_end": (day + timedelta(hours=2)).isoformat(),
                "duration_minutes": 60,
                "busy": [
                    {"start": day.isoformat(), "end": (day + timedelta(hours=2)).isoformat()},
                ],
            },
            context=None,
        )
        assert result == "No open slots of that length were found in the search window."

    async def test_working_hours_confine_the_search(self) -> None:
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) \
            + timedelta(days=1)
        result = await find_calendar_slots.execute(
            {
                "search_start": day.isoformat(),
                "search_end": (day + timedelta(hours=23)).isoformat(),
                "duration_minutes": 30,
                "working_hours_start": "09:00",
                "working_hours_end": "10:00",
                "max_results": 20,
            },
            context=None,
        )
        for line in result.splitlines():
            start_str = line.split(" - ")[0]
            hour = datetime.fromisoformat(start_str).hour
            assert 9 <= hour < 10

    async def test_search_end_before_search_start_is_rejected(self) -> None:
        with pytest.raises(ToolExecutionError):
            await find_calendar_slots.execute(
                {
                    "search_start": _iso(2),
                    "search_end": _iso(1),
                    "duration_minutes": 30,
                },
                context=None,
            )

    async def test_lopsided_working_hours_pair_is_rejected(self) -> None:
        with pytest.raises(ToolExecutionError):
            await find_calendar_slots.execute(
                {
                    "search_start": _iso(1),
                    "search_end": _iso(2),
                    "duration_minutes": 30,
                    "working_hours_start": "09:00",
                },
                context=None,
            )

    async def test_oversized_search_window_is_rejected(self) -> None:
        with pytest.raises(ToolExecutionError):
            await find_calendar_slots.execute(
                {
                    "search_start": _iso(0),
                    "search_end": _iso(2000),
                    "duration_minutes": 30,
                },
                context=None,
            )
