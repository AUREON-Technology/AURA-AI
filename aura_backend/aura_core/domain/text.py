"""Text folding for the few rules that match on what a person wrote.

Stdlib only, like the rest of ``domain``. Two users: the intent and claim matchers, which must
read Arabic as people type it -- with or without diacritics, with any of the alef forms -- and
the ``remember_fact`` check, which asks whether a fact restates words the user actually wrote.

These are heuristics, and every caller treats them as such: a miss falls back to the behaviour
the code had before, never to something stricter than the model's own answer.
"""

from __future__ import annotations

import re
from typing import Iterable

__all__ = ["fold_arabic", "content_terms", "restates"]

#: Harakat, Quranic marks, the superscript alef and tatweel: none changes what a word is.
_ARABIC_MARKS = re.compile(r"[ؐ-ًؚ-ٰٟـ]")
#: Hamza-carrying and wasla alefs fold to the bare alef; final ta marbuta and alef maqsura to the
#: letters people often type instead.
_ARABIC_FOLDS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ى": "ي",
})


def fold_arabic(text: str) -> str:
    """``text`` with Arabic diacritics removed and letter variants folded; other text as is."""
    return _ARABIC_MARKS.sub("", text or "").translate(_ARABIC_FOLDS)


_WORD = re.compile(r"\w+", re.UNICODE)

#: Words that carry no fact of their own, including the third-person frame ``remember_fact``
#: asks facts to be written in ("The user is ..."). Folded forms for the Arabic ones.
_STOPWORDS = frozenset({
    # English
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with", "from",
    "by", "as", "is", "are", "was", "were", "be", "been", "being", "am", "has", "have", "had",
    "do", "does", "did", "not", "no", "that", "this", "these", "those", "it", "its", "they",
    "them", "their", "he", "him", "his", "she", "her", "hers", "i", "me", "my", "mine", "we",
    "our", "you", "your", "user", "users", "person", "who", "which", "what", "when", "where",
    "very", "really", "also", "just", "so", "than", "then", "there", "here", "about", "into",
    "will", "would", "can", "could", "should", "prefers", "prefer", "likes", "like", "loves",
    "love", "wants", "want", "remember", "please",
    # Arabic (folded)
    "المستخدم", "مستخدم", "انا", "هو", "هي", "هم", "في", "من", "الي", "علي", "عن", "مع", "ان",
    "هذا", "هذه", "ذلك", "التي", "الذي", "كان", "يكون", "لا", "لم", "لن", "ما", "و", "او",
    "يحب", "احب", "يفضل", "افضل", "تذكر", "اني", "لدي", "عنده", "لديه",
})


def _stem(word: str) -> str:
    """A crude, language-agnostic stem: enough that "live"/"lives" and "نباتي"/"النباتي" meet."""
    if len(word) > 4 and word.startswith("ال"):
        word = word[2:]
    elif len(word) > 5 and word[0] in "وفبل" and word[1:3] == "ال":
        word = word[3:]
    return word[:4] if len(word) > 4 else word


def content_terms(text: str) -> frozenset[str]:
    """The stems of the words in ``text`` that could carry a fact."""
    folded = fold_arabic(text).casefold()
    terms: set[str] = set()
    for word in _WORD.findall(folded):
        if word in _STOPWORDS or (len(word) < 3 and not word.isdigit()):
            continue
        terms.add(_stem(word))
    return frozenset(terms)


def restates(statement: str, sources: Iterable[str], *, minimum_share: float = 0.5) -> bool:
    """Whether ``statement`` is mostly made of words from ``sources``.

    At least ``minimum_share`` of the statement's content terms must occur in the sources, and at
    least one must. A statement with no content terms at all ("The user is.") restates nothing.
    """
    wanted = content_terms(statement)
    if not wanted:
        return False
    seen: set[str] = set()
    for source in sources:
        seen |= content_terms(source)
    found = len(wanted & seen)
    return found >= 1 and found / len(wanted) >= minimum_share
