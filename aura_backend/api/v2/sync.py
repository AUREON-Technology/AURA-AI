"""Cursor-based delta sync — the architectural centerpiece of the V2 backend
redesign (Doc/V2_BLUEPRINT.md §6/§7). Replaces V1's combination of direct
client-to-Firestore writes and a 577-line hand-rolled per-table LWW sync
engine on the Flutter side with two endpoints:

  GET  /api/v2/sync?since=<cursor>  -> every row changed since `since`,
                                        across every syncable table, in one call
  POST /api/v2/sync                 -> a batch of client mutations, applied
                                        with last-write-wins on `updated_at`

Postgres becomes the source of truth; the client's local Drift database is
a cache + offline mutation queue, not a second authority.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import DateTime, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.base import get_db, is_configured
from infrastructure.db.models import SYNCABLE_MODELS
from infrastructure.db.user_repo import get_or_create_user as _get_or_create_user
from services.jwt_auth import verify_access_token

router = APIRouter(prefix="/api/v2", tags=["sync (v2)"])


def _require_db_configured():
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Postgres sync is not configured (DATABASE_URL unset). See docker-compose.yml's postgres service.",
        )


def _row_to_dict(row) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, uuid.UUID):
            value = str(value)
        result[col.name] = value
    return result


def _coerce_value(model, key: str, value: Any) -> Any:
    """Payload values arrive as JSON (strings/numbers); coerce ISO datetime
    strings into real datetimes for DateTime-typed columns so asyncpg can
    bind them."""
    if value is None:
        return None
    column = model.__table__.columns.get(key)
    if column is not None and isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


# Columns no payload may set. `id`/`user_id` are identity (letting a client set
# them is straightforward IDOR); the timestamps are subtler but worse in
# practice — a client that writes a far-future comparand pins the row
# permanently into the `server has a newer version` branch below and can never
# update it again. `deleted_at` would let an `upsert` silently tombstone (or
# resurrect) a row, bypassing the `op` field entirely.
#
# `client_updated_at` is on this list even though it *is* a client value: it may
# only arrive through the mutation envelope, which is validated, never through
# the free-form payload, which is not.
_SERVER_OWNED_COLUMNS = frozenset({
    "id", "user_id", "created_at", "updated_at", "client_updated_at", "deleted_at",
})


def conflict_comparand(existing) -> datetime:
    """The timestamp an incoming mutation must beat to be applied.

    Returns the row's *client* clock, because that is what the incoming mutation carries. Using
    `updated_at` here -- the server's write time -- is the bug this function exists to prevent:
    it rejected any mutation whose client timestamp predated the last server write, which for an
    offline-first client is the normal case rather than a conflict. Edit on a plane, sync on
    landing, and the server refuses the edit for being older than its own record of receiving the
    previous one. The client saw HTTP 200 and a per-mutation `conflict` it did not inspect, so the
    change disappeared with nothing surfacing as an error.

    Falls back to `updated_at` when `client_updated_at` is NULL, which is every row written before
    that column existed. Returning epoch for those instead would make each one writable by an
    arbitrarily stale mutation; falling back preserves exactly the previous behaviour until the
    row's first write populates the column.

    A free function so the rule is testable without a database -- the case that exposed the bug
    needed Postgres, which is why it went unnoticed for so long.
    """
    return existing.client_updated_at or existing.updated_at


class SyncPullResponse(BaseModel):
    data: dict[str, list[dict[str, Any]]]
    cursor: str


class SyncMutation(BaseModel):
    table: str
    op: Literal["upsert", "delete"]
    id: str  # client-generated UUID — makes retries idempotent
    client_updated_at: datetime
    payload: Optional[dict[str, Any]] = None


class SyncPushRequest(BaseModel):
    mutations: list[SyncMutation]


class SyncMutationResult(BaseModel):
    id: str
    table: str
    status: Literal["applied", "conflict", "error"]
    detail: Optional[str] = None


@router.get("/sync", response_model=SyncPullResponse)
async def pull_sync(
    since: Optional[datetime] = None,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    """One round trip for a full delta sync across every domain table."""
    _require_db_configured()
    user = await _get_or_create_user(db, user_info)

    data: dict[str, list[dict[str, Any]]] = {}
    for table_name, model in SYNCABLE_MODELS.items():
        stmt = select(model).where(model.user_id == user.id)
        if since is not None:
            stmt = stmt.where(model.updated_at > since)
        rows = (await db.execute(stmt)).scalars().all()
        data[table_name] = [_row_to_dict(r) for r in rows]

    await db.commit()
    return SyncPullResponse(data=data, cursor=datetime.now(timezone.utc).isoformat())


@router.post("/sync", response_model=list[SyncMutationResult])
async def push_sync(
    body: SyncPushRequest,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    """Applies a batch of outbox mutations with last-write-wins on
    `updated_at`. A mutation whose `id` already exists with an
    equal-or-newer server `updated_at` is reported as a conflict instead of
    silently overwritten — the client re-pulls and reconciles rather than
    the server guessing which side should win."""
    _require_db_configured()
    user = await _get_or_create_user(db, user_info)

    results: list[SyncMutationResult] = []
    for mutation in body.mutations:
        model = SYNCABLE_MODELS.get(mutation.table)
        if model is None:
            results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="error", detail="unknown table"))
            continue

        try:
            row_id = uuid.UUID(mutation.id)
        except ValueError:
            results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="error", detail="id must be a UUID"))
            continue

        existing = await db.get(model, row_id)

        if existing is not None and existing.user_id != user.id:
            results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="error", detail="not owned by this user"))
            continue

        if existing is not None and conflict_comparand(existing) >= mutation.client_updated_at:
            results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="conflict", detail="server has a newer version"))
            continue

        if mutation.op == "delete":
            if existing is not None:
                existing.deleted_at = datetime.now(timezone.utc)
                existing.client_updated_at = mutation.client_updated_at
        else:
            payload = mutation.payload or {}
            if existing is None:
                existing = model(id=row_id, user_id=user.id)
                db.add(existing)
            for key, value in payload.items():
                if key in _SERVER_OWNED_COLUMNS:
                    continue
                if hasattr(existing, key):
                    setattr(existing, key, _coerce_value(model, key, value))
            # Set after the payload loop, so it records the envelope's value and cannot be
            # overwritten by a payload key of the same name. Without this the comparand would stay
            # NULL and every row would fall back to the server clock -- the bug this fixes.
            existing.client_updated_at = mutation.client_updated_at

        results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="applied"))

    await db.commit()
    return results
