"""Read-only goal snapshot for the context engine and planning tools (Phase D2).

``Goal.title_enc``/``description_enc`` (``infrastructure/db/models.py``) are zero-knowledge
ciphertext the backend is contractually forbidden to read -- the same constraint that applies to
every other syncable table. :class:`GoalSnapshot` carries only the columns that table stores in
plaintext: enough for the kernel to reason about *which* goals exist, how urgent and important
they are, and how far along they are, without ever seeing what a goal is actually about.
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
