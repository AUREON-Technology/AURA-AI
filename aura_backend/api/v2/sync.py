"""Cursor-based delta sync — the architectural centerpiece of the V2 backend
redesign (Doc/V2_BLUEPRINT.md §6/§7). Replaces V1's combination of direct
client-to-Firestore writes and a 577-line hand-rolled per-table LWW sync
engine on the Flutter side with two endpoints:

  GET  /api/v3/sync?since=<cursor>  -> every row changed since `since`,
                                        across every syncable table, in one call
  POST /api/v3/sync                 -> a batch of client mutations, applied
                                        with last-write-wins on `client_updated_at`

Postgres becomes the source of truth; the client's local Drift database is
a cache + offline mutation queue, not a second authority.

**The cursor is a transaction id, not a time (audit F-12).** It used to be the application clock
read after the pull's queries, compared against `updated_at` (Postgres `now()`, the *start* time
of the writing transaction). A push that started before a pull and committed after it wrote rows
stamped earlier than that pull's cursor, and no later pull returned them. Now every row carries
`sync_txid`, the id of the transaction that last wrote it, assigned by the database. A pull reads
its snapshot's `xmin` *before* reading any rows -- every transaction with a smaller id has already
committed or aborted -- returns rows `WHERE sync_txid >= since`, and hands back that xmin as the
next cursor. A row the pull could not see belongs to a transaction that was still open, whose id
is therefore at least that xmin, so the next pull returns it. The price is that rows written by
transactions still open at pull time are re-sent once; the client applies every row as an
idempotent upsert, so that is harmless.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import DateTime, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.base import get_db, is_configured
from infrastructure.db.models import SYNCABLE_MODELS
from infrastructure.db.user_repo import get_or_create_user as _get_or_create_user
from services.jwt_auth import verify_access_token

router = APIRouter(prefix="/api/v3", tags=["sync"])


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


#: How far ahead of the server clock a client's `client_updated_at` may claim to be. LWW compares
#: client clocks, so an unbounded value let one device whose clock ran a day fast write rows no
#: correctly-clocked device could update for a day (F-12, FL-15). Five minutes absorbs ordinary
#: NTP drift; anything further ahead is clamped to it.
CLIENT_CLOCK_SKEW_ALLOWANCE = timedelta(minutes=5)

#: Prefix of a transaction-id cursor. Anything else -- including every timestamp cursor a client
#: stored before this format existed -- is read as "no cursor", which means one full sync.
CURSOR_PREFIX = "txid:"

#: The pull's snapshot bounds. Read before any row: see the module docstring for why the order
#: matters, and `encode_cursor` for what is done with each.
_SNAPSHOT_SQL = text(
    "SELECT pg_snapshot_xmin(s)::text::bigint AS xmin, pg_snapshot_xmax(s)::text::bigint AS xmax "
    "FROM pg_current_snapshot() AS s"
)


def encode_cursor(xmin: int) -> str:
    return f"{CURSOR_PREFIX}{xmin}"


def decode_cursor(since: Optional[str], *, xmax: int) -> Optional[int]:
    """The transaction id to resume from, or None for a full sync.

    None when there is no cursor, when it is not a transaction-id cursor (a client upgrading from
    the timestamp format, whose stored cursor may already have skipped rows -- a full sync is
    exactly what repairs that), and when it is *ahead* of the database's own next transaction
    id. That last case cannot happen on the database that issued the cursor, since transaction
    ids only grow; it means the data was moved to a new cluster (dump and restore) whose counter
    restarted lower, and resuming from the stale cursor would skip every new write until the
    counter caught up.
    """
    if not since or not since.startswith(CURSOR_PREFIX):
        return None
    try:
        value = int(since[len(CURSOR_PREFIX):])
    except ValueError:
        return None
    if value < 0 or value > xmax:
        return None
    return value


def bounded_client_clock(value: datetime, *, now: datetime) -> datetime:
    """`client_updated_at` as the LWW rule may use it: aware, and at most
    `CLIENT_CLOCK_SKEW_ALLOWANCE` ahead of the server.

    Clamped rather than rejected, so a device with a fast clock still gets its edit applied; it
    just cannot claim a comparand further in the future than every honest device can reach within
    a few minutes. A naive value is read as UTC, which is what the client sends.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return min(value, now + CLIENT_CLOCK_SKEW_ALLOWANCE)


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
    since: Optional[str] = None,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    """One round trip for a full delta sync across every domain table.

    `since` is the opaque cursor a previous pull returned. The new cursor is this pull's snapshot
    xmin, read before any row (module docstring).
    """
    _require_db_configured()
    user = await _get_or_create_user(db, user_info)

    snapshot = (await db.execute(_SNAPSHOT_SQL)).one()
    since_txid = decode_cursor(since, xmax=snapshot.xmax)

    data: dict[str, list[dict[str, Any]]] = {}
    for table_name, model in SYNCABLE_MODELS.items():
        stmt = select(model).where(model.user_id == user.id)
        if since_txid is not None:
            stmt = stmt.where(model.sync_txid >= since_txid)
        rows = (await db.execute(stmt)).scalars().all()
        data[table_name] = [_row_to_dict(r) for r in rows]

    await db.commit()
    return SyncPullResponse(data=data, cursor=encode_cursor(snapshot.xmin))


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
    now = datetime.now(timezone.utc)

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

        client_clock = bounded_client_clock(mutation.client_updated_at, now=now)
        existing = await db.get(model, row_id)

        if existing is not None and existing.user_id != user.id:
            results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="error", detail="not owned by this user"))
            continue

        if existing is not None and conflict_comparand(existing) >= client_clock:
            results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="conflict", detail="server has a newer version"))
            continue

        if mutation.op == "delete":
            if existing is not None:
                existing.deleted_at = now
                existing.client_updated_at = client_clock
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
            existing.client_updated_at = client_clock

        results.append(SyncMutationResult(id=mutation.id, table=mutation.table, status="applied"))

    await db.commit()
    return results
