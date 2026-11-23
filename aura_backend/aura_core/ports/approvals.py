"""The durable record of held tool calls and how each was decided (``domain/approvals.py``)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from ..domain.approvals import ApprovalStatus, ToolApproval
from ..domain.identifiers import UserId

__all__ = ["ApprovalLedger"]


@runtime_checkable
class ApprovalLedger(Protocol):
    """Every statement is scoped to the owner: another person's id answers as a missing one."""

    async def record_held(self, approvals: Sequence[ToolApproval]) -> None:
        """Store newly held calls (status ``pending``). Idempotent on ``id``."""
        ...

    async def decide(
        self, owner_id: UserId, ids: Sequence[str], status: ApprovalStatus, *, at: datetime, via: str,
    ) -> int:
        """Move the owner's still-``pending`` rows among ``ids`` to ``status``; returns how many
        moved. A row already decided is left as it is -- a decision is never rewritten."""
        ...

    async def get(self, owner_id: UserId, approval_id: str) -> ToolApproval | None:
        ...

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
        """Newest first by ``(created_at, id)``; ``before`` is the position of the last row of the
        previous page. ``status`` is the *effective* one: ``pending`` excludes rows past their hour
        and ``expired`` is exactly those."""
        ...

    async def delete_for_owner(self, owner_id: UserId) -> int:
        """Account erasure."""
        ...
