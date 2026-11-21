"""Token estimation, stdlib only (KAN-103/KAN-104).

One estimator for every place that needs a token figure before a provider reports one: the
context engine's budget, the router's context-window check, the quota reservation and a
provider's usage fallback. It lives in the domain so ``Message.approx_tokens`` can use it
without the domain importing an engine; ``context/tokenizer.py`` wraps it behind the
``Tokenizer`` port and adds the exact backends (a Hugging Face ``tokenizer.json``, tiktoken).

The estimate is deliberately biased **high**. A budget built on a low estimate produces prompts
that overflow the context window or a quota, which the user sees as a failed turn; a high one
only wastes a little room. That is why this replaces the flat ``len/4`` the router, the quota
and the providers' fallbacks used: ``len/4`` counted 2,020 characters of Arabic as 505 tokens
and 1,220 characters of code as 305, both well under what any BPE vocabulary spends on them.

Per-family profiles (:data:`PROFILES`) differ from the generic one only where the family's
pre-tokenizer is publicly documented and the difference is mechanical:

* ``qwen2``  -- Qwen2/Qwen2.5 split every digit into its own token.
* ``o200k``, ``cl100k`` -- tiktoken-style pre-tokenizers group digits up to three per token.
* ``claude`` -- no public tokenizer; the generic heuristic with a larger safety margin.

Nothing else in a profile is a measured ratio: no reference tokenizer for these families is
available offline here, so letters, punctuation and Arabic keep the generic, pessimistic costs.
A deployment that wants exact counts points the provider at its ``tokenizer.json``
(``AURA_PROVIDER_<NAME>_TOKENIZER=qwen2@/path/tokenizer.json``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Mapping

__all__ = [
    "MESSAGE_OVERHEAD",
    "REPLY_PRIMING",
    "TokenProfile",
    "PROFILES",
    "GENERIC",
    "TokenCounter",
    "estimate_tokens",
    "profile_named",
    "infer_family",
]

#: Tokens of framing per chat message (role marker, separators). Matches the ~4-token overhead
#: OpenAI documents for chat formats; other chat templates are within one or two of it.
MESSAGE_OVERHEAD = 4
#: Tokens that prime the assistant's reply after the last message.
REPLY_PRIMING = 3

#: A text -> token-count function: what callers that must stay stdlib-only are handed.
TokenCounter = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class TokenProfile:
    """Per-unit costs for one tokenizer family. Every default is the generic heuristic's."""

    name: str
    #: Characters of Arabic per token. Pessimistic: measured BPE ratios run ~2.5-4 for current
    #: multilingual vocabularies and lower for English-centric ones.
    arabic_chars_per_token: float = 2.0
    #: Characters per token for letters of every other non-Latin, non-Arabic, non-CJK script
    #: (Cyrillic, Greek, Hebrew, Devanagari, Thai, ...). Before this they counted as nothing.
    other_chars_per_token: float = 2.0
    #: Digits per token. BPE splits digits far more finely than letters.
    digits_per_token: float = 2.0
    #: Tokens per CJK character.
    cjk_per_char: float = 1.0
    #: Tokens per punctuation or symbol character (underscores included: code is full of them).
    punctuation: float = 0.85
    #: Tokens per newline, and per run of two or more spaces/tabs (code indentation).
    newline: float = 0.5
    whitespace_run: float = 1.0
    #: Multiplier on the whole count. >1 on purpose: see the module docstring.
    safety: float = 1.05


GENERIC = TokenProfile(name="generic")

PROFILES: Mapping[str, TokenProfile] = {
    "generic": GENERIC,
    "qwen2": TokenProfile(name="qwen2", digits_per_token=1.0),
    "o200k": TokenProfile(name="o200k", digits_per_token=3.0),
    "cl100k": TokenProfile(name="cl100k", digits_per_token=3.0),
    "claude": TokenProfile(name="claude", safety=1.15),
}


def profile_named(name: str | None) -> TokenProfile:
    """The profile for ``name``; the generic one for an unknown or empty name."""
    return PROFILES.get((name or "").strip().lower(), GENERIC)


#: Substrings of a model id that identify its tokenizer family. Model names only -- never a
#: host or a vendor endpoint (tests/architecture/test_no_external_inference.py).
_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("qwen", "qwen2"),
    ("gpt-oss", "o200k"),
    ("claude", "claude"),
)


def infer_family(model_id: str | None) -> str:
    """Best-effort tokenizer family from a model id; ``generic`` when nothing matches."""
    lowered = (model_id or "").lower()
    for needle, family in _FAMILY_HINTS:
        if needle in lowered:
            return family
    return "generic"


_LATIN = "A-Za-zÀ-ɏ"
_ARABIC_BLOCKS = "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
_CJK_RANGES = "぀-ヿ㐀-䶿一-鿿가-힯"

_WORD = re.compile(rf"[{_LATIN}]+(?:'[{_LATIN}]+)?")
_DIGIT_RUN = re.compile(r"\d+")
_PUNCT = re.compile(r"[^\w\s]|_")
_CJK = re.compile(rf"[{_CJK_RANGES}]")
#: Arabic letter runs (main block letters, extended blocks and presentation forms; harakat are
#: left to ``_PUNCT``, which counts them high). Costed per letter, not per word: vocabularies
#: trained mostly on English split an Arabic word into far more pieces than an English one
#: (docs/plans/arabic-language_plan.md, stage 1).
_ARABIC = re.compile(
    r"[ؠ-يٮ-ۓۺ-ۿݐ-ݿࢠ-ࣉﭐ-ﷻﹰ-ﻼ]+"
)
#: Letters of any other script: word characters that are not digits, underscores, Latin,
#: Arabic or CJK.
_OTHER_LETTERS = re.compile(rf"[^\W\d_{_LATIN}{_ARABIC_BLOCKS}{_CJK_RANGES}]+")
_WHITESPACE_RUN = re.compile(r"[ \t]{2,}")


def estimate_tokens(text: str, profile: TokenProfile = GENERIC) -> int:
    """Estimated BPE tokens in ``text`` under ``profile``. 0 for empty text, otherwise >= 1.

    Counts the units BPE actually produces rather than characters: whitespace-delimited Latin
    words (long ones cost extra sub-word pieces), punctuation and symbols (almost always their
    own token), digit runs, CJK characters (about one each), letters of Arabic and other
    scripts (per character), newlines and indentation runs.
    """
    if not text:
        return 0

    tokens = len(_CJK.findall(text)) * profile.cjk_per_char

    for word in _WORD.findall(text):
        length = len(word)
        if length <= 4:
            tokens += 1.0
        elif length <= 8:
            tokens += 1.6
        else:
            # Long words break into roughly one piece per 3.5 characters.
            tokens += 1.0 + (length - 4) / 3.5

    for run in _ARABIC.findall(text):
        tokens += max(1.0, len(run) / profile.arabic_chars_per_token)

    for run in _OTHER_LETTERS.findall(text):
        tokens += max(1.0, len(run) / profile.other_chars_per_token)

    for run in _DIGIT_RUN.findall(text):
        tokens += max(1.0, len(run) / profile.digits_per_token)

    tokens += len(_PUNCT.findall(text)) * profile.punctuation
    tokens += text.count("\n") * profile.newline
    tokens += len(_WHITESPACE_RUN.findall(text)) * profile.whitespace_run

    return max(1, int(tokens * profile.safety + 0.5))
