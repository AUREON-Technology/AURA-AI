"""Is a fact the model wants to remember something the user actually said?

``remember_fact`` writes durable memory that is recalled into every later prompt. Its description
tells the model to store only what the user *stated about themselves* and never what it inferred,
but a description is a request, not a check: a model that paraphrases generously, guesses, or has
been steered by a retrieved document can still write "the user is a vegetarian" after the user said
nothing of the kind. This module is the check -- deterministic, stdlib-only, no model call.

The rule is deliberately about *words*, not meaning. A fact is supported when the user's own message
contains its substance:

* every number in the fact appears in the message (an age, a date, a phone digit group is never
  "close enough");
* at least :data:`MIN_SUPPORTED_SHARE` of the fact's other content words appear in the message,
  where "appear" tolerates inflection ("lives" for "live", "vegetarian" for "vegetarians") by
  comparing a shared stem, and where filler such as "the user", "is", "has" is ignored.

So "The user lives in Cairo" is supported by "I live in Cairo", and "The user is a vegetarian" is
not supported by "I had a salad". A synonym ("does not eat meat" for "vegetarian") is *not*
supported: the answer is to write the fact in the user's own words, which is also what makes it
auditable later. English and Arabic are handled; for any other script the words are compared as
they are.

Which messages count is the caller's business (``kernel`` passes the user's own message for the
turn, never retrieved text or a delegating agent's task). ``None`` from the caller means "cannot be
checked", which this module never turns into a pass or a fail by itself.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = ["FactSupport", "check_fact_support", "MIN_SUPPORTED_SHARE"]

#: Share of a fact's content words that must appear in what the user said. High enough that one
#: borrowed word ("cairo") cannot carry an invented sentence, low enough that a third-person
#: rewording ("works as", "based in") of a real statement still passes.
MIN_SUPPORTED_SHARE = 0.7

#: Words that carry no claim. "User" and the pronouns are how a fact is *phrased* ("written in the
#: third person"), not what it says.
_FILLER = frozenset(
    """
    a an the and or but of to in on at for with by from as is are was were be been being am
    it its this that these those there their they them he she his her him i me my mine we our us
    you your user users person someone has have had do does did not no
    also very really just about into onto than then so such
    year years old age aged
    المستخدم المستخدمة هو هي هم في من على الى إلى عن مع ان أن إن كان كانت و او أو ثم
    """.split()
)

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_ARABIC_MARKS = re.compile("[ً-ٰٟـ]")  # diacritics and tatweel
_STEM_LEN = 5


@dataclass(frozen=True, slots=True)
class FactSupport:
    supported: bool
    #: Content words of the fact the user's message does not contain (numbers first).
    missing: tuple[str, ...] = ()
    #: Share of content words found, 0.0-1.0 (1.0 when the fact has no content words to check).
    share: float = 1.0

    def explain(self) -> str:
        if self.supported:
            return "supported by what the user said"
        shown = ", ".join(repr(w) for w in self.missing[:6])
        return (
            f"not supported by what the user said this turn (missing: {shown}). Only store what "
            "the user stated about themselves, in their own words: quote it, do not paraphrase "
            "or infer. If you are unsure, ask the user instead of storing it."
        )


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = _ARABIC_MARKS.sub("", text)
    # Alef variants and ta marbuta/ya spellings differ between writers of the same word.
    return text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))


_FILLER_NORMAL = frozenset(_normalise(w) for w in _FILLER)


def _words(text: str) -> list[str]:
    return _WORD.findall(_normalise(text))


_ARABIC = re.compile("[\u0600-\u06ff]")


def _stem(word: str) -> str:
    if word.isdigit():
        return word
    if _ARABIC.search(word):
        # "اسكن" (I live) and "يسكن" (he lives) are one verb: drop the article and the person prefix.
        if word.startswith("ال") and len(word) > 4:
            word = word[2:]
        if len(word) >= 4 and word[0] in "ايتن":
            word = word[1:]
        return word[:_STEM_LEN]
    # Trailing plural/verb endings first, so "lives"/"live" and "vegetarians"/"vegetarian" meet.
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word[:_STEM_LEN]


def check_fact_support(fact: str, said: str) -> FactSupport:
    """Whether ``fact`` is supported by ``said`` (what the user wrote). See the module docstring."""
    said_words = _words(said)
    said_stems = {_stem(w) for w in said_words}
    said_numbers = {w for w in said_words if w.isdigit()}

    content = [w for w in _words(fact) if w not in _FILLER_NORMAL]
    if not content:
        # Nothing but filler: there is no claim to support, and one is not invented for it.
        return FactSupport(True)

    numbers = [w for w in content if w.isdigit()]
    words = [w for w in content if not w.isdigit()]
    missing_numbers = tuple(dict.fromkeys(n for n in numbers if n not in said_numbers))
    missing_words = tuple(dict.fromkeys(w for w in words if _stem(w) not in said_stems))
    word_share = (len(words) - sum(1 for w in words if w in missing_words)) / len(words) if words else 1.0
    found = len(content) - len(missing_numbers) - sum(1 for w in words if w in missing_words)
    return FactSupport(
        supported=not missing_numbers and word_share >= MIN_SUPPORTED_SHARE,
        missing=missing_numbers + missing_words,
        share=found / len(content),
    )
