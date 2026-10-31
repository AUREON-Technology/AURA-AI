"""Read-only goal snapshot for the context engine and planning tools (Phase D2).

``Goal.title_enc``/``description_enc`` (``infrastructure/db/models.py``) are ciphertext. Since
ADR-032 they are sealed with the user's data key, which the server holds, so they are no longer
zero-knowledge -- but this snapshot was designed before that and still carries only the columns the
table stores in plaintext: enough for the kernel to reason about *which* goals exist, how urgent
and important they are, and how far along they are. Reading goal text is not wired (no port
decrypts it); see ADR-032's residual risks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["GoalSnapshot"]


@dataclass(frozen=True, slots=True)
class GoalSnapshot:
    id: str
    category: str | None
    status: str
    priority: str
    target_date: datetime | None
    progress_pct: int
