"""Cursor-based delta sync — the architectural centerpiece of the V2 backend
redesign (Doc/V2_BLUEPRINT.md §6/§7). Replaces V1's combination of direct
client-to-Firestore writes and a 577-line hand-rolled per-table LWW sync
engine on the Flutter side with two endpoints:

  GET  /api/v3/sync?since=<cursor>  -> every row changed since `since`,
                                        across every syncable table, in one call
  POST /api/v3/sync                 -> a batch of client mutations, applied
                                        with last-write-wins on `client_updated_at`

**A second writer (ADR-032).** The agent's personal-OS tools write `tasks`, `calendar_events`,
`finance_records` and `health_records` too, through
`aura_core/adapters/postgres_synced_personal_os.py`. `aura_core` may not import this module, so
that adapter restates the rules below in SQL: the owner scope, `sync_txid` on every write, the
server clock as `client_updated_at`, tombstones that blank `*_enc`, and the `health_records`
natural key. Change a rule here and change it there; `test_personal_os_sync_integration.py`
checks the two against each other on a real Postgres. `*_enc` values are AES-GCM under the
user's data key, which the server holds, so they are not opaque to the server any more, although
this endpoint still only stores and returns them.

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

**A pull can be paged (INT-013).** A client that sends `limit` gets at most that many rows, plus
`has_more` and an opaque `next_page` token to send instead of `since` for the next page; the
cursor it stores is the one on the last page. Without `limit` the pull is one response, as every
client before paging expects. Pages walk the tables in a fixed order and each table by
`(sync_txid, id)`, and the final cursor is the *first* page's snapshot xmin (carried in the
token), so a row written while the pages were being fetched has a txid at or past it and the next
pull returns it -- the same argument as above, stretched over several requests.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ValidationError
from sqlalchemy import DateTime, select, text, tuple_
from sqlalchemy.exc import DataError, IntegrityError, InterfaceError, OperationalError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.base import get_db, is_configured
from infrastructure.db.models import SYNCABLE_MODELS
from infrastructure.db.user_repo import get_or_create_user as _get_or_create_user
from services.jwt_auth import verify_access_token

router = APIRouter(prefix="/api/v3", tags=["sync"])
logger = logging.getLogger("aura_backend.sync")


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
#
# `sync_txid` is the pull cursor's column; a payload that set it low would hide
# the row from every other device's next pull.
_SERVER_OWNED_COLUMNS = frozenset({
    "id", "user_id", "created_at", "updated_at", "client_updated_at", "deleted_at", "sync_txid",
})


def payload_contract() -> dict[str, dict[str, list[str]]]:
    """What a push payload may and must carry, per table: the client/server contract.

    `columns` are the keys the server writes (any other key is ignored); `required` are the
    columns a new row cannot be inserted without. Checked into the client as
    `aura_client/test/fixtures/sync_payload_contract.json` and asserted on both sides, because
    the two drifted once already: the client sent memories as `content_enc` to a `fact_enc NOT
    NULL` column, so every saved memory was a row the server could never accept (F-13).
    """
    contract: dict[str, dict[str, list[str]]] = {}
    for table, model in sorted(SYNCABLE_MODELS.items()):
        writable = [c for c in model.__table__.columns if c.name not in _SERVER_OWNED_COLUMNS]
        contract[table] = {
            "columns": sorted(c.name for c in writable),
            "required": sorted(
                c.name for c in writable
                if not c.nullable and c.default is None and c.server_default is None
            ),
        }
    return contract


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


#: Prefix of a page token (`next_page`). Its fields, dot-separated: the `since` txid the pull
#: started from (`-` for a full sync), the cursor the last page will hand back, the index of the
#: table to resume in (`_PULL_TABLES`), and the `(sync_txid, id)` last returned from it (`-` when
#: the table starts afresh). Not signed: every field only narrows a query already scoped to the
#: caller's own rows, so a forged token can at worst re-send or skip the forger's own data.
PAGE_PREFIX = "page1."

#: Upper bound on `limit`, so one request stays well inside the client's timeout.
MAX_PULL_LIMIT = 5000

#: The order a paged pull walks the tables in. Sorted, so it cannot differ between two pages
#: served by different processes.
_PULL_TABLES: tuple[str, ...] = tuple(sorted(SYNCABLE_MODELS))


class PageToken(BaseModel):
    since: Optional[int]
    cursor: int
    table: int
    after_txid: Optional[int] = None
    after_id: Optional[uuid.UUID] = None


def encode_page(token: PageToken) -> str:
    def part(value) -> str:
        return "-" if value is None else str(value)

    return PAGE_PREFIX + ".".join(
        part(v) for v in (token.since, token.cursor, token.table, token.after_txid, token.after_id)
    )


def decode_page(page: Optional[str], *, xmax: int) -> Optional[PageToken]:
    """The page to resume, or None when there is no token or it cannot be honoured here -- the
    pull then starts over from its first page, which is always safe: the client applies rows
    idempotently and stores the cursor only from the last page. A token whose txids are ahead of
    this database came from another cluster, as for `decode_cursor`."""
    if not page or not page.startswith(PAGE_PREFIX):
        return None
    fields = page[len(PAGE_PREFIX):].split(".")
    if len(fields) != 5:
        return None
    since, cursor, table, after_txid, after_id = (None if f == "-" else f for f in fields)
    try:
        token = PageToken(
            since=None if since is None else int(since),
            cursor=int(cursor),
            table=int(table),
            after_txid=None if after_txid is None else int(after_txid),
            after_id=None if after_id is None else uuid.UUID(after_id),
        )
    except (TypeError, ValueError, ValidationError):
        return None
    if (token.after_txid is None) != (token.after_id is None):
        return None
    if not 0 <= token.table < len(_PULL_TABLES):
        return None
    for value in (token.since, token.cursor, token.after_txid):
        if value is not None and not 0 <= value <= xmax:
            return None
    if token.since is not None and token.since > token.cursor:
        return None
    return token


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
    #: The cursor to store. On a page with `has_more`, the cursor the pull started from (empty for
    #: a full sync): a client must store only the last page's.
    cursor: str
    #: More rows remain; fetch them with `page=next_page`. Always False on an unpaged pull.
    has_more: bool = False
    next_page: Optional[str] = None


class SyncMutation(BaseModel):
    table: str
    op: Literal["upsert", "delete"]
    id: str  # client-generated UUID — makes retries idempotent
    client_updated_at: datetime
    payload: Optional[dict[str, Any]] = None


class SyncPushRequest(BaseModel):
    # Validated one by one inside push_sync rather than here. Typed as `list[SyncMutation]`, one
    # malformed mutation (an unparseable `client_updated_at`, an unknown `op`) failed validation
    # of the whole request with a 422 -- the same batch-wide poison pill as an exception mid-loop
    # (F-11), just raised earlier.
    mutations: list[dict[str, Any]]


class SyncMutationResult(BaseModel):
    id: str
    table: str
    status: Literal["applied", "conflict", "error"]
    detail: Optional[str] = None
    #: On `error` only. False means the same mutation will fail the same way every time (bad
    #: data, unknown table, an id that is not a UUID), so the client should stop resending it
    #: and surface it; True means it may succeed later (a constraint that depends on another row
    #: arriving first).
    retryable: Optional[bool] = None
    #: Set when the mutation was stored under a different id than the one sent: a natural-key
    #: merge (`_NATURAL_KEYS`). The client re-keys its local row.
    server_id: Optional[str] = None
    #: The server's row as it now stands, on `conflict` and on a natural-key merge. A conflict
    #: used to be resolved only "by the next pull" -- but the next pull resumes from a cursor
    #: the unchanged server row may already be behind, so the device kept its rejected edit
    #: forever (FL-15). With the row in hand the client can converge immediately.
    row: Optional[dict[str, Any]] = None


@router.get("/sync", response_model=SyncPullResponse, response_model_exclude_none=True)
async def pull_sync(
    since: Optional[str] = None,
    limit: Optional[int] = Query(default=None, ge=1),
    page: Optional[str] = None,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    """One round trip for a full delta sync across every domain table, or one page of it.

    `since` is the opaque cursor a previous pull returned. The new cursor is this pull's snapshot
    xmin, read before any row (module docstring). With `limit` the response is one page, and
    `page=<next_page>` fetches the next one (INT-013).
    """
    _require_db_configured()
    user = await _get_or_create_user(db, user_info)

    snapshot = (await db.execute(_SNAPSHOT_SQL)).one()

    if limit is None:
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

    limit = min(limit, MAX_PULL_LIMIT)
    token = decode_page(page, xmax=snapshot.xmax)
    if token is None:
        token = PageToken(since=decode_cursor(since, xmax=snapshot.xmax), cursor=snapshot.xmin, table=0)

    data = {name: [] for name in SYNCABLE_MODELS}
    remaining = limit
    next_token: Optional[PageToken] = None
    for index in range(token.table, len(_PULL_TABLES)):
        table_name = _PULL_TABLES[index]
        model = SYNCABLE_MODELS[table_name]
        stmt = select(model).where(model.user_id == user.id)
        if token.since is not None:
            stmt = stmt.where(model.sync_txid >= token.since)
        if index == token.table and token.after_txid is not None:
            stmt = stmt.where(tuple_(model.sync_txid, model.id) > tuple_(token.after_txid, token.after_id))
        # One row more than fits says whether this table has more.
        stmt = stmt.order_by(model.sync_txid, model.id).limit(remaining + 1)
        rows = (await db.execute(stmt)).scalars().all()
        taken = rows[:remaining]
        data[table_name] = [_row_to_dict(r) for r in taken]
        remaining -= len(taken)
        if len(rows) > len(taken):
            last = taken[-1]
            next_token = token.model_copy(update={"table": index, "after_txid": last.sync_txid, "after_id": last.id})
            break
        if remaining == 0 and index + 1 < len(_PULL_TABLES):
            next_token = token.model_copy(update={"table": index + 1, "after_txid": None, "after_id": None})
            break

    await db.commit()
    if next_token is None:
        return SyncPullResponse(data=data, cursor=encode_cursor(token.cursor))
    return SyncPullResponse(
        data=data,
        cursor=encode_cursor(token.since) if token.since is not None else "",
        has_more=True,
        next_page=encode_page(next_token),
    )


#: Tables where a second client-minted id for the same logical record is expected, keyed by the
#: columns that identify that record. `health_records` is one row per user per day
#: (`uq_health_records_user_date`), but every device mints its own uuid for "today"; the second
#: device's insert hit the unique constraint and, with no per-mutation isolation, failed the
#: whole batch on every sync from then on (F-11). The incoming mutation is merged onto the row
#: that already holds the key instead, under the same last-write-wins rule as any other update.
_NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "health_records": ("record_date",),
}


def _drop_tombstone_content(row) -> None:
    """Drop a tombstoned row's content (audit F-07).

    The tombstone itself has to stay, so every other device learns of the delete on its next
    pull; the user's encrypted text does not. A delete used to keep the full ciphertext on the
    server indefinitely behind `deleted_at`. NOT NULL columns get the empty string.
    """
    for column in row.__table__.columns:
        if column.name.endswith("_enc"):
            setattr(row, column.name, None if column.nullable else "")


class _RowError(Exception):
    def __init__(self, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retryable = retryable


async def _natural_key_twin(db: AsyncSession, model, table: str, user_id, payload: dict[str, Any]):
    keys = _NATURAL_KEYS.get(table)
    if not keys or any(payload.get(k) is None for k in keys):
        return None
    stmt = select(model).where(model.user_id == user_id).execution_options(populate_existing=True)
    for key in keys:
        stmt = stmt.where(getattr(model, key) == _coerce_value(model, key, payload[key]))
    return (await db.execute(stmt)).scalars().first()


def is_stale(existing, client_clock: datetime, *, written_in_batch: Optional[datetime] = None) -> bool:
    """Whether an incoming mutation loses to the row the server holds.

    Ties lose: across requests the server cannot tell one device's newer change from another
    device's concurrent one carrying the same clock, and re-applying an identical mutation would
    resurrect a row a concurrent delete had just tombstoned.

    The one exception is a row an earlier mutation *of this same batch* wrote with that same
    clock (`written_in_batch`). A batch is one device's outbox, sent in the order its user made
    the changes, so the later mutation is the newer one. The client's clock is stored in whole
    seconds, and without this a save followed within the second by a delete ("save, then
    forget") lost the delete as a conflict and the row stayed alive (INT-001). The client also
    keeps its clock strictly increasing per row, which covers the same two changes split across
    two pushes, where this rule cannot help.
    """
    comparand = conflict_comparand(existing)
    if comparand > client_clock:
        return True
    return comparand == client_clock and written_in_batch != client_clock


async def _apply_mutation(
    db: AsyncSession, user_id, raw: dict[str, Any], *, now: datetime,
    written: Optional[dict[tuple[str, uuid.UUID], datetime]] = None,
) -> SyncMutationResult:
    """One mutation, inside the caller's SAVEPOINT. Returns a result, or raises for the caller
    to roll the savepoint back and report the row as an error.

    `written` maps each row an earlier mutation of this batch applied to the clock it wrote
    (`is_stale`). Only the caller adds to it, once the savepoint has been released."""
    written = written if written is not None else {}
    try:
        mutation = SyncMutation.model_validate(raw)
    except ValidationError as exc:
        fields = ", ".join(sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()}))
        raise _RowError(f"invalid mutation: {fields}", retryable=False) from None

    def result(status, **fields) -> SyncMutationResult:
        return SyncMutationResult(id=mutation.id, table=mutation.table, status=status, **fields)

    model = SYNCABLE_MODELS.get(mutation.table)
    if model is None:
        raise _RowError("unknown table", retryable=False)
    try:
        row_id = uuid.UUID(mutation.id)
    except ValueError:
        raise _RowError("id must be a UUID", retryable=False) from None

    client_clock = bounded_client_clock(mutation.client_updated_at, now=now)
    payload = mutation.payload or {}
    # populate_existing: an earlier mutation in this batch may have flushed this row, leaving its
    # server-generated columns expired -- and an expired attribute cannot lazy-load under asyncio.
    existing = await db.get(model, row_id, populate_existing=True)

    if existing is not None and existing.user_id != user_id:
        raise _RowError("not owned by this user", retryable=False)

    merged = False
    if existing is None and mutation.op == "upsert":
        existing = await _natural_key_twin(db, model, mutation.table, user_id, payload)
        merged = existing is not None

    if mutation.op == "upsert" and existing is not None and not merged and existing.deleted_at is not None:
        # A deleted id stays deleted, whatever the clocks say (INT-005). The client never
        # re-inserts an id it deleted (`undoable_deletes.dart` defers the delete instead), so
        # this is an edit from a device that had not yet heard of the delete. Writing it used to
        # answer `applied` while the row stayed deleted -- the client dropped the edit as saved
        # and its next pull deleted the row anyway -- and put the edit's ciphertext back onto the
        # tombstone, undoing F-07. A conflict carrying the tombstone says what happened, and the
        # client applies it as a local delete. The natural-key merge is different: a new id for
        # the key is a new record, so it revives the row (as the personal-OS adapter's `revive`).
        return result("conflict", detail="deleted on the server", row=_row_to_dict(existing))

    if existing is not None and is_stale(
        existing, client_clock, written_in_batch=written.get((mutation.table, existing.id)),
    ):
        return result(
            "conflict", detail="server has a newer version",
            server_id=str(existing.id) if merged else None, row=_row_to_dict(existing),
        )

    if mutation.op == "delete":
        if existing is not None:
            existing.deleted_at = now
            existing.client_updated_at = client_clock
            _drop_tombstone_content(existing)
    else:
        if existing is None:
            existing = model(id=row_id, user_id=user_id)
            db.add(existing)
        elif merged:
            # A new record for a key whose previous holder was deleted: the newer write is a new
            # record, not an edit of the tombstone.
            existing.deleted_at = None
        columns = model.__table__.columns
        for key, value in payload.items():
            # Columns only: `hasattr` also matched ORM internals (`metadata`, `registry`, ...).
            if key in _SERVER_OWNED_COLUMNS or key not in columns:
                continue
            setattr(existing, key, _coerce_value(model, key, value))
        # Set after the payload loop, so it records the envelope's value and cannot be
        # overwritten by a payload key of the same name. Without this the comparand would stay
        # NULL and every row would fall back to the server clock.
        existing.client_updated_at = client_clock

    # Flushed here, inside the savepoint, so a constraint violation fails this row and not the
    # batch's final commit.
    await db.flush()
    if merged:
        await db.refresh(existing)
        return result("applied", server_id=str(existing.id), row=_row_to_dict(existing))
    return result("applied")


@router.post("/sync", response_model=list[SyncMutationResult], response_model_exclude_none=True)
async def push_sync(
    body: SyncPushRequest,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    """Applies a batch of outbox mutations with last-write-wins on `client_updated_at`.

    A mutation whose `id` already exists with an equal-or-newer client clock is reported as a
    conflict, carrying the server's row, instead of silently overwriting it -- unless an earlier
    mutation of this batch wrote that row with the same clock (`is_stale`, INT-001). An upsert
    onto a deleted id is a conflict carrying the tombstone (INT-005).

    **Each mutation stands alone (F-11).** It runs in its own SAVEPOINT; a bad value, a
    constraint violation or a malformed envelope rolls back that one mutation and reports it as
    `error`, and the rest of the batch still applies. Previously the only per-row failures were
    the three checked up front; anything else -- an unparseable date, the per-day health
    constraint -- raised out of the loop, the whole request was a 500, and since the client keeps
    its outbox on a failed push and used to stop before pulling, one bad row stalled that
    device's sync in both directions for good. Connection-level failures still fail the request:
    those are not about the row, and nothing after them could commit anyway.
    """
    _require_db_configured()
    user = await _get_or_create_user(db, user_info)
    user_id = user.id
    now = datetime.now(timezone.utc)

    results: list[SyncMutationResult] = []
    written: dict[tuple[str, uuid.UUID], datetime] = {}
    for raw in body.mutations:
        try:
            async with db.begin_nested():
                outcome = await _apply_mutation(db, user_id, raw, now=now, written=written)
            results.append(outcome)
            if outcome.status == "applied":
                # Validated already, or it could not have applied.
                clock = bounded_client_clock(SyncMutation.model_validate(raw).client_updated_at, now=now)
                written[(outcome.table, uuid.UUID(outcome.server_id or outcome.id))] = clock
            continue
        except _RowError as exc:
            detail, retryable = exc.detail, exc.retryable
        except (OperationalError, InterfaceError):
            raise
        except IntegrityError as exc:
            # Possibly ordering (a goal whose project has not arrived yet), so worth a retry.
            logger.info("sync mutation violated a constraint: %s", str(exc.orig)[:300])
            detail, retryable = "violates a database constraint", True
        except (ValueError, TypeError, DataError, StatementError) as exc:
            logger.info("sync mutation carried an invalid value: %s", str(exc)[:300])
            detail, retryable = "invalid value in payload", False
        results.append(SyncMutationResult(
            id=str(raw.get("id", "")), table=str(raw.get("table", "")),
            status="error", detail=detail, retryable=retryable,
        ))

    await db.commit()
    return results
