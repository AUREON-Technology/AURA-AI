"""Arabic normalisation, script detection and light stemming.

Every piece of code that compares Arabic text needs the same answer to "are these the same
word?", or two components drift apart: the safety guards folded hamza forms while the BM25
tokenizer dropped Arabic altogether, so a document a guard could read was invisible to search.
This module is that one answer (docs/plans/arabic-language_plan.md, stage 1).

* :func:`fold` removes what written Arabic treats as optional (harakat, Quranic marks, tatweel)
  and folds letters writers use interchangeably (hamza-carrying alefs, alef maqsura, teh
  marbuta), and maps Arabic-Indic digits to ASCII. Non-Arabic text passes through unchanged.
* :func:`light_stem` strips the commonest attached prefixes and suffixes (the "light10" family
  of stemmers). It is a retrieval aid, not morphology: it never extracts roots, and it is applied
  identically to documents and queries, so its occasional over-stripping costs precision on a
  few short words rather than producing mismatches.
* :func:`dominant_script` is the rule the voice layer already used (Arabic wins when its count is
  above zero and at least the Latin count), shared so the reply-language hint and the TTS voice
  can never disagree about what language a message is in.
"""

from __future__ import annotations

import re

__all__ = [
    "ARABIC_STOPWORDS",
    "arabic_view",
    "dominant_script",
    "fold",
    "has_arabic",
    "is_arabic_token",
    "light_stem",
]

#: Arabic block + supplement + extended-A + presentation forms: what counts as Arabic *script*
#: for language detection. Tashkeel lives in the main block and is counted on purpose.
_ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[A-Za-z]")

#: Arabic letters proper (hamza through yeh), the alphabet tokens are made of after folding.
_ARABIC_LETTER = re.compile(r"[ء-ي]")
_ARABIC_TOKEN = re.compile(r"^[ء-ي]+$")

#: Harakat, Quranic annotation marks, superscript alef and tatweel: optional in written Arabic.
_MARKS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")

#: Letters written interchangeably in practice, plus Arabic-Indic and Eastern Arabic-Indic digits.
_FOLD = str.maketrans(
    {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه",
        **{chr(0x0660 + d): str(d) for d in range(10)},
        **{chr(0x06F0 + d): str(d) for d in range(10)},
    }
)

#: Function words, already folded. Deliberately short for the same reason the English list is:
#: BM25's IDF already discounts common words, and a long list starts removing meaning.
#: Includes the Egyptian demonstratives and relative pronoun (ده، دي، اللي).
ARABIC_STOPWORDS = frozenset(
    "في من الي علي عن ان ما لا هذا هذه ذلك تلك الذي التي الذين هو هي هم انا انت نحن "
    "كان كانت مع ثم او و ف ب ل ده دي دول اللي".split()
)

#: Attached prefixes, longest first: conjunction/preposition + article, the article, لل.
_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
#: Suffixes in folded form (ية -> يه): feminine/possessive, sound plurals, nisba, dual.
_SUFFIXES = ("ها", "ات", "ون", "ين", "يه", "ان")


def has_arabic(text: str) -> bool:
    """True when ``text`` contains any Arabic-script character."""
    return bool(text) and _ARABIC_SCRIPT.search(text) is not None


def is_arabic_token(token: str) -> bool:
    """True when ``token`` is made only of (folded) Arabic letters."""
    return bool(_ARABIC_TOKEN.match(token))


def fold(text: str) -> str:
    """``text`` with Arabic diacritics and tatweel removed and letter variants and digits folded.

    Text with no Arabic in it is returned unchanged, so this is safe on every input.
    """
    if not text or not has_arabic(text):
        return text
    return _MARKS.sub("", text).translate(_FOLD)


def arabic_view(text: str) -> tuple[str, list[int]] | None:
    """Folded ``text`` plus each kept character's index in the original, so a match on the
    folded view maps back to an accurate span (what the safety guards report). ``None`` if
    there is no Arabic letter at all."""
    if not _ARABIC_LETTER.search(text):
        return None
    kept: list[str] = []
    index: list[int] = []
    for position, char in enumerate(text):
        if _MARKS.match(char):
            continue
        kept.append(char.translate(_FOLD))
        index.append(position)
    return "".join(kept), index


def light_stem(token: str) -> str:
    """Strip one attached prefix and one suffix from a folded Arabic token.

    Each strip keeps at least two letters (the light10 rule), and a leading conjunction و is only
    stripped from words longer than three letters, so short words such as وقت survive intact.
    Non-Arabic tokens are returned unchanged.
    """
    if not is_arabic_token(token):
        return token
    stem = token
    for prefix in _PREFIXES:
        if stem.startswith(prefix) and len(stem) - len(prefix) >= 2:
            stem = stem[len(prefix):]
            break
    else:
        if stem.startswith("و") and len(stem) > 3:
            stem = stem[1:]
    for suffix in _SUFFIXES:
        if stem.endswith(suffix) and len(stem) - len(suffix) >= 2:
            stem = stem[: -len(suffix)]
            break
    return stem


def dominant_script(text: str) -> str | None:
    """``"ar"`` when Arabic script is present and at least as common as Latin letters, ``"en"``
    when Latin letters are present otherwise, ``None`` when the text has neither (a URL-free
    number, an emoji, punctuation)."""
    if not text:
        return None
    arabic = len(_ARABIC_SCRIPT.findall(text))
    latin = len(_LATIN.findall(text))
    if arabic > 0 and arabic >= latin:
        return "ar"
    if latin > 0:
        return "en"
    return None
