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
from ..text import plural_ar

__all__ = ["BriefingCounts", "build_daily_briefing"]


@dataclass(frozen=True, slots=True)
class BriefingCounts:
    overdue_tasks: int = 0
    due_today_tasks: int = 0
    events_today: int = 0
    goals_near_target: tuple[GoalSnapshot, ...] = field(default_factory=tuple)


def build_daily_briefing(counts: BriefingCounts, language: str = "en") -> tuple[str, str]:
    """Returns ``(message, explanation)`` -- the same shape ``services/insights.py``'s rule
    candidates already use, so a briefing renders on the client's Today screen identically to a
    rule-generated insight.

    ``language`` is the user's saved app language (``users.language``); ``"ar"`` writes it in
    Egyptian Arabic (docs/plans/arabic-language_plan.md, decision 9), anything else in English,
    exactly as before.
    """
    if language == "ar":
        return _build_daily_briefing_ar(counts)
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


#: Goal priorities as the briefing names them in Arabic; an unknown value is shown as stored.
_PRIORITY_AR = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}


def _build_daily_briefing_ar(counts: BriefingCounts) -> tuple[str, str]:
    items: list[str] = []
    if counts.overdue_tasks:
        items.append(plural_ar(
            counts.overdue_tasks, one="مهمة واحدة متأخرة", two="مهمتين متأخرين",
            few="{n} مهام متأخرة", other="{n} مهمة متأخرة",
        ))
    if counts.due_today_tasks:
        items.append(plural_ar(
            counts.due_today_tasks, one="مهمة واحدة مطلوبة النهارده",
            two="مهمتين مطلوبين النهارده", few="{n} مهام مطلوبة النهارده",
            other="{n} مهمة مطلوبة النهارده",
        ))
    if counts.events_today:
        items.append(plural_ar(
            counts.events_today, one="ميعاد واحد في التقويم النهارده",
            two="ميعادين في التقويم النهارده", few="{n} مواعيد في التقويم النهارده",
            other="{n} ميعاد في التقويم النهارده",
        ))
    for goal in counts.goals_near_target:
        priority = _PRIORITY_AR.get(goal.priority, goal.priority)
        items.append(f"هدف أولويته {priority} وصل لـ {goal.progress_pct}%")

    if not items:
        return (
            "مفيش حاجة مستعجلة النهارده.",
            "مفيش مهام متأخرة ولا مطلوبة النهارده، ولا مواعيد النهارده، ولا أهداف قربت على "
            "ميعادها.",
        )
    message = plural_ar(
        len(items), one="حاجة واحدة لازم تعرفها النهارده.", two="حاجتين لازم تعرفهم النهارده.",
        few="{n} حاجات لازم تعرفها النهارده.", other="{n} حاجة لازم تعرفها النهارده.",
    )
    explanation = "؛ ".join(items) + "."
    return message, explanation
