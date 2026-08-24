"""Pure-computation planning tools (Phase D3): prioritisation and calendar slot-finding.

Both take caller-supplied JSON descriptors as arguments rather than reading storage directly.
That keeps them decoupled from *which* store holds tasks, goals, and calendar events -- the
legacy encrypted ``tasks``/``goals``/``calendar_events`` tables synced from the Flutter client,
the Phase C2 ``PersonalOsRecord`` store, or a future source. The model gathers real data through
``list_tasks``/``list_calendar_events``-style tools or conversation context, then hands it here
for the arithmetic; neither tool touches a database, so neither needs a port, an adapter, or a
capability grant beyond the tool itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from .base import param, tool

__all__ = ["prioritize_tasks", "find_calendar_slots"]

_PRIORITY_WEIGHT = {"high": 30.0, "medium": 15.0, "low": 5.0}


def _parse_datetime(value: str, *, field: str, tool_name: str) -> datetime:
    if not isinstance(value, str):
        raise ToolExecutionError(f"{field} must be a string; got {type(value).__name__}", tool=tool_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolExecutionError(
            f"{field} must be an ISO 8601 date/datetime string; got {value!r}", tool=tool_name,
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------------------------
# prioritize_tasks
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ScoredTask:
    id: str
    score: float
    reasons: list = field(default_factory=list)


def _score_tasks(tasks: list, goals_by_id: dict, now: datetime):
    """Deterministic scoring: deadline urgency + goal alignment + a quick-win effort tilt +
    a bonus for unblocking other listed tasks. A task with an unmet dependency *in this same
    list* is held out of the ranking rather than scored -- there is no way to do it yet."""
    task_ids = {t["id"] for t in tasks}
    blockers_of: dict[str, int] = {}
    for t in tasks:
        for dep in t.get("depends_on") or []:
            blockers_of[dep] = blockers_of.get(dep, 0) + 1

    scored: list[_ScoredTask] = []
    blocked: list[tuple[str, list]] = []
    for t in tasks:
        unmet = [d for d in (t.get("depends_on") or []) if d in task_ids]
        if unmet:
            blocked.append((t["id"], unmet))
            continue

        reasons: list[str] = []
        score = 0.0

        due_date_raw = t.get("due_date")
        if due_date_raw:
            due = _parse_datetime(due_date_raw, field="due_date", tool_name="prioritize_tasks")
            days_left = (due - now).total_seconds() / 86400.0
            if days_left < 0:
                score += 100.0 + min(-days_left, 30.0) * 2.0
                reasons.append(f"overdue by {-days_left:.1f}d")
            else:
                score += max(0.0, 100.0 - days_left * 5.0)
                reasons.append(f"due in {days_left:.1f}d")

        goal_id = t.get("goal_id")
        if goal_id and goal_id in goals_by_id:
            weight = _PRIORITY_WEIGHT.get(goals_by_id[goal_id], 15.0)
            score += weight
            reasons.append(f"goal priority={goals_by_id[goal_id]}")

        effort = t.get("effort_minutes")
        if isinstance(effort, (int, float)) and effort > 0:
            # All else equal, a quick win beats a slog -- a small tilt, not a dominant factor.
            score += max(0.0, (120.0 - min(effort, 120.0)) / 120.0 * 10.0)
            reasons.append(f"effort={effort:g}m")

        unblocks = blockers_of.get(t["id"], 0)
        if unblocks:
            score += unblocks * 5.0
            reasons.append(f"unblocks {unblocks} task(s)")

        scored.append(_ScoredTask(id=t["id"], score=score, reasons=reasons))

    scored.sort(key=lambda s: (-s.score, s.id))
    return scored, blocked


@tool(
    "prioritize_tasks",
    "Rank a list of open tasks by urgency: deadline, alignment with the user's active goals, "
    "estimated effort, and dependencies between the tasks. Pure computation -- pass in tasks "
    "(and, optionally, goals) already gathered via list_tasks or conversation context; this tool "
    "reads and writes no storage itself. A task whose depends_on names another task id present "
    "in this same list is held back as blocked until that id is left out of the list (i.e. "
    "treated as already done).",
    param(
        "tasks", "array",
        "Each item: {id, due_date? (ISO date/datetime), effort_minutes?, goal_id?, depends_on? "
        "(array of task ids from this same list)}.",
        item_type="object",
    ),
    param(
        "goals", "array",
        "Optional. Each item: {id, priority? ('low'|'medium'|'high', default 'medium')}, "
        "matched against a task's goal_id to score goal alignment.",
        required=False, item_type="object", default=[],
    ),
    category=ToolCategory.PRODUCTIVITY,
    sensitivity=ToolSensitivity.PERSONAL,
    timeout_s=3.0,
)
async def prioritize_tasks(tasks: list, goals: list | None = None) -> str:
    if not tasks:
        return "No tasks to prioritise."
    for t in tasks:
        if not isinstance(t, dict) or not t.get("id"):
            raise ToolExecutionError(
                "each task must be an object with a non-empty 'id' field", tool="prioritize_tasks"
            )
    goals_by_id = {
        g["id"]: g.get("priority", "medium")
        for g in (goals or []) if isinstance(g, dict) and g.get("id")
    }
    scored, blocked = _score_tasks(tasks, goals_by_id, datetime.now(timezone.utc))

    lines = []
    for rank, s in enumerate(scored, start=1):
        reason = ", ".join(s.reasons) if s.reasons else "no deadline, goal, or effort signal"
        lines.append(f"{rank}. {s.id} (score={s.score:.1f}: {reason})")
    if blocked:
        lines.append("Blocked (waiting on dependencies not marked done):")
        for task_id, deps in blocked:
            lines.append(f"  - {task_id} waiting on {', '.join(deps)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# find_calendar_slots
# --------------------------------------------------------------------------------------


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _daily_windows(search_start: datetime, search_end: datetime, work_start, work_end):
    """Yield each day's working-hours window (default: the whole day) clipped to the search
    range. Calendar days are read off the datetimes' own tzinfo/offset as given -- callers
    searching across timezones should normalise before calling."""
    day = search_start.date()
    while True:
        if work_start is None:
            window_start = datetime.combine(day, datetime.min.time(), tzinfo=search_start.tzinfo)
            window_end = datetime.combine(day, datetime.max.time(), tzinfo=search_start.tzinfo)
        else:
            window_start = datetime.combine(day, work_start, tzinfo=search_start.tzinfo)
            window_end = datetime.combine(day, work_end, tzinfo=search_start.tzinfo)
        window_start = max(window_start, search_start)
        window_end = min(window_end, search_end)
        if window_start < window_end:
            yield window_start, window_end
        if window_end >= search_end or day > search_end.date():
            break
        day += timedelta(days=1)


def _collect_free_slots(window_start, window_end, busy_merged, duration, max_results, found):
    cursor = window_start
    for b_start, b_end in busy_merged:
        if b_end <= cursor:
            continue
        if b_start >= window_end:
            break
        gap_end = min(b_start, window_end)
        while cursor + duration <= gap_end and len(found) < max_results:
            found.append((cursor, cursor + duration))
            cursor += duration
        cursor = max(cursor, b_end)
        if cursor >= window_end or len(found) >= max_results:
            return
    while cursor + duration <= window_end and len(found) < max_results:
        found.append((cursor, cursor + duration))
        cursor += duration


@tool(
    "find_calendar_slots",
    "Find open time slots of a given duration within a search window, avoiding a supplied list "
    "of busy intervals (e.g. gathered from list_calendar_events). Pure computation -- does not "
    "read the calendar itself.",
    param("search_start", "string", "ISO 8601 datetime; start of the window to search."),
    param("search_end", "string", "ISO 8601 datetime; end of the window to search."),
    param("duration_minutes", "integer", "Length of the slot needed, in minutes.", minimum=1),
    param(
        "busy", "array",
        "Existing commitments to avoid. Each item: {start, end} as ISO 8601 datetimes.",
        item_type="object", required=False, default=[],
    ),
    param(
        "working_hours_start", "string",
        "Optional daily window start as 'HH:MM' (e.g. '09:00'); omit to search the full day.",
        required=False,
    ),
    param(
        "working_hours_end", "string",
        "Daily window end as 'HH:MM'; required together with working_hours_start.",
        required=False,
    ),
    param("max_results", "integer", "Maximum number of slots to return.", required=False,
          minimum=1, maximum=20, default=5),
    category=ToolCategory.PRODUCTIVITY,
    sensitivity=ToolSensitivity.PERSONAL,
    timeout_s=3.0,
)
async def find_calendar_slots(
    search_start: str,
    search_end: str,
    duration_minutes: int,
    busy: list | None = None,
    working_hours_start: str | None = None,
    working_hours_end: str | None = None,
    max_results: int = 5,
) -> str:
    tool_name = "find_calendar_slots"
    start = _parse_datetime(search_start, field="search_start", tool_name=tool_name)
    end = _parse_datetime(search_end, field="search_end", tool_name=tool_name)
    if end <= start:
        raise ToolExecutionError("search_end must be after search_start", tool=tool_name)
    if (end - start) > timedelta(days=400):
        # A daily scan is O(days); an unbounded window from a model-supplied argument is a
        # resource-exhaustion knob otherwise (same reasoning as calculator's exponent cap).
        raise ToolExecutionError(
            "search window must not exceed 400 days", tool=tool_name
        )

    if (working_hours_start is None) != (working_hours_end is None):
        raise ToolExecutionError(
            "working_hours_start and working_hours_end must both be set or both omitted",
            tool=tool_name,
        )
    work_start = work_end = None
    if working_hours_start is not None:
        try:
            work_start = datetime.strptime(working_hours_start, "%H:%M").time()
            work_end = datetime.strptime(working_hours_end, "%H:%M").time()
        except ValueError as exc:
            raise ToolExecutionError(
                "working_hours_start/working_hours_end must be 'HH:MM'", tool=tool_name
            ) from exc
        if work_end <= work_start:
            raise ToolExecutionError(
                "working_hours_end must be after working_hours_start", tool=tool_name
            )

    intervals = []
    for item in busy or []:
        if not isinstance(item, dict) or "start" not in item or "end" not in item:
            raise ToolExecutionError(
                "each busy item must be an object with 'start' and 'end'", tool=tool_name
            )
        b_start = _parse_datetime(item["start"], field="busy.start", tool_name=tool_name)
        b_end = _parse_datetime(item["end"], field="busy.end", tool_name=tool_name)
        if b_end > b_start:
            intervals.append((b_start, b_end))
    merged = _merge_intervals(intervals)

    duration = timedelta(minutes=duration_minutes)
    found: list[tuple[datetime, datetime]] = []
    for window_start, window_end in _daily_windows(start, end, work_start, work_end):
        _collect_free_slots(window_start, window_end, merged, duration, max_results, found)
        if len(found) >= max_results:
            break

    if not found:
        return "No open slots of that length were found in the search window."
    return "\n".join(f"{s.isoformat()} - {e.isoformat()}" for s, e in found)
