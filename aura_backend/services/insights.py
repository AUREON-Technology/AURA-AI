"""AURA Insights (Doc/V2_BLUEPRINT.md §8) — the first concrete piece of
"AI that acts on data the backend actually owns," which V1 could never do
since it never held the user's tasks/calendar/finance/health data (see
PROJECT_KNOWLEDGE_BASE.md's verdict on V1). Reads recent Postgres state for
one user and produces a small ranked list of things worth surfacing on the
client's Today screen.

Deliberately rule-based rather than an LLM call: the analysis here
(overdue tasks, hydration vs. goal, weekly spend) is simple enough that a
rule beats an API call on cost, latency, and reliability — and the
project's Gemini key was found suspended during Phase 0 verification, so a
Gemini-dependent-only implementation would ship broken. Phrasing/ranking
via Gemini is a natural follow-up enhancement, not a hard requirement.

Each candidate carries ``citations`` -- the record ids that produced it --
alongside ``message``/``explanation`` (Phase E3). This is the grounding half
of "kernel-generated insights with citations": a future model-phrased
version needs the same evidence to cite from, and capturing it now is real,
free of a model call, and fully backward compatible (an unknown JSON key is
never a breaking change for ``api/v2/insights.py``'s ``payload.get(...)``
reads). The *generation* half -- replacing this rule engine with a kernel
call -- stays out of scope until it clears the eval gate the roadmap
requires: this environment has no real (non-echo) model to run that eval
against, so the honest state is "grounding is ready, replacement is not
proven," not "replaced."
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.models import FinanceRecord, HealthRecord, Insight, Task, User
from aura_core.text import plural_ar
from services.user_language import resolve_language
from services.user_time import local_day_bounds, local_now, resolve_timezone

logger = logging.getLogger("aura_backend.insights")

# Business rules already established for V1 (PROJECT_KNOWLEDGE_BASE.md §6).
WATER_GOAL_ML = 2000
HIGH_SPEND_THRESHOLD = 100.0
#: The insight kinds this module generates -- and therefore the only ones it may replace. Other
#: producers share the ``insights`` table (``services/daily_briefing.py`` writes
#: ``daily_briefing``), and an hourly run must not delete their rows.
GENERATED_KINDS = ("overdue_tasks", "hydration", "high_spend")


def _text(kind: str, language: str, **v) -> dict[str, str]:
    """``message``/``explanation`` for one rule, in the user's language (docs/plans/
    arabic-language_plan.md, decision 9). English is word-for-word what this module always wrote;
    Arabic is Egyptian, with the noun agreeing with the count. ``$`` and ``ml`` stay as they are:
    the language never changes a unit or a currency (decision 15)."""
    if language == "ar":
        if kind == "overdue_tasks":
            tasks = plural_ar(v["count"], one="مهمة واحدة متأخرة", two="مهمتين متأخرين",
                              few="{n} مهام متأخرة", other="{n} مهمة متأخرة")
            return {
                "message": f"عندك {tasks}.",
                "explanation": f"{tasks}: عدّى ميعادها ولسه مش متعلّم عليها إنها خلصت.",
            }
        if kind == "hydration":
            return {
                "message": f"سجّلت {v['current']}ml مية بس النهارده.",
                "explanation": f"الهدف {WATER_GOAL_ML}ml في اليوم، والساعة عدّت 3 العصر وانت لسه موصلتش لنصه.",
            }
        if kind == "high_spend":
            records = plural_ar(v["count"], one="مصروف واحد", two="مصروفين",
                                few="{n} مصاريف", other="{n} مصروف")
            return {
                "message": f"صرفت ${v['total']:.0f} في آخر 7 أيام.",
                "explanation": f"{records} مجموعهم ${v['total']:.2f} الأسبوع ده.",
            }
    if kind == "overdue_tasks":
        n = v["count"]
        return {
            "message": f"You have {n} overdue task{'s' if n != 1 else ''}.",
            "explanation": f"{n} task(s) passed their due date and aren't marked complete.",
        }
    if kind == "hydration":
        return {
            "message": f"Only {v['current']}ml of water logged today.",
            "explanation": f"Goal is {WATER_GOAL_ML}ml/day; it's past 3pm and you're under halfway there.",
        }
    if kind == "high_spend":
        return {
            "message": f"${v['total']:.0f} spent in the last 7 days.",
            "explanation": f"{v['count']} expense record(s) totaling ${v['total']:.2f} this week.",
        }
    raise ValueError(f"unknown insight kind {kind!r}")


async def generate_insights_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None,
) -> list[Insight]:
    """Computes fresh candidate insights, replacing any un-surfaced ones
    from a previous run for this user (avoids piling up stale duplicates
    across repeated calls/scheduled runs)."""
    now = now or datetime.now(timezone.utc)
    # "Today" and "past 3pm" are the user's, not UTC's (audit F-17): the client stores a day's
    # health record at local midnight, which east of UTC is before the UTC day's start.
    settings = (await db.execute(
        select(User.timezone, User.language).where(User.id == user_id)
    )).first()
    tz = resolve_timezone(settings.timezone if settings else None)
    # Written in the language the user's app last reported (decision 9); English if never.
    language = resolve_language(settings.language if settings else None)
    today_start, today_end = local_day_bounds(now, tz)
    candidates: list[dict] = []

    # 1. Overdue tasks
    overdue_result = await db.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.completed_at.is_(None),
            Task.due_date.is_not(None),
            Task.due_date < now,
        )
    )
    overdue = overdue_result.scalars().all()
    if overdue:
        candidates.append({
            "kind": "overdue_tasks",
            "severity": min(len(overdue), 5),
            **_text("overdue_tasks", language, count=len(overdue)),
            "citations": [str(t.id) for t in overdue],
        })

    # 2. Hydration vs. goal (today's record, if any)
    health_result = await db.execute(
        select(HealthRecord)
        .where(
            HealthRecord.user_id == user_id,
            HealthRecord.deleted_at.is_(None),
            HealthRecord.record_date >= today_start,
            HealthRecord.record_date < today_end,
        )
        .order_by(HealthRecord.record_date)
    )
    today_health = health_result.scalars().first()
    # Only nudge in the back half of the user's day, so this isn't wrong at 8am.
    if local_now(now, tz).hour >= 15 and (today_health is None or today_health.water_ml < WATER_GOAL_ML * 0.5):
        current = today_health.water_ml if today_health else 0
        candidates.append({
            "kind": "hydration",
            "severity": 2,
            **_text("hydration", language, current=current),
            "citations": [str(today_health.id)] if today_health else [],
        })

    # 3. High spend this week
    week_start = now - timedelta(days=7)
    finance_result = await db.execute(
        select(FinanceRecord).where(
            FinanceRecord.user_id == user_id,
            FinanceRecord.deleted_at.is_(None),
            FinanceRecord.type == "expense",
            FinanceRecord.occurred_at >= week_start,
        )
    )
    week_expenses = finance_result.scalars().all()
    total_spent = sum(r.amount for r in week_expenses)
    if total_spent >= HIGH_SPEND_THRESHOLD:
        candidates.append({
            "kind": "high_spend",
            "severity": 3,
            **_text("high_spend", language, total=total_spent, count=len(week_expenses)),
            "citations": [str(r.id) for r in week_expenses],
        })

    # Rank by severity, keep top 3 (blueprint §8: "rank the top 1-3 worth surfacing").
    candidates.sort(key=lambda c: c["severity"], reverse=True)
    top = candidates[:3]

    # Replace this user's previously un-surfaced, un-dismissed insights
    # rather than accumulating duplicates across repeated generation runs.
    stale = await db.execute(
        select(Insight).where(
            Insight.user_id == user_id,
            Insight.kind.in_(GENERATED_KINDS),
            Insight.dismissed_at.is_(None),
        )
    )
    for row in stale.scalars().all():
        await db.delete(row)

    created = []
    for c in top:
        row = Insight(
            id=uuid.uuid4(),
            user_id=user_id,
            kind=c["kind"],
            payload_json=json.dumps({
                "message": c["message"], "explanation": c["explanation"],
                "citations": c.get("citations", []),
            }),
        )
        db.add(row)
        created.append(row)

    await db.flush()
    logger.info(f"Generated {len(created)} insight(s) for user {user_id}.")
    return created
