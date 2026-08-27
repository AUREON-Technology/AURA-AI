"""Self-hosted transcript / text NLP (Phase I).

Stdlib-first preprocessing that runs before ``TurnRequest`` construction for chat and live
voice. Engines may import domain + ports (+ other engines); no adapters, no hosted inference.
"""

from .engine import NlpEngine, NlpResult, normalize_for_turn
from .intent import detect_shallow_intent
from .normalize import normalize_transcript

__all__ = [
    "NlpEngine",
    "NlpResult",
    "normalize_for_turn",
    "normalize_transcript",
    "detect_shallow_intent",
]
