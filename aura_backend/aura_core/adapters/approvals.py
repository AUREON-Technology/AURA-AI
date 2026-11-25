"""In-memory :class:`~aura_core.ports.approvals.ApprovalLedger`.

A real implementation, not a mock: the default test run and a single-process deployment use it, and
``tests/load/test_tool_approvals_postgres.py`` holds it and the Postgres store to the same outcomes.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Sequence

from ..domain.approvals import ApprovalStatus, ToolApproval
from ..domain.identifiers import UserId

__all__ = ["InMemoryApprovalLedger"]


def matches(row: ToolApproval, *, now: datetime, status: ApprovalStatus | None) -> bool:
    return status is None or row.effective_status(now) is status


class InMemoryApprovalLedger:
    def __init__(self) -> None:
        self._rows: dict[str, ToolApproval] = {}
        self._lock = asyncio.Lock()

    async def record_held(self, approvals: Sequence[ToolApproval]) -> None:
        async with self._lock:
            for approval in approvals:
                self._rows.setdefault(approval.id, approval)

    async def decide(
        self, owner_id: UserId, ids: Sequence[str], status: ApprovalStatus, *, at: datetime, via: str,
    ) -> int:
        moved = 0
        async with self._lock:
            for approval_id in dict.fromkeys(ids):
                row = self._rows.get(approval_id)
                if row is None or row.owner_id != owner_id or row.status is not ApprovalStatus.PENDING:
                    continue
                self._rows[approval_id] = replace(row, status=status, decided_at=at, decided_via=via)
                moved += 1
        return moved

    async def get(self, owner_id: UserId, approval_id: str) -> ToolApproval | None:
        row = self._rows.get(approval_id)
        return row if row is not None and row.owner_id == owner_id else None

    async def list(
        self,
        owner_id: UserId,
        *,
        now: datetime,
        status: ApprovalStatus | None = None,
        tool: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> Sequence[ToolApproval]:
        rows = [
            r for r in self._rows.values()
            if r.owner_id == owner_id
            and matches(r, now=now, status=status)
            and (tool is None or r.tool == tool)
            and (since is None or r.created_at >= since)
            and (until is None or r.created_at < until)
            and (before is None or (r.created_at, r.id) < before)
        ]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return rows[:limit]

    async def delete_for_owner(self, owner_id: UserId) -> int:
        async with self._lock:
            mine = [k for k, r in self._rows.items() if r.owner_id == owner_id]
            for key in mine:
                del self._rows[key]
        return len(mine)
