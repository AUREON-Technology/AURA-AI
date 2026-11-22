"""Server-side personal-OS CRUD tools (Phase C2; ADR-032).

These read and write the user's real personal-OS records: in production the store is
``PostgresSyncedPersonalOsStore``, which is the app's own synced ``tasks``/``calendar_events``/
``finance_records``/``health_records`` tables, encrypted with the user's data key that both the
server and the user's devices hold (ADR-032). A task created here is on the user's Tasks tab after
their next sync, and ``list_tasks`` sees what the user created in the app. ``personal_os.py``'s
``propose_*`` tools remain the alternative when a deployment wants a human to approve first.

They are side-effecting with no confirmation gate of their own -- ``ToolPolicy`` still requires
confirmation for a tainted context (ADR-006), but an untainted call from a persona whose
``risk_ceiling`` and ``allowed_tools`` include one of these runs immediately. Keep the
corresponding ``propose_*`` tool for anything a deployment wants a human to approve first; these
are for personas trusted to act without asking.

Full CRUD: ``update_*``/``delete_*`` fetch through the owner-scoped ``store.get``, merge fields
via ``PersonalOsRecord.with_fields``, and write back through ``store.update``/``store.delete``.
Both re-check the fetched record's ``kind`` before acting: ``PersonalOsStore.get``/``delete`` are
scoped by id and owner only, not kind, so without that check ``update_task``/``delete_task`` could
reach a calendar event or finance record that happens to share this owner, given its id -- either
silently corrupting its ``fields`` (an update merging task-shaped keys into an event) or deleting
the wrong record kind outright.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.identifiers import PersonalOsRecordId
from ..domain.personal_os import PersonalOsRecord, RecordKind
from ..domain.tools import ParameterSpec, ToolCategory, ToolSensitivity
from ..ports.runtime import ToolContext
from ..ports.storage import PersonalOsStore
from .base import param, tool
from .personal_os import parse_iso, require_title

__all__ = ["build_personal_os_crud_tools"]

#: Characters of one field shown by a ``list_*`` tool. Without it, 50 records with a 2,000-character
#: description each overflow the result and the registry drops whole records off the end.
_SHOWN_FIELD_CHARS = 200
_DATE_FIELDS = ("due_date", "start_time", "end_time", "occurred_at", "record_date")
_METRIC_FIELDS = ("weight_kg", "water_ml", "sleep_minutes", "exercise_minutes", "calories")


def _shown(value: Any) -> str:
    """One field on one line: a description holding a newline and ``- [id] ...`` must not read as
    another record, and a calendar invite's text is not the tool's to format."""
    text = "".join(c if c.isprintable() else " " for c in str(value))
    return text if len(text) <= _SHOWN_FIELD_CHARS else text[:_SHOWN_FIELD_CHARS] + "..."


def _list_summary(records: list[PersonalOsRecord], *, empty: str) -> str:
    if not records:
        return empty
    lines = []
    for r in records:
        summary = ", ".join(f"{k}={_shown(v)}" for k, v in r.fields.items() if v is not None)
        lines.append(f"- [{r.id.value}] {summary} (created {r.created_at.date().isoformat()})")
    return "\n".join(lines)


def _checked(kind: RecordKind, fields: Mapping[str, Any], tool_name: str) -> dict[str, Any]:
    """The fields as they may be stored, or a :class:`ToolExecutionError` the model can act on.

    The in-memory store accepts anything and the Postgres one rejects some of it late, so the
    rules that matter live here, where both behave the same: no NUL bytes (a Postgres text column
    refuses them), a real title, parseable dates, finite numbers.
    """
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, str):
            value = value.replace("\x00", "")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ToolExecutionError(f"{key} must be a finite number", tool=tool_name)
        out[key] = value
    if "title" in out:
        out["title"] = require_title(out["title"], tool_name)
    for key in _DATE_FIELDS:
        if key in out:
            parse_iso(out[key], key, tool_name)
    return out


def _event_order(fields: Mapping[str, Any], tool_name: str) -> None:
    start, end = fields.get("start_time"), fields.get("end_time")
    if start and end:
        try:
            if parse_iso(end, "end_time", tool_name) < parse_iso(start, "start_time", tool_name):
                raise ToolExecutionError("end_time must not be before start_time", tool=tool_name)
        except TypeError:  # one naive, one aware: not comparable, and not worth refusing over
            pass


def _as_optional(params: Sequence[ParameterSpec]) -> tuple[ParameterSpec, ...]:
    """A create tool's parameters, but every one optional -- an update only names what changes.

    ``create_task`` requires ``title``; requiring it again on ``update_task`` would force a caller
    to resupply a field it has no intention of changing just to adjust, say, ``priority``.
    """
    return tuple(replace(p, required=False, default=None) for p in params)


def _domain(
    *, kind: RecordKind, noun: str, plural: str, create_params, extra_fields=(),
):
    """The (create, list, update, delete) tool quartet for a personal-OS record kind.

    A closure factory rather than four hand-written quartets: the CRUD logic is identical across
    domains (put a record, list this owner's records of this kind, merge fields into one it
    already owns, remove one); only the tool's declared parameters and description differ, which
    is exactly what varies between the arguments passed here.
    """

    def register(builder: "_Builder") -> None:
        async def _owned(context: ToolContext, record_id: str) -> PersonalOsRecord | None:
            """The record, only if it is this caller's and of this kind. ``store.get`` is scoped
            by owner, so a guessed or leaked id from another account finds nothing; the kind check
            stops ``update_task`` reaching an event or finance record that shares the owner."""
            try:
                typed = PersonalOsRecordId(record_id)
            except ValueError:  # not an id at all (spaces, over-long, NUL)
                return None
            found = await builder.store.get(typed, owner_id=context.user_id)
            if found is None or found.kind != kind or found.owner_id != context.user_id:
                return None
            return found

        @tool(
            f"create_{noun}", f"Create a new {noun.replace('_', ' ')} for this user, "
            f"stored immediately -- not a proposal the user must approve.",
            *create_params,
            category=ToolCategory.PRODUCTIVITY,
            sensitivity=ToolSensitivity.PERSONAL,
            side_effecting=True,
        )
        async def create(context: ToolContext, **kwargs: Any) -> str:
            now = datetime.now(timezone.utc)
            name = f"create_{noun}"
            fields = _checked(kind, {k: v for k, v in kwargs.items() if v is not None}, name)
            if kind is RecordKind.CALENDAR_EVENT:
                _event_order(fields, name)
            if kind is RecordKind.HEALTH_RECORD and not any(k in fields for k in _METRIC_FIELDS):
                # A row with a date and nothing else is a blank entry that then blocks that day's
                # real one (health records merge by date).
                raise ToolExecutionError(
                    f"give at least one metric ({', '.join(_METRIC_FIELDS)})", tool=name
                )
            record = PersonalOsRecord(
                id=PersonalOsRecordId.new(), owner_id=context.user_id, kind=kind,
                fields=fields, created_at=now, updated_at=now,
                source=f"agent:{context.agent_id}" if context.agent_id else "kernel",
            )
            stored = await builder.store.put(record)
            # A store may keep the record under another id (a health record merged onto that
            # day's existing row); report the id the record can actually be found under.
            return f"Created {noun} {(stored or record).id.value}."

        @tool(
            f"list_{plural}", f"List this user's {plural.replace('_', ' ')}, most recent first.",
            param("limit", "integer", "Maximum records to return (1-50).", required=False,
                  minimum=1, maximum=50, default=20),
            category=ToolCategory.PRODUCTIVITY,
            sensitivity=ToolSensitivity.PERSONAL,
        )
        async def list_records(context: ToolContext, limit: int = 20) -> str:
            records = await builder.store.list_for_owner(
                context.user_id, kind=kind, limit=limit
            )
            return _list_summary(records, empty=f"No {plural.replace('_', ' ')} recorded yet.")

        record_id_param = param(
            "record_id", "string", f"The {noun}'s id, from create_{noun} or list_{plural}.",
        )

        @tool(
            f"update_{noun}", f"Change one or more fields on an existing {noun.replace('_', ' ')} "
            f"this user owns. Only the fields supplied are changed; the rest are left as they are.",
            record_id_param, *_as_optional(create_params),
            category=ToolCategory.PRODUCTIVITY,
            sensitivity=ToolSensitivity.PERSONAL,
            side_effecting=True,
        )
        async def update(context: ToolContext, record_id: str, **kwargs: Any) -> str:
            existing = await _owned(context, record_id)
            if existing is None:
                return f"No {noun} found with id {_shown(record_id)}."
            name = f"update_{noun}"
            changes = _checked(kind, {k: v for k, v in kwargs.items() if v is not None}, name)
            if not changes:
                return f"Nothing to update for {noun} {record_id}."
            updated = existing.with_fields(changes, updated_at=datetime.now(timezone.utc))
            if kind is RecordKind.CALENDAR_EVENT:
                _event_order(updated.fields, name)  # the merged event, not just what was sent
            await builder.store.update(updated)
            return f"Updated {noun} {record_id}."

        @tool(
            f"delete_{noun}", f"Permanently delete an existing {noun.replace('_', ' ')} "
            f"this user owns.",
            record_id_param,
            category=ToolCategory.PRODUCTIVITY,
            sensitivity=ToolSensitivity.PERSONAL,
            side_effecting=True,
        )
        async def delete(context: ToolContext, record_id: str) -> str:
            existing = await _owned(context, record_id)
            if existing is None:
                return f"No {noun} found with id {_shown(record_id)}."
            if not await builder.store.delete(existing.id, owner_id=context.user_id):
                # Gone between the read and the delete (another device, another call).
                return f"No {noun} found with id {_shown(record_id)}."
            return f"Deleted {noun} {record_id}."

        builder.tools.append(create)
        builder.tools.append(list_records)
        builder.tools.append(update)
        builder.tools.append(delete)

    return register


class _Builder:
    def __init__(self, store: PersonalOsStore) -> None:
        self.store = store
        self.tools: list = []


_DOMAINS = (
    _domain(
        kind=RecordKind.TASK, noun="task", plural="tasks",
        create_params=(
            param("title", "string", "The task title.", max_length=200),
            param("description", "string", "Optional detail.", required=False, max_length=2000),
            param("due_date", "string", "ISO 8601 date, e.g. '2026-09-01'.", required=False),
            param("priority", "string", "Task priority.", required=False,
                  enum=["low", "medium", "high"], default="medium"),
            param("completed", "boolean", "Whether the task is done.", required=False),
        ),
    ),
    _domain(
        kind=RecordKind.CALENDAR_EVENT, noun="calendar_event", plural="calendar_events",
        create_params=(
            param("title", "string", "The event title.", max_length=200),
            param("start_time", "string", "ISO 8601 datetime the event starts."),
            param("end_time", "string", "ISO 8601 datetime the event ends."),
            param("description", "string", "Optional detail.", required=False, max_length=2000),
        ),
    ),
    _domain(
        kind=RecordKind.FINANCE_RECORD, noun="finance_record", plural="finance_records",
        create_params=(
            param("type", "string", "Whether this is money in or out.",
                  enum=["expense", "income"]),
            param("amount", "number", "The amount, positive.", minimum=0),
            param("category", "string", "e.g. 'groceries', 'salary'.", required=False,
                  max_length=64),
            param("description", "string", "Optional detail.", required=False, max_length=500),
            param("occurred_at", "string", "ISO 8601 date or datetime of the transaction; "
                  "defaults to now.", required=False),
        ),
    ),
    _domain(
        kind=RecordKind.HEALTH_RECORD, noun="health_record", plural="health_records",
        create_params=(
            param("record_date", "string", "ISO 8601 date this entry is for.", required=False),
            param("weight_kg", "number", "Body weight in kg.", required=False, minimum=0),
            param("water_ml", "integer", "Water intake in mL.", required=False, minimum=0),
            param("sleep_minutes", "integer", "Sleep duration in minutes.", required=False,
                  minimum=0),
            param("exercise_minutes", "integer", "Exercise duration in minutes.", required=False,
                  minimum=0),
            param("calories", "integer", "Calories consumed.", required=False, minimum=0),
        ),
    ),
)


def build_personal_os_crud_tools(store: PersonalOsStore) -> list:
    """The create/list/update/delete tool quartet for every personal-OS record kind, backed by
    one store."""
    builder = _Builder(store)
    for register in _DOMAINS:
        register(builder)
    return builder.tools
