"""Turn assistant prose into something a synthesizer can read clearly.

Espeak-ng and Piper will vocalise markdown, URLs, and abbreviations as written, which is why a
reply that looks fine on screen can sound like noise. This module is the last rewrite before
bytes are synthesised -- punctuation stays (it is how both engines pause), markup does not.

English expansions (``e.g.`` → "for example") must not run on Arabic replies: they Latinize
the text. Arabic sentence endings (``؟`` ``؛`` ``。``) already tell the synthesizer to pause,
so a Latin period is not forced on top.
"""

from __future__ import annotations

import re

from .speech_language import detect_speech_language

__all__ = ["prepare_spoken_text"]

_FENCE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.MULTILINE)
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"\*(\S+?)\*")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_WHITESPACE = re.compile(r"\s+")
_MULTI_PUNCT = re.compile(r"([.!?؟。]){2,}")

# Latin period, ASCII ?!, Arabic question/semicolon, ideographic full stop.
_SENTENCE_ENDINGS = frozenset(".!?؟؛。")

# Spoken expansions, applied before leftover punctuation is collapsed. Longer keys first
# so "e.g." is not eaten as a stray "e". English-only -- skipped for Arabic-script replies.
_ABBREVIATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\be\.g\.", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.e\.", re.IGNORECASE), "that is"),
    (re.compile(r"\betc\.", re.IGNORECASE), "et cetera"),
    (re.compile(r"\bvs\.", re.IGNORECASE), "versus"),
    (re.compile(r"\bvs\b", re.IGNORECASE), "versus"),
    (re.compile(r"\bapprox\.", re.IGNORECASE), "approximately"),
    (re.compile(r"\bDr\.", re.IGNORECASE), "Doctor"),
    (re.compile(r"\bMr\.", re.IGNORECASE), "Mister"),
    (re.compile(r"\bMrs\.", re.IGNORECASE), "Missus"),
    (re.compile(r"\bMs\.", re.IGNORECASE), "Miss"),
    (re.compile(r"\bFYI\b"), "for your information"),
    (re.compile(r"\bOK\b"), "okay"),
    (re.compile(r"\bAURA\b"), "Aura"),
    (re.compile(r"%"), " percent"),
    (re.compile(r"&"), " and "),
    (re.compile(r"\u2013|\u2014"), ", "),
)


def _strip_markup(text: str) -> str:
    text = _FENCE.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _EMAIL.sub(" ", text)
    return text.replace("_", " ")


def prepare_spoken_text(raw: str) -> str:
    """Return speakable prose, or ``""`` when nothing remains.

    Language is detected *after* markup and URLs are stripped so a Latin URL in an Arabic
    reply cannot flip the expansions on. Tashkeel is left intact.
    """
    text = raw.replace("\r\n", "\n").strip()
    if not text:
        return ""

    text = _strip_markup(text)
    language = detect_speech_language(text)
    if language == "en":
        for pattern, spoken in _ABBREVIATIONS:
            text = pattern.sub(spoken, text)

    text = _MULTI_PUNCT.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return ""
    if text[-1] not in _SENTENCE_ENDINGS:
        text = f"{text}."
    return text
