"""Server-side personal-OS CRUD tools (Phase C2).

Where ``personal_os.py``'s ``propose_*`` tools return a payload the Flutter client applies to its
*own* (zero-knowledge encrypted) local tables after the user approves a card, these write
immediately to a server-owned table the kernel can also read back
(``aura_core/domain/personal_os.py`` explains why it is a different table, not the same one).
That makes them side-effecting with no confirmation gate of their own -- ``ToolPolicy`` still
requires confirmation for a tainted context (ADR-006), but an untainted call from a persona whose
``risk_ceiling`` and ``allowed_tools`` include one of these runs immediately. Keep the corresponding
``propose_*`` tool for anything a deployment wants a human to approve first; these are for personas
trusted to act without asking.

Create and list only, for now -- update and delete are named follow-up (Phase C exit criteria),
not silently out of scope: adding them is the same shape of change as create, once a deployment
actually needs a kernel that revises its own writes rather than only accumulating them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..domain.identifiers import PersonalOsRecordId
from ..domain.personal_os import PersonalOsRecord, RecordKind
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import ToolContext
from ..ports.storage import PersonalOsStore
from .base import param, tool

__all__ = ["build_personal_os_crud_tools"]


def _list_summary(records: list[PersonalOsRecord], *, empty: str) -> str:
    if not records:
        return empty
    lines = []
    for r in records:
        summary = ", ".join(f"{k}={v}" for k, v in r.fields.items() if v is not None)
        lines.append(f"- [{r.id.value}] {summary} (created {r.created_at.date().isoformat()})")
    return "\n".join(lines)


def _domain(
    *, kind: RecordKind, noun: str, plural: str, create_params, extra_fields=(),
):
    """One (create, list) tool pair for a personal-OS record kind.

    A closure factory rather than four hand-written pairs: the CRUD logic is identical across
    domains (put a record, list this owner's records of this kind); only the tool's declared
    parameters and description differ, which is exactly what varies between the arguments passed
    here.
    """

    def register(builder: "_Builder") -> None:
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
            fields = {k: v for k, v in kwargs.items() if v is not None}
            record = PersonalOsRecord(
                id=PersonalOsRecordId.new(), owner_id=context.user_id, kind=kind,
                fields=fields, created_at=now, updated_at=now,
                source=f"agent:{context.agent_id}" if context.agent_id else "kernel",
            )
            await builder.store.put(record)
            return f"Created {noun} {record.id.value}."

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

        builder.tools.append(create)
        builder.tools.append(list_records)

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
        ),
    ),
)


def build_personal_os_crud_tools(store: PersonalOsStore) -> list:
    """The create/list tool pair for every personal-OS record kind, backed by one store."""
    builder = _Builder(store)
    for register in _DOMAINS:
        register(builder)
    return builder.tools
