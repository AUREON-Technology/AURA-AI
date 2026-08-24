"""Server-authored personal-OS records (Phase C2).

Deliberately a *different* store from the legacy ``tasks``/``calendar_events``/``finance_records``/
``health_records`` tables (``infrastructure/db/models.py``). Those columns are zero-knowledge
encrypted -- the client holds the only key, and the server is contractually forbidden to read
them (``aura_client/lib/core/security/encryption_service.dart``). A server-side tool has no key to
encrypt with, so it cannot write valid ciphertext there; writing plaintext into a column the whole
architecture promises never contains any would not degrade that guarantee, it would invert it.

This module is the alternative: a plain, server-readable table the kernel owns outright, exactly
like ``aura_memories``/``aura_documents`` already are (``aura_core/adapters/postgres_store.py``).
A record here is visible to the kernel that wrote it and to server-side tools that read it back;
it is not (yet) synced to the Flutter client's local database or existing UI -- that needs its own
endpoint and is named as follow-up work, not implied by this module's existence.
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
