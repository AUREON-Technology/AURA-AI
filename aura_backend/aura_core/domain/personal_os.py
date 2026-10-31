"""Personal-OS records: tasks, calendar events, finance and health records (Phase C2; ADR-032).

The domain view of one of the user's items as the agent reads and writes it. Since ADR-032 these
are the app's own synced rows (``tasks``/``calendar_events``/``finance_records``/
``health_records``, ``infrastructure/db/models.py``): their text fields are encrypted with a
per-user data key that the user's devices *and the server* hold, so the agent can create, read,
update and delete the user's real items and the app shows them after its next sync. That ended
the zero-knowledge property these tables used to have; the threat-model change is recorded in
``docs/architecture/032-personal-os-single-store.md``.

Before ADR-032 the agent wrote a separate server-only table (``aura_personal_os_records``) the app
never read; ``scripts/move_personal_os_records.py`` moves those rows across.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .identifiers import PersonalOsRecordId, UserId

__all__ = ["RecordKind", "PersonalOsRecord"]


class RecordKind(str, enum.Enum):
    TASK = "task"
    CALENDAR_EVENT = "calendar_event"
    FINANCE_RECORD = "finance_record"
    HEALTH_RECORD = "health_record"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PersonalOsRecord:
    """One task, calendar event, finance record, or health record.

    ``fields`` is a plain JSON-shaped mapping rather than one dataclass per kind: the four
    domains' fields differ enough (a task's due date, an event's start/end, a finance record's
    amount and type, a health record's several optional metrics) that forcing them into one
    shared shape would mean a table half full of nulls, and forcing four separate dataclasses
    through one store's CRUD methods would mean four near-identical adapters. The *tools* that
    write ``fields`` are strongly typed and per-domain (``aura_core/tools/records.py``); this is
    the storage-side generalisation underneath them.
    """

    id: PersonalOsRecordId
    owner_id: UserId
    kind: RecordKind
    fields: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime
    source: str = "kernel"

    def with_fields(self, fields: Mapping[str, Any], *, updated_at: datetime) -> "PersonalOsRecord":
        return PersonalOsRecord(
            id=self.id, owner_id=self.owner_id, kind=self.kind,
            fields={**self.fields, **fields}, created_at=self.created_at,
            updated_at=updated_at, source=self.source,
        )
