"""Shallow intent labels for Phase I.

Regex-only hints. Informational metadata for callers and future routing experiments — **not** a
replacement for ``AIKernel`` planning or tool selection.
"""

from __future__ import annotations

import re
from typing import Mapping

__all__ = ["detect_shallow_intent"]

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greet", re.compile(r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening))\b", re.I)),
    ("farewell", re.compile(r"\b(bye|goodbye|see\s+you|talk\s+later)\b\s*[.!]?\s*$", re.I)),
    ("confirm", re.compile(r"^\s*(yes|yeah|yep|ok|okay|sure|confirm|do\s+it)\b", re.I)),
    ("reject", re.compile(r"^\s*(no|nope|cancel|never\s*mind|don't|do\s+not)\b", re.I)),
    (
        "question",
        re.compile(
            r"(\?|^(what|who|when|where|why|how|which|is|are|can|could|would|will|do|does)\b)",
            re.I,
        ),
    ),
    (
        "schedule",
        re.compile(r"\b(schedule|remind|calendar|meeting|appointment|tomorrow|next week)\b", re.I),
    ),
    ("search", re.compile(r"\b(search|find|look\s+up|what\s+do\s+you\s+know)\b", re.I)),
)


def detect_shallow_intent(text: str) -> Mapping[str, object]:
    """Return ``{"label": str | None, "confidence": float}``.

    First matching pattern wins. Confidence is a fixed heuristic (0.55–0.7), never calibrated.
    """
    stripped = (text or "").strip()
    if not stripped:
        return {"label": None, "confidence": 0.0}
    for label, pattern in _PATTERNS:
        if pattern.search(stripped):
            return {"label": label, "confidence": 0.65}
    return {"label": None, "confidence": 0.0}
