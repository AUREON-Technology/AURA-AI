"""Turn assistant prose into something a synthesizer can read *like a person would*.

Espeak-ng and Piper will vocalise markdown, URLs, and abbreviations as written, which is why a
reply that looks fine on screen can sound like noise. This module is the last rewrite before
bytes are synthesised -- punctuation stays (it is how both engines pause), markup does not.

Two things here are what stop the result sounding robotic, and they matter more than any engine
flag:

*Pause structure.* Both engines pause only at punctuation. Markdown carries its structure in
newlines and bullet markers, so deleting those and collapsing the whitespace -- which is what
this module used to do -- turns a heading plus four bullets into one breathless run-on clause.
``_punctuate_structure`` converts each structural boundary into a sentence terminator instead,
so the engine has somewhere to breathe. Measured against the real Piper binary, this is worth
roughly +55% wall-clock on a structured reply, versus +0.6% for the prosody flags alone: the
flags have nothing to act on until the boundaries exist.

*Word order.* Espeak's own number handling is good and is deliberately left alone -- it already
reads ``3:30 PM``, ``3rd``, ``15%`` and ``1000000`` correctly. Only two constructs come out
wrong, and only those are rewritten: ``$1,250.50`` (the symbol is read first, and the cents as
digits -- "dollar one thousand two hundred fifty point five zero") and ISO dates like
``2026-08-25`` ("two thousand twenty six dash zero eight dash twenty five").

English expansions (``e.g.`` -> "for example") must not run on Arabic replies: they Latinize
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
_HEADING = re.compile(r"^#{1,6}\s+")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"\*(\S+?)\*")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_WHITESPACE = re.compile(r"\s+")
_MULTI_PUNCT = re.compile(r"([.!?؟。]){2,}")

# Latin period, ASCII ?!, Arabic question/semicolon, ideographic full stop.
_SENTENCE_ENDINGS = frozenset(".!?؟؛。")
# Trailing separators that a structural terminator should *replace* rather than follow --
# "Send it,." is worse than the comma was.
_CLAUSE_TRAILERS = ",;:،"

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# symbol -> (major singular, major plural, minor singular, minor plural)
_CURRENCY_NAMES: dict[str, tuple[str, str, str, str]] = {
    "$": ("dollar", "dollars", "cent", "cents"),
    "€": ("euro", "euros", "cent", "cents"),
    "£": ("pound", "pounds", "penny", "pence"),
}
# Derived from the table above rather than written out again, so a symbol can never be
# matchable without a name to read it as.
_CURRENCY = re.compile(
    rf"([{re.escape(''.join(_CURRENCY_NAMES))}])\s?(\d[\d,]*)(?:\.(\d{{1,2}}))?"
)

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

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
    (re.compile(r"–|—"), ", "),
)


def _strip_markup(text: str) -> str:
    """Remove markup that is not structural. Headings and bullets survive this pass -- they
    carry the pause structure ``_punctuate_structure`` needs, and are stripped there."""
    text = _FENCE.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _EMAIL.sub(" ", text)
    return text.replace("_", " ")


def _terminate(line: str) -> str:
    """End a structural unit so the engine pauses after it."""
    line = line.rstrip()
    while line and line[-1] in _CLAUSE_TRAILERS:
        line = line[:-1].rstrip()
    if not line:
        return ""
    if line[-1] in _SENTENCE_ENDINGS:
        return line
    return f"{line}."


def _punctuate_structure(text: str) -> str:
    """Convert markdown structure into sentence terminators.

    Lines are gathered into blocks and terminated once per block, not once per line. A block
    ends at a blank line, a heading, or the start of a new list item -- so a soft-wrapped
    paragraph stays one sentence ("The invoice is ready and. the total..." is exactly the
    artefact this is meant to remove) while a lead-in line before a list still gets its pause.
    """
    blocks: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        line = _terminate(" ".join(pending))
        pending.clear()
        if line:
            blocks.append(line)

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if _HEADING.match(line):
            flush()
            heading = _HEADING.sub("", line).strip()
            if heading:
                pending.append(heading)
                flush()
            continue
        if _BULLET.match(line):
            flush()
            item = _BULLET.sub("", line).strip()
            if item:
                pending.append(item)
            continue
        # A plain line continues whatever block is open -- a paragraph or a wrapped list item.
        pending.append(line)

    flush()
    return " ".join(blocks)


def _ordinal(day: int) -> str:
    """``25`` -> ``25th``. Espeak reads the suffixed form as "twenty fifth", which is how a
    person says a date; the bare number comes out as "twenty five"."""
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def _spoken_currency(match: re.Match[str]) -> str:
    """``$1,250.50`` -> ``1250 dollars and 50 cents``.

    Reordering is the whole point: espeak reads the symbol where it sits, so the amount has to
    come first, and a decimal has to become a minor unit rather than "point five zero"."""
    symbol, whole, minor = match.group(1), match.group(2), match.group(3)
    major_sg, major_pl, minor_sg, minor_pl = _CURRENCY_NAMES[symbol]
    amount = int(whole.replace(",", ""))
    spoken = f"{amount} {major_sg if amount == 1 else major_pl}"
    if minor is not None:
        # ".5" means fifty cents, not five -- pad before reading.
        minor_amount = int(minor.ljust(2, "0"))
        if minor_amount:
            spoken += f" and {minor_amount} {minor_sg if minor_amount == 1 else minor_pl}"
    return spoken


def _spoken_iso_date(match: re.Match[str]) -> str:
    """``2026-08-25`` -> ``August 25th, 2026``. Left as written if it is not a real date, so a
    version string or an identifier is not mangled into a month."""
    year, month, day = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return match.group(0)
    return f"{_MONTHS[month - 1]} {_ordinal(day)}, {year}"


def _verbalize_english(text: str) -> str:
    """Rewrite the two constructs espeak gets wrong. Everything else it reads correctly and is
    deliberately left untouched."""
    text = _ISO_DATE.sub(_spoken_iso_date, text)
    return _CURRENCY.sub(_spoken_currency, text)


def prepare_spoken_text(raw: str) -> str:
    """Return speakable prose, or ``""`` when nothing remains.

    Language is detected *after* markup and URLs are stripped so a Latin URL in an Arabic
    reply cannot flip the expansions on. Tashkeel is left intact.
    """
    text = raw.replace("\r\n", "\n").strip()
    if not text:
        return ""

    text = _strip_markup(text)
    text = _punctuate_structure(text)
    language = detect_speech_language(text)
    if language == "en":
        text = _verbalize_english(text)
        for pattern, spoken in _ABBREVIATIONS:
            text = pattern.sub(spoken, text)

    text = _MULTI_PUNCT.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return ""
    if text[-1] not in _SENTENCE_ENDINGS:
        text = f"{text}."
    return text
