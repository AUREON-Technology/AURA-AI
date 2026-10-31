"""The daily briefing (Phase E2): a pure function over counts, same design as Phase D3's tools.

``title_enc``/``description_enc`` on tasks and calendar events are ciphertext. Since ADR-032 the
server holds the user's data key and *could* read them (the agent's personal-OS tools do), so this
is no longer a zero-knowledge guarantee -- but the briefing is still built only from structured,
plaintext facts (counts, dates), as ``services/insights.py``'s rules are: there is no field here a
title could occupy, so a briefing never repeats an item's text into a notification.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.goals import GoalSnapshot

__all__ = ["BriefingCounts", "build_daily_briefing"]


@dataclass(frozen=True, slots=True)
class BriefingCounts:
    overdue_tasks: int = 0
    due_today_tasks: int = 0
    events_today: int = 0
    goals_near_target: tuple[GoalSnapshot, ...] = field(default_factory=tuple)


def build_daily_briefing(counts: BriefingCounts) -> tuple[str, str]:
    """Returns ``(message, explanation)`` -- the same shape ``services/insights.py``'s rule
    candidates already use, so a briefing renders on the client's Today screen identically to a
    rule-generated insight."""
    items: list[str] = []
    if counts.overdue_tasks:
        items.append(f"{counts.overdue_tasks} overdue task{'s' if counts.overdue_tasks != 1 else ''}")
    if counts.due_today_tasks:
        items.append(
            f"{counts.due_today_tasks} task{'s' if counts.due_today_tasks != 1 else ''} due today"
        )
    if counts.events_today:
        items.append(
            f"{counts.events_today} calendar event{'s' if counts.events_today != 1 else ''} today"
        )
    for goal in counts.goals_near_target:
        items.append(f"a {goal.priority}-priority goal at {goal.progress_pct}% progress")

    if not items:
        return (
            "Nothing urgent today.",
            "No overdue or due-today tasks, no events today, and no goals approaching their "
            "target date.",
        )
    message = f"{len(items)} thing{'s' if len(items) != 1 else ''} to know about today."
    explanation = "; ".join(items) + "."
    return message, explanation
