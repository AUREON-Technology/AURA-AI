"""Detect which TTS path a reply should take from the text itself.

The client already posts the assistant message to ``POST /api/v3/voice/speak``. Script
detection on that string is enough: if the user wrote Arabic, the model almost always
replies in Arabic, and mixed replies that are still Arabic-majority should sound Arabic.
"""

from __future__ import annotations

import re

__all__ = ["detect_speech_language"]

# Arabic block + supplement + extended-A + presentation forms. Tashkeel lives in the
# main block (U+064B–U+065F) and is counted on purpose -- it is Arabic, not Latin.
_ARABIC = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)
_LATIN = re.compile(r"[A-Za-z]")


def detect_speech_language(text: str) -> str:
    """Return ``"ar"`` or ``"en"`` from letter-script counts.

    Arabic wins when the Arabic-script count is greater than zero **and** at least the
    Latin count. Empty, punctuation-only, and Latin-majority strings are English.
    """
    if not text:
        return "en"
    arabic = len(_ARABIC.findall(text))
    latin = len(_LATIN.findall(text))
    if arabic > 0 and arabic >= latin:
        return "ar"
    return "en"
