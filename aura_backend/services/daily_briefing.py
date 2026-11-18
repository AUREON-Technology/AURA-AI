"""The daily briefing (Phase E2): a proactive per-user digest, generated once a day.

Same shape as ``services/insights.py`` deliberately -- this *is* an insight (``kind
="daily_briefing"``), just generated on a schedule rather than by a rule reacting to the latest
state. It reads the same legacy tables and only ever counts things, never names them: task and
event titles are ``title_enc`` ciphertext. Since ADR-032 the server holds each user's data key
and *could* decrypt ``gcm2`` titles, but this module deliberately does not -- a digest of counts
needs no plaintext, so it stays out of the set of code paths that read user content. The actual composition (turning counts into the two strings a
client renders) is ``aura_core.triggers.briefing.build_daily_briefing`` -- a pure function, tested
without a database -- kept in ``aura_core`` because it is genuinely reusable kernel logic, not
because this module couldn't hold it.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aura_core.domain.goals import GoalSnapshot
from aura_core.triggers.briefing import BriefingCounts, build_daily_briefing
from infrastructure.db.models import CalendarEvent, Goal, Insight, Task, User
from services.user_time import local_day_bounds, local_now, resolve_timezone
from services.user_language import resolve_language

logger = logging.getLogger("aura_backend.daily_briefing")

#: A goal is "near its target" inside this window -- close enough to be worth a daily nudge,
#: far enough that it doesn't show up fresh every morning for a week straight.
GOAL_NEAR_TARGET_WINDOW = timedelta(days=7)

#: The briefing goes out at this hour of the *user's* morning. The beat runs hourly and asks
#: ``briefing_due`` per user, rather than once a day at 06:00 UTC -- which was 23:00 the night
#: before in California, and would have briefed that user on a day that had not started yet.
BRIEFING_LOCAL_HOUR = 6
#: A run missed at 06:00 (a beat restart) is caught up by a later one until noon local. Past
#: that it is not a morning briefing any more, so the user waits for tomorrow's.
BRIEFING_CATCH_UP_HOURS = 6


def briefing_due(now: datetime, tz: ZoneInfo, last_briefing_at: datetime | None) -> bool:
    """Whether a user in ``tz`` should get a briefing at ``now``: it is their morning (06:00 to
    noon, their time) and they have had none yet this local day.

    A window rather than "the 06:00 hour" so a missed hourly run costs nothing.
    ``last_briefing_at`` counts dismissed briefings too: dismissing it is not asking for another.
    """
    hour = local_now(now, tz).hour
    if not BRIEFING_LOCAL_HOUR <= hour < BRIEFING_LOCAL_HOUR + BRIEFING_CATCH_UP_HOURS:
        return False
    today_start, _ = local_day_bounds(now, tz)
    return last_briefing_at is None or last_briefing_at < today_start


async def generate_daily_briefing_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None,
) -> Insight:
    """Computes and persists today's briefing, replacing yesterday's un-surfaced one (same
    replace-not-accumulate rule ``services/insights.py`` uses for its own candidates)."""
    now = now or datetime.now(timezone.utc)
    # The user's own day, not the UTC one (audit F-17).
    settings = (await db.execute(
        select(User.timezone, User.language).where(User.id == user_id)
    )).first()
    tz = resolve_timezone(settings.timezone if settings else None)
    # Written in the language the user's app last reported (decision 9); English if never.
    language = resolve_language(settings.language if settings else None)
    today_start, today_end = local_day_bounds(now, tz)

    overdue_result = await db.execute(
        select(Task).where(
            Task.user_id == user_id, Task.deleted_at.is_(None),
            Task.completed_at.is_(None), Task.due_date.is_not(None), Task.due_date < now,
        )
    )
    overdue = overdue_result.scalars().all()

    due_today_result = await db.execute(
        select(Task).where(
            Task.user_id == user_id, Task.deleted_at.is_(None), Task.completed_at.is_(None),
            Task.due_date >= today_start, Task.due_date < today_end,
        )
    )
    due_today = due_today_result.scalars().all()

    events_result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.user_id == user_id, CalendarEvent.deleted_at.is_(None),
            CalendarEvent.start_time >= today_start, CalendarEvent.start_time < today_end,
        )
    )
    events = events_result.scalars().all()

    goals_result = await db.execute(
        select(Goal).where(
            Goal.user_id == user_id, Goal.deleted_at.is_(None), Goal.status == "active",
            Goal.target_date.is_not(None), Goal.target_date >= now,
            Goal.target_date < now + GOAL_NEAR_TARGET_WINDOW,
        )
    )
    goals = goals_result.scalars().all()
    goals_near_target = tuple(
        GoalSnapshot(
            id=str(g.id), category=g.category, status=g.status, priority=g.priority,
            target_date=g.target_date, progress_pct=g.progress_pct,
        )
        for g in goals
    )
    citations = (
        [str(t.id) for t in overdue] + [str(t.id) for t in due_today]
        + [str(e.id) for e in events] + [str(g.id) for g in goals]
    )

    message, explanation = build_daily_briefing(
        BriefingCounts(
            overdue_tasks=len(overdue), due_today_tasks=len(due_today),
            events_today=len(events), goals_near_target=goals_near_target,
        ),
        language,
    )

    stale = await db.execute(
        select(Insight).where(
            Insight.user_id == user_id, Insight.kind == "daily_briefing",
            Insight.dismissed_at.is_(None),
        )
    )
    for row in stale.scalars().all():
        await db.delete(row)

    row = Insight(
        id=uuid.uuid4(), user_id=user_id, kind="daily_briefing",
        # Stamped with the same clock the day boundaries came from: ``briefing_due`` compares
        # it against the user's local day to send one briefing a day.
        created_at=now,
        payload_json=json.dumps({
            "message": message, "explanation": explanation, "citations": citations,
        }),
    )
    db.add(row)
    await db.flush()
    logger.info(f"Generated daily briefing for user {user_id}.")
    return row
