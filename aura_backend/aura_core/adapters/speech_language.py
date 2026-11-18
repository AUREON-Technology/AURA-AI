"""Detect which TTS path a reply should take from the text itself.

The client already posts the assistant message to ``POST /api/v3/voice/speak``. Script
detection on that string is enough: if the user wrote Arabic, the model almost always
replies in Arabic, and mixed replies that are still Arabic-majority should sound Arabic.
"""

from __future__ import annotations

from ..text import dominant_script

__all__ = ["detect_speech_language"]


def detect_speech_language(text: str) -> str:
    """Return ``"ar"`` or ``"en"`` from letter-script counts.

    Arabic wins when the Arabic-script count is greater than zero **and** at least the
    Latin count. Empty, punctuation-only, and Latin-majority strings are English. The rule
    lives in ``aura_core/text/arabic.py`` so the reply-language hint uses the same one.
    """
    return dominant_script(text) or "en"
