"""In-memory :class:`~aura_core.ports.coding.EngineerConsentStore` (module readiness WP1).

The real substrate for tests and a single-process local run; production uses
``postgres_engineer_consent.py``. Shared by reference, two kernels on one instance behave like
two gateway workers on one table.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..domain.engineering import EngineerConsent
from ..domain.identifiers import UserId

__all__ = ["InMemoryEngineerConsentStore"]


class InMemoryEngineerConsentStore:
    def __init__(self) -> None:
        self._rows: dict[str, EngineerConsent] = {}

    async def get(self, user_id: UserId) -> EngineerConsent | None:
        return self._rows.get(user_id.value)

    async def accept(self, user_id: UserId, notice_version: str, *, at: datetime) -> EngineerConsent:
        consent = EngineerConsent(user_id=user_id, notice_version=notice_version, accepted_at=at)
        self._rows[user_id.value] = consent
        return consent

    async def withdraw(self, user_id: UserId, *, at: datetime) -> EngineerConsent | None:
        current = self._rows.get(user_id.value)
        if current is None:
            return None
        if current.withdrawn_at is None:
            current = replace(current, withdrawn_at=at)
            self._rows[user_id.value] = current
        return current

    async def delete_for_owner(self, user_id: UserId) -> int:
        return 1 if self._rows.pop(user_id.value, None) is not None else 0
