"""Tool approvals as a durable, content-free history (``aura_core/execution/confirmations.py``).

When policy holds a side-effecting call for the person's confirmation, the pending turn lives in the
shared cache for an hour and the call gets an ``approval_id``. That is the mechanism. This is its
*record*: one row per held call, so the person can see what is waiting, what they decided and when,
from any device -- the cache cannot be listed, and forgets.

**No content.** A row names the tool, the call id and a *digest* of the call; never the arguments
(they can hold private data and an approval history is long-lived), never the turn's message. To see
what a pending call would do, look at the turn that held it, on the device that received it.

Statuses are what happened to the held call:

* ``pending``    -- waiting, and the cache still holds the turn.
* ``approved``   -- the approval was accepted and the call released to run (not "it succeeded";
  the run ledger and audit say what then happened).
* ``rejected``   -- the person refused; the held turn was discarded and can no longer be approved.
* ``superseded`` -- the turn was resumed with *other* calls approved; this one was held again under
  a fresh approval id (its own row).
* ``expired``    -- never decided within the hour. **Derived, not stored**: a stored ``pending``
  row whose ``expires_at`` has passed.

Stdlib only (ADR-001).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from .identifiers import UserId

__all__ = ["ApprovalStatus", "STORED_STATUSES", "ToolApproval"]


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


#: What a row can actually hold; ``expired`` is derived from ``pending`` plus the clock.
STORED_STATUSES = (
    ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.SUPERSEDED,
)


@dataclass(frozen=True, slots=True)
class ToolApproval:
    """One held call. ``id`` is the ``approval_id`` the client sends back to approve it."""

    id: str
    owner_id: UserId
    tool: str
    call_id: str
    digest: str
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    session_id: str | None = None
    turn_id: str | None = None
    decided_at: datetime | None = None
    decided_via: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STORED_STATUSES:
            raise ValueError(f"{self.status!r} is not a stored approval status")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

    def effective_status(self, now: datetime) -> ApprovalStatus:
        """``expired`` once a still-pending row is past its hour."""
        if self.status is ApprovalStatus.PENDING and now >= self.expires_at:
            return ApprovalStatus.EXPIRED
        return self.status

    def as_dict(self, now: datetime) -> dict[str, Any]:
        return {
            "approval_id": self.id,
            "status": self.effective_status(now).value,
            "tool": self.tool,
            "call_id": self.call_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decided_via": self.decided_via,
        }
