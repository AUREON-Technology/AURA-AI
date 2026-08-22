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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.base import get_db, is_configured
from infrastructure.db.models import Insight
from infrastructure.db.user_repo import get_or_create_user
from services.insights import generate_insights_for_user
from services.jwt_auth import verify_access_token

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


def _to_response(row: Insight) -> InsightResponse:
    payload = json.loads(row.payload_json)
    return InsightResponse(
        id=str(row.id),
        kind=row.kind,
        message=payload.get("message", ""),
        explanation=payload.get("explanation", ""),
        created_at=row.created_at.isoformat(),
    )


@router.get("", response_model=list[InsightResponse])
async def list_insights(user_info: dict = Depends(verify_access_token), db: AsyncSession = Depends(get_db)):
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    result = await db.execute(
        select(Insight)
        .where(Insight.user_id == user.id, Insight.dismissed_at.is_(None))
        .order_by(Insight.created_at.desc())
    )
    await db.commit()
    return [_to_response(r) for r in result.scalars().all()]


@router.post("/generate", response_model=list[InsightResponse])
async def generate_insights(user_info: dict = Depends(verify_access_token), db: AsyncSession = Depends(get_db)):
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
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
