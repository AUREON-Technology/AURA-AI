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

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.base import get_db, is_configured
from infrastructure.db.models import Insight, User
from infrastructure.db.user_repo import get_or_create_user
from services.insights import generate_insights_for_user
from services.jwt_auth import verify_access_token
from services.user_language import LANGUAGE_HEADER, remember_language
from services.user_time import TIMEZONE_HEADER, normalize_timezone

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


def _remember_timezone(user: User, client_timezone: str | None) -> None:
    """Stores the zone the client reported (``X-Client-Timezone``) so the scheduled runs, which
    have no request to read it from, compute "today" in it too (audit F-17). A missing or
    unrecognised value leaves the stored one alone: an old client, or a platform that could not
    tell, must not reset a correctly set-up user back to UTC."""
    valid = normalize_timezone(client_timezone)
    if valid is not None and user.timezone != valid:
        user.timezone = valid


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
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
    client_timezone: str | None = Header(default=None, alias=TIMEZONE_HEADER),
    client_language: str | None = Header(default=None, alias=LANGUAGE_HEADER),
):
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    _remember_timezone(user, client_timezone)
    remember_language(user, client_language)
    result = await db.execute(
        select(Insight)
        .where(Insight.user_id == user.id, Insight.dismissed_at.is_(None))
        .order_by(Insight.created_at.desc())
    )
    await db.commit()
    return [_to_response(r) for r in result.scalars().all()]


@router.post("/generate", response_model=list[InsightResponse])
async def generate_insights(
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
    client_timezone: str | None = Header(default=None, alias=TIMEZONE_HEADER),
    client_language: str | None = Header(default=None, alias=LANGUAGE_HEADER),
):
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    _remember_timezone(user, client_timezone)
    remember_language(user, client_language)
    # Flushed before generating: the rules read the zone back from the users row.
    await db.flush()
    created = await generate_insights_for_user(db, user.id)
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
