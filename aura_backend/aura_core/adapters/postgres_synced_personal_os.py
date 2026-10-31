"""The agent's personal-OS store, on the app's own synced tables (ADR-032).

Implements :class:`~aura_core.ports.storage.PersonalOsStore` over ``tasks``,
``calendar_events``, ``finance_records`` and ``health_records`` -- the rows the Flutter client
pulls and pushes through ``/api/v3/sync`` -- so a task the agent creates is on the user's Tasks
tab after their next sync, and ``list_tasks`` returns what the user created in the app.

**Encryption.** ``*_enc`` columns hold ``gcm2`` ciphertext under the owner's data key
(``personal_data_crypto.py``), which the user's devices also hold. This is server-readable by
design (owner decision, ADR-032), not zero-knowledge. A value still in a device's legacy
``gcm:`` format is unreadable here: it is left out of the record the agent sees (with a note
saying so) and never overwritten by an update, because an update writes only the fields it was
given.

**The sync contract, restated.** ``aura_core`` may not import ``api/v2/sync.py`` (ADR-002), so
the rules that endpoint applies are repeated here in SQL; ``test_personal_os_sync_integration.py``
checks the result through the endpoint itself against a real Postgres:

* ownership: every statement is scoped to the owner's ``users.id`` (found, or created as
  ``get_or_create_user`` would, through ``auth_subject``);
* the pull cursor: ``sync_txid`` is the column default on insert and the writing transaction's
  id on every update, so every device's next pull returns the change;
* last-write-wins: ``client_updated_at`` is the server clock at the write -- an older offline edit
  on a device then loses to the agent's change and a newer one wins, as between two devices;
* tombstones: a delete sets ``deleted_at`` and blanks every ``*_enc`` column (NOT NULL ones to
  ``''``), as ``_drop_tombstone_content`` does; tombstoned rows are invisible here;
* ``health_records``' natural key (one row per user per ``record_date``): a create for a day that
  already has a row merges onto it, and the stored record (with that row's id) is returned.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping

from ..domain.errors import AuraError, DataIntegrityError, DependencyUnavailableError, ValidationError
from ..domain.identifiers import PersonalOsRecordId, UserId
from ..domain.personal_os import PersonalOsRecord, RecordKind
from ..domain.user_keys import UserKeyring
from .personal_data_crypto import decrypt_field, encrypt_field
from .postgres_app_users import ensure_user_id, find_user_id
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresSyncedPersonalOsStore", "TABLES", "to_columns", "from_row"]

#: The SQL every update sets besides the changed columns: what makes the row a sync change.
_SYNC_TXID = "(pg_current_xact_id()::text)::bigint"


@dataclass(frozen=True, slots=True)
class _Col:
    field: str
    column: str
    type: str  # enc | text | ts | date | float | int | bool | done
    required: bool = False


@dataclass(frozen=True, slots=True)
class _Table:
    name: str
    columns: tuple[_Col, ...]
    natural_key: str | None = None

    @property
    def enc_columns(self) -> tuple[_Col, ...]:
        return tuple(c for c in self.columns if c.type == "enc")


#: Field names are the tools' parameter names (``aura_core/tools/records.py``); column names and
#: nullability are ``infrastructure/db/models.py``'s. Every SQL identifier below comes from here.
TABLES: dict[RecordKind, _Table] = {
    RecordKind.TASK: _Table("tasks", (
        _Col("title", "title_enc", "enc", required=True),
        _Col("description", "description_enc", "enc"),
        _Col("priority", "priority", "text"),
        _Col("category", "category", "text"),
        _Col("due_date", "due_date", "date"),
        _Col("completed", "completed_at", "done"),
    )),
    RecordKind.CALENDAR_EVENT: _Table("calendar_events", (
        _Col("title", "title_enc", "enc", required=True),
        _Col("description", "description_enc", "enc"),
        _Col("start_time", "start_time", "ts", required=True),
        _Col("end_time", "end_time", "ts", required=True),
        _Col("all_day", "all_day", "bool"),
    )),
    RecordKind.FINANCE_RECORD: _Table("finance_records", (
        _Col("type", "type", "text", required=True),
        _Col("amount", "amount", "float", required=True),
        _Col("category", "category", "text"),
        _Col("description", "description_enc", "enc"),
        _Col("occurred_at", "occurred_at", "ts", required=True),
    )),
    RecordKind.HEALTH_RECORD: _Table("health_records", (
        _Col("record_date", "record_date", "date", required=True),
        _Col("weight_kg", "weight", "float"),
        _Col("water_ml", "water_ml", "int"),
        _Col("sleep_minutes", "sleep_minutes", "int"),
        _Col("exercise_minutes", "exercise_minutes", "int"),
        _Col("calories", "calories", "int"),
    ), natural_key="record_date"),
}

#: Not-NULL columns whose value the server supplies on insert when the caller did not.
_INSERT_DEFAULTS = {
    ("finance_records", "occurred_at"): lambda now: now,
    ("health_records", "record_date"): lambda now: datetime.combine(now.date(), time(), timezone.utc),
}

_UNREADABLE_NOTE = (
    "{fields} not readable by the assistant yet: encrypted on a device that has not yet "
    "upgraded to the account's shared key"
)


def _unavailable(message: str) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres")


def _parse_datetime(field: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time())
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError(
                f"{field} must be an ISO 8601 date or datetime", details={"field": field}
            ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _column_value(col: _Col, value: Any, keyring: UserKeyring, now: datetime) -> Any:
    if value is None:
        return None
    if col.type == "enc":
        return encrypt_field(keyring, str(value))
    if col.type in ("ts", "date"):
        return _parse_datetime(col.field, value)
    if col.type == "float":
        return float(value)
    if col.type == "int":
        return int(value)
    if col.type == "bool":
        return bool(value)
    if col.type == "done":
        return now if value else None
    return str(value)


def to_columns(
    kind: RecordKind, fields: Mapping[str, Any], keyring: UserKeyring, *, now: datetime,
) -> dict[str, Any]:
    """Column values for the fields present in ``fields``; unknown keys are ignored.

    ``done`` (a task's ``completed``) is the one field whose ``False`` must reach the column, as
    a NULL ``completed_at``; every other ``None`` is "not given" and is left out.
    """
    out: dict[str, Any] = {}
    for col in TABLES[kind].columns:
        if col.field not in fields:
            continue
        value = fields[col.field]
        if value is None and col.type != "done":
            continue
        out[col.column] = _column_value(col, value, keyring, now)
    return out


def _field_value(col: _Col, value: Any) -> Any:
    if col.type in ("ts", "date") and isinstance(value, datetime):
        utc = value.astimezone(timezone.utc)
        if col.type == "date" and utc.time() == time():
            return utc.date().isoformat()
        return utc.isoformat()
    return value


def from_row(
    kind: RecordKind, row: Mapping[str, Any], keyring: UserKeyring | None,
) -> dict[str, Any]:
    """The record's fields as the agent sees them. ``*_enc`` values it cannot read are omitted
    and named in a ``note``; never returned as ciphertext."""
    fields: dict[str, Any] = {}
    unreadable: list[str] = []
    for col in TABLES[kind].columns:
        raw = row[col.column]
        if col.type == "done":
            fields[col.field] = raw is not None
            continue
        if raw is None:
            continue
        if col.type == "enc":
            try:
                plain = decrypt_field(keyring, raw) if keyring is not None else (
                    "" if raw == "" else None
                )
            except DataIntegrityError as exc:
                logger.warning(
                    "personal-OS field failed authentication",
                    extra={"table": TABLES[kind].name, "column": col.column,
                           "reason": exc.details.get("reason") if exc.details else None},
                )
                plain = None
            if plain is None:
                unreadable.append(col.field)
                continue
            fields[col.field] = plain
            continue
        fields[col.field] = _field_value(col, raw)
    if unreadable:
        fields["note"] = _UNREADABLE_NOTE.format(fields=", ".join(unreadable))
    return fields


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PostgresSyncedPersonalOsStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None, keys: Any) -> None:
        self._pool = pool or PostgresPool(url)
        #: A ``UserDataKeyStore``.
        self._keys = keys

    # -- helpers ----------------------------------------------------------------------

    def _record(
        self, kind: RecordKind, row: Mapping[str, Any], owner_id: UserId,
        keyring: UserKeyring | None,
    ) -> PersonalOsRecord:
        return PersonalOsRecord(
            id=PersonalOsRecordId(str(row["id"])), owner_id=owner_id, kind=kind,
            fields=from_row(kind, row, keyring), created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]), source="sync",
        )

    async def _run(self, what: str, work):
        try:
            pool = await self._pool._get_pool()
            async with pool.acquire() as conn:
                return await work(conn)
        except AuraError:
            raise
        except Exception as exc:
            from asyncpg.exceptions import DataError, IntegrityConstraintViolationError

            if isinstance(exc, (IntegrityConstraintViolationError, DataError)):
                raise ValidationError(
                    f"could not {what}: the values conflict with an existing record",
                    details={"reason": type(exc).__name__},
                ) from exc
            raise _unavailable(f"could not {what}: {type(exc).__name__}") from exc

    # -- port -------------------------------------------------------------------------

    async def put(self, record: PersonalOsRecord) -> PersonalOsRecord | None:
        table = TABLES[record.kind]
        row_id = _as_uuid(record.id.value)
        if row_id is None:
            raise ValidationError("a personal-OS record id must be a UUID")
        keyring = await self._keys.keyring(record.owner_id, create=True)
        now = datetime.now(timezone.utc)
        columns = to_columns(record.kind, record.fields, keyring, now=now)
        for (name, column), default in _INSERT_DEFAULTS.items():
            if name == table.name and column not in columns:
                columns[column] = default(now)
        missing = [
            c.field for c in table.columns if c.required and c.column not in columns
        ]
        if missing:
            raise ValidationError(
                f"missing required field(s): {', '.join(missing)}", details={"fields": missing}
            )

        async def work(conn):
            async with conn.transaction():
                user_id = await ensure_user_id(conn, record.owner_id.value)
                if table.natural_key is not None:
                    twin = await conn.fetchval(
                        f"SELECT id FROM {table.name} "
                        f"WHERE user_id = $1 AND {table.natural_key} = $2",
                        user_id, columns[table.natural_key],
                    )
                    if twin is not None:
                        # The sync push's rule: a new record for a key whose previous holder was
                        # deleted is a new record, so the tombstone is revived.
                        await self._write(conn, table, twin, user_id, columns, now, revive=True)
                        return twin
                names = ["id", "user_id", "client_updated_at", *columns]
                values = [row_id, user_id, now, *columns.values()]
                placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
                await conn.execute(
                    f"INSERT INTO {table.name} ({', '.join(names)}) VALUES ({placeholders}) "
                    "ON CONFLICT (id) DO NOTHING",
                    *values,
                )
                return row_id

        stored_id = await self._run("write the personal-OS record", work)
        if stored_id == row_id:
            return None
        return PersonalOsRecord(
            id=PersonalOsRecordId(str(stored_id)), owner_id=record.owner_id, kind=record.kind,
            fields=record.fields, created_at=record.created_at, updated_at=now,
            source=record.source,
        )

    async def _write(
        self, conn: Any, table: _Table, row_id: uuid.UUID, user_id: uuid.UUID,
        columns: Mapping[str, Any], now: datetime, *, revive: bool = False,
    ) -> bool:
        # A task already completed keeps its completion time when an update re-asserts it.
        done = {c.column for c in table.columns if c.type == "done"}
        sets = [
            f"{name} = COALESCE({name}, ${i})" if name in done and columns[name] is not None
            else f"{name} = ${i}"
            for i, name in enumerate(columns, start=4)
        ]
        sets += ["client_updated_at = $3", "updated_at = now()", f"sync_txid = {_SYNC_TXID}"]
        if revive:
            sets.append("deleted_at = NULL")
        status = await conn.execute(
            f"UPDATE {table.name} SET {', '.join(sets)} WHERE id = $1 AND user_id = $2"
            + ("" if revive else " AND deleted_at IS NULL"),
            row_id, user_id, now, *columns.values(),
        )
        return status.endswith(" 1")

    async def get(
        self, record_id: PersonalOsRecordId, *, owner_id: UserId
    ) -> PersonalOsRecord | None:
        row_id = _as_uuid(record_id.value)
        if row_id is None:
            return None

        async def work(conn):
            user_id = await find_user_id(conn, owner_id.value)
            if user_id is None:
                return None
            for kind, table in TABLES.items():
                row = await conn.fetchrow(
                    f"SELECT * FROM {table.name} "
                    "WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
                    row_id, user_id,
                )
                if row is not None:
                    return kind, row
            return None

        found = await self._run("read the personal-OS record", work)
        if found is None:
            return None
        keyring = await self._keys.keyring(owner_id, create=False)
        return self._record(found[0], found[1], owner_id, keyring)

    async def list_for_owner(
        self, owner_id: UserId, *, kind: RecordKind | None = None, limit: int = 100,
    ) -> list[PersonalOsRecord]:
        kinds = [kind] if kind is not None else list(TABLES)

        async def work(conn):
            user_id = await find_user_id(conn, owner_id.value)
            if user_id is None:
                return []
            found = []
            for k in kinds:
                rows = await conn.fetch(
                    f"SELECT * FROM {TABLES[k].name} "
                    "WHERE user_id = $1 AND deleted_at IS NULL "
                    "ORDER BY created_at DESC LIMIT $2",
                    user_id, limit,
                )
                found += [(k, r) for r in rows]
            return found

        found = await self._run("list personal-OS records", work)
        if not found:
            return []
        keyring = await self._keys.keyring(owner_id, create=False)
        records = [self._record(k, r, owner_id, keyring) for k, r in found]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records[:limit]

    async def update(self, record: PersonalOsRecord) -> None:
        table = TABLES[record.kind]
        row_id = _as_uuid(record.id.value)
        if row_id is None:
            return
        keyring = await self._keys.keyring(record.owner_id, create=True)
        now = datetime.now(timezone.utc)
        columns = to_columns(record.kind, record.fields, keyring, now=now)
        if not columns:
            return

        async def work(conn):
            user_id = await find_user_id(conn, record.owner_id.value)
            if user_id is None:
                return False
            return await self._write(conn, table, row_id, user_id, columns, now)

        await self._run("update the personal-OS record", work)

    async def delete(self, record_id: PersonalOsRecordId, *, owner_id: UserId) -> bool:
        row_id = _as_uuid(record_id.value)
        if row_id is None:
            return False
        now = datetime.now(timezone.utc)

        async def work(conn):
            user_id = await find_user_id(conn, owner_id.value)
            if user_id is None:
                return False
            async with conn.transaction():
                for table in TABLES.values():
                    # Not-NULL ciphertext columns become '' and nullable ones NULL, as the sync
                    # endpoint's tombstone does.
                    blanks = [
                        f"{c.column} = " + ("''" if c.required else "NULL")
                        for c in table.enc_columns
                    ]
                    sets = [
                        "deleted_at = $3", "client_updated_at = $3", "updated_at = now()",
                        f"sync_txid = {_SYNC_TXID}", *blanks,
                    ]
                    status = await conn.execute(
                        f"UPDATE {table.name} SET {', '.join(sets)} "
                        "WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
                        row_id, user_id, now,
                    )
                    if status.endswith(" 1"):
                        return True
            return False

        return bool(await self._run("delete the personal-OS record", work))
