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

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from .base import param, tool

__all__ = ["prioritize_tasks", "find_calendar_slots"]

_PRIORITY_WEIGHT = {"high": 30.0, "medium": 15.0, "low": 5.0}


#: Tasks, goals or busy intervals one call may carry. Both tools run on the event loop, where a
#: timeout cannot interrupt them, so the cap is the actual bound on the work.
_MAX_ITEMS = 5_000
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_datetime(
    value: str, *, field: str, tool_name: str, default_tz: tzinfo = timezone.utc,
    end_of_day: bool = False,
) -> datetime:
    """An ISO 8601 date or datetime as an aware datetime.

    A value with no offset takes ``default_tz`` (UTC unless the caller says otherwise). A bare date
    is midnight, or with ``end_of_day`` the last instant of that day: a task due "2026-11-22" is not
    overdue at 09:00 on the 22nd.
    """
    if not isinstance(value, str):
        raise ToolExecutionError(f"{field} must be a string; got {type(value).__name__}", tool=tool_name)
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolExecutionError(
            f"{field} must be an ISO 8601 date/datetime string; got {value[:60]!r}", tool=tool_name,
        ) from exc
    if end_of_day and _DATE_ONLY.match(text):
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999_999)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def _label(value: object) -> str:
    """An id as one printable line, so a hostile id cannot forge a second line of the ranking."""
    return "".join(c if c.isprintable() else "?" for c in str(value))[:80]


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
            due = _parse_datetime(
                due_date_raw, field="due_date", tool_name="prioritize_tasks", end_of_day=True
            )
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
        if isinstance(effort, (int, float)) and not isinstance(effort, bool) \
                and math.isfinite(effort) and effort > 0:
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
    name = "prioritize_tasks"
    if not tasks:
        return "No tasks to prioritise."
    if len(tasks) > _MAX_ITEMS or len(goals or []) > _MAX_ITEMS:
        raise ToolExecutionError(f"at most {_MAX_ITEMS} tasks or goals per call", tool=name)

    clean: list[dict] = []
    seen: set[str] = set()
    for t in tasks:
        raw_id = t.get("id") if isinstance(t, dict) else None
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or not str(raw_id).strip():
            raise ToolExecutionError(
                "each task must be an object with a non-empty 'id' field", tool=name
            )
        task_id = str(raw_id)
        if task_id in seen:
            # Two tasks under one id would be ranked as one and one of them silently lost.
            raise ToolExecutionError(f"task id {_label(task_id)!r} appears more than once", tool=name)
        seen.add(task_id)
        deps = t.get("depends_on")
        if deps is None:
            deps = []
        elif isinstance(deps, (str, int)) and not isinstance(deps, bool):
            deps = [deps]  # one id given bare; iterating it would split a string into characters
        elif not isinstance(deps, list):
            raise ToolExecutionError(f"depends_on of {_label(task_id)!r} must be an array of ids", tool=name)
        clean.append({**t, "id": task_id, "depends_on": [str(d) for d in deps],
                      "goal_id": None if t.get("goal_id") is None else str(t["goal_id"])})

    goals_by_id = {
        str(g["id"]): str(g.get("priority") or "medium").lower()
        for g in (goals or []) if isinstance(g, dict) and g.get("id")
    }
    scored, blocked = _score_tasks(clean, goals_by_id, datetime.now(timezone.utc))

    lines = []
    for rank, s in enumerate(scored, start=1):
        reason = ", ".join(s.reasons) if s.reasons else "no deadline, goal, or effort signal"
        lines.append(f"{rank}. {_label(s.id)} (score={s.score:.1f}: {reason})")
    if blocked:
        waiting = {task_id: deps for task_id, deps in blocked}

        def circular(start: str) -> bool:
            """Whether ``start`` is (transitively) waiting on itself, so it can never be unblocked."""
            stack, visited = list(waiting[start]), set()
            while stack:
                node = stack.pop()
                if node == start:
                    return True
                if node not in visited:
                    visited.add(node)
                    stack.extend(waiting.get(node, ()))
            return False

        lines.append("Blocked (waiting on dependencies not marked done):")
        for task_id, deps in blocked:
            note = " -- circular dependency: it can never be unblocked as listed" if circular(task_id) else ""
            lines.append(f"  - {_label(task_id)} waiting on {', '.join(_label(d) for d in deps)}{note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# find_calendar_slots
# --------------------------------------------------------------------------------------


def _clock_minutes(value: str) -> int:
    """``'09:30'`` -> 570. Raises ``ValueError`` for anything that is not an ``HH:MM`` time."""
    parsed = datetime.strptime(str(value).strip(), "%H:%M")
    return parsed.hour * 60 + parsed.minute


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
    """Yield the windows to look for slots in, clipped to the search range.

    With no working hours that is the whole range as one window, so a slot may cross midnight (a
    per-day split with a 23:59:59.999999 end meant the day's last hour was never offered).
    ``work_start``/``work_end`` are minutes after midnight; ``work_end`` may be 1440 (24:00). Calendar
    days are read off ``search_start``'s own offset -- callers searching across timezones should
    normalise before calling.
    """
    if work_start is None:
        yield search_start, search_end
        return
    tz = search_start.tzinfo
    day = search_start.date()
    last_day = search_end.astimezone(tz).date()
    while day <= last_day:
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        window_start = max(midnight + timedelta(minutes=work_start), search_start)
        window_end = min(midnight + timedelta(minutes=work_end), search_end)
        if window_start < window_end:
            yield window_start, window_end
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
    # A value with no offset means the caller's own timezone, i.e. the one search_start names.
    end = _parse_datetime(search_end, field="search_end", tool_name=tool_name, default_tz=start.tzinfo)
    if end <= start:
        raise ToolExecutionError("search_end must be after search_start", tool=tool_name)
    if (end - start) > timedelta(days=400):
        # A daily scan is O(days); an unbounded window from a model-supplied argument is a
        # resource-exhaustion knob otherwise (same reasoning as calculator's exponent cap).
        raise ToolExecutionError(
            "search window must not exceed 400 days", tool=tool_name
        )
    if duration_minutes < 1 or duration_minutes > 400 * 24 * 60:
        raise ToolExecutionError("duration_minutes must be between 1 and the 400-day window", tool=tool_name)

    if (working_hours_start is None) != (working_hours_end is None):
        raise ToolExecutionError(
            "working_hours_start and working_hours_end must both be set or both omitted",
            tool=tool_name,
        )
    work_start = work_end = None
    if working_hours_start is not None:
        try:
            work_start = _clock_minutes(working_hours_start)
            # "24:00" is how a window runs to midnight; "23:59" would lose that last minute.
            work_end = 24 * 60 if str(working_hours_end).strip() == "24:00" else _clock_minutes(working_hours_end)
        except ValueError as exc:
            raise ToolExecutionError(
                "working_hours_start/working_hours_end must be 'HH:MM'", tool=tool_name
            ) from exc
        if work_end <= work_start:
            raise ToolExecutionError(
                "working_hours_end must be after working_hours_start", tool=tool_name
            )

    if len(busy or []) > _MAX_ITEMS:
        raise ToolExecutionError(f"at most {_MAX_ITEMS} busy intervals per call", tool=tool_name)
    intervals = []
    for item in busy or []:
        if not isinstance(item, dict) or "start" not in item or "end" not in item:
            raise ToolExecutionError(
                "each busy item must be an object with 'start' and 'end'", tool=tool_name
            )
        b_start = _parse_datetime(item["start"], field="busy.start", tool_name=tool_name,
                                  default_tz=start.tzinfo)
        b_end = _parse_datetime(item["end"], field="busy.end", tool_name=tool_name,
                                default_tz=start.tzinfo)
        if b_end < b_start:
            # Dropping it would offer the very time the caller meant to block (start and end swapped).
            raise ToolExecutionError(
                f"busy interval ends before it starts: {item['start']} -> {item['end']}", tool=tool_name
            )
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
