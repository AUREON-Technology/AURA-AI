"""Transcript / message normalization (Phase I).

Pure functions over ``str``. Deterministic, no I/O, no models. Intended for ASR transcripts and
typed chat so both share one preprocess path before ``TurnRequest``.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_transcript"]

# Common English ASR / filler tokens collapsed when they stand alone as words.
_FILLERS = frozenset(
    {
        "uh",
        "um",
        "uhm",
        "erm",
        "ah",
        "eh",
        "hmm",
        "hm",
        "mhm",
        "mm",
        "like",  # only stripped when repeated; see below
    }
)

_MULTI_SPACE = re.compile(r"\s+")
_REPEATED_FILLER = re.compile(
    r"\b(?:uh|um|uhm|erm|ah|eh|hmm|hm|mhm|mm)(?:\s+(?:uh|um|uhm|erm|ah|eh|hmm|hm|mhm|mm))+\b",
    re.IGNORECASE,
)
_STUTTER = re.compile(r"\b(\w{1,12})\s+\1\b", re.IGNORECASE)


def normalize_transcript(text: str) -> str:
    """Return a cleaned transcript suitable for ``TurnRequest.message``.

    - Unicode NFKC
    - Strip control characters (keep newlines as spaces)
    - Collapse repeated fillers (``um um um`` → removed)
    - Collapse immediate word stutters (``the the`` → ``the``) once
    - Collapse whitespace; strip ends

    Empty input stays empty. Does not invent content and does not translate.
    """
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = "".join(
        ch if (ch == "\n" or unicodedata.category(ch)[0] != "C") else " " for ch in cleaned
    )
    cleaned = cleaned.replace("\n", " ")
    cleaned = _REPEATED_FILLER.sub(" ", cleaned)
    # Single leading/trailing fillers only — keep mid-sentence "like" as content.
    tokens = cleaned.split()
    while tokens and tokens[0].lower().strip(".,!?") in _FILLERS - {"like"}:
        tokens.pop(0)
    while tokens and tokens[-1].lower().strip(".,!?") in _FILLERS - {"like"}:
        tokens.pop()
    cleaned = " ".join(tokens)
    cleaned = _STUTTER.sub(r"\1", cleaned)
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip()
    return cleaned
