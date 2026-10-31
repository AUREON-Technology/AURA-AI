"""AURA Insights API (Doc/V2_BLUEPRINT.md §8). The on-demand
`/generate` endpoint calls services/insights.py directly and is the
primary interface — it works with nothing but Postgres configured. The
Celery Beat schedule (celery_app.py) is an additive automation on top,
periodically calling the same service function for active users, not a
prerequisite for the feature to function.
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.base import get_db, is_configured
from infrastructure.db.models import Insight
from infrastructure.db.user_repo import get_or_create_user
from services.insights import generate_insights_for_user
from services.jwt_auth import verify_access_token
from services.user_timezone import (
    TIMEZONE_HEADER,
    parse_timezone,
    remember_timezone,
    stored_timezone,
)

router = APIRouter(prefix="/api/v3/insights", tags=["insights"])


def _require_db_configured():
    if not is_configured():
        raise HTTPException(status_code=503, detail="Postgres is not configured (DATABASE_URL unset).")


class InsightResponse(BaseModel):
    id: str
    kind: str
    message: str
    explanation: str
    created_at: str
    #: Record ids (task/health/finance/goal/event) grounding this insight (Phase E3). Empty for
    #: any insight generated before this field existed -- `payload.get` defaults it, not a
    #: migration, since it lives in the JSON blob, not a column.
    citations: list[str] = []


def _shared_cache(request: Request):
    """The v3 container's cache (Redis in both deployments), or ``None`` without a container."""
    container = getattr(request.app.state, "aura", None)
    return getattr(container, "cache_backend", None)


async def _user_timezone(request: Request, user_id: uuid.UUID):
    """The timezone for this user's "today" (F-17).

    A valid ``X-Timezone`` header wins and is remembered for the scheduled jobs, which have no
    request to read it from. Without one, the last remembered value is used. With neither, the
    result is UTC (``None``). An invalid header is ignored rather than refused: a bad timezone
    should cost the user their local day, not their insights.
    """
    cache = _shared_cache(request)
    sent = parse_timezone(request.headers.get(TIMEZONE_HEADER))
    if sent is not None:
        await remember_timezone(cache, user_id, sent)
        return sent
    return await stored_timezone(cache, user_id)


def _to_response(row: Insight) -> InsightResponse:
    payload = json.loads(row.payload_json)
    return InsightResponse(
        id=str(row.id),
        kind=row.kind,
        message=payload.get("message", ""),
        explanation=payload.get("explanation", ""),
        created_at=row.created_at.isoformat(),
        citations=payload.get("citations", []),
    )


@router.get("", response_model=list[InsightResponse])
async def list_insights(
    request: Request,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    # Nothing here depends on the day, but this is the request the client makes on every visit
    # to the Today screen, so it is where the scheduled jobs learn the user's timezone.
    await _user_timezone(request, user.id)
    result = await db.execute(
        select(Insight)
        .where(Insight.user_id == user.id, Insight.dismissed_at.is_(None))
        .order_by(Insight.created_at.desc())
    )
    await db.commit()
    return [_to_response(r) for r in result.scalars().all()]


@router.post("/generate", response_model=list[InsightResponse])
async def generate_insights(
    request: Request,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    created = await generate_insights_for_user(
        db, user.id, tz=await _user_timezone(request, user.id)
    )
    await db.commit()
    return [_to_response(r) for r in created]


@router.post("/{insight_id}/dismiss")
async def dismiss_insight(insight_id: str, user_info: dict = Depends(verify_access_token), db: AsyncSession = Depends(get_db)):
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    try:
        row_id = uuid.UUID(insight_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="insight_id must be a UUID")

    row = await db.get(Insight, row_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Insight not found")

    row.dismissed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "dismissed"}
