"""A cooperative time budget for the ``open`` readers that walk many files or read long PDFs.

Reading a whole 500-page PDF, or a repository of them, legitimately takes a while, so the default is
generous (:data:`DEFAULT_BUDGET_S`) and the cap is a hard :data:`MAX_BUDGET_S`. The budget is checked
*between* units of work (files, members, chunks), never used to cut an input short to fit: when it runs
out the tool stops, says so (``timed_out``) and reports what was left undone. Stdlib only.
"""

from __future__ import annotations

import math
import time
from typing import Any

DEFAULT_BUDGET_S = 300.0
MAX_BUDGET_S = 600.0


def clamp_budget(value: Any, default: float = DEFAULT_BUDGET_S) -> float:
    """``value`` seconds clamped to ``(0, MAX_BUDGET_S]``; anything unusable gives ``default``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        value = default
    return float(min(value, MAX_BUDGET_S))


class Deadline:
    """``Deadline(120).expired()`` is true once 120 s (at most 600) have passed since creation."""

    def __init__(self, seconds: Any = DEFAULT_BUDGET_S) -> None:
        self.seconds = clamp_budget(seconds)
        self._end = time.monotonic() + self.seconds

    def expired(self) -> bool:
        return time.monotonic() >= self._end

    def message(self, what: str) -> str:
        return f"Time budget of {self.seconds:g}s was used up; {what}"
