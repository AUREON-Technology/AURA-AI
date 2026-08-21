"""Feedback persistence port (ADR-008 Loop B).

``/api/v3/feedback`` used to increment a metric and drop the row. The curator
never saw it. This port is the store those rows go into; the in-memory adapter
is the test substrate, and Postgres is selected when the document or memory
backend is postgres.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Protocol, Sequence, runtime_checkable

from ..domain.identifiers import UserId
from .curation import FeedbackRecord

__all__ = ["FeedbackStore", "InMemoryFeedbackStore"]


@runtime_checkable
class FeedbackStore(Protocol):
    async def put(self, record: FeedbackRecord, *, owner_id: UserId) -> None:
        """Persist one rated turn. ``owner_id`` is the tenant; never omitted."""
        ...

    async def list_for_owner(
        self, owner_id: UserId, *, limit: int = 200
    ) -> Sequence[FeedbackRecord]:
        """Return this owner's rows only. There is no unscoped list."""
        ...


class InMemoryFeedbackStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], FeedbackRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: FeedbackRecord, *, owner_id: UserId) -> None:
        owner = owner_id.value
        stored = replace(record, user_ref=record.user_ref or owner)
        async with self._lock:
            self._records[(owner, stored.id)] = stored

    async def list_for_owner(
        self, owner_id: UserId, *, limit: int = 200
    ) -> list[FeedbackRecord]:
        owner = owner_id.value
        matches = [r for (o, _), r in self._records.items() if o == owner]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[:limit]
