"""Token counting.

Nothing in the current system counts tokens. Context is whatever happens to fit, prompts grow
until a provider rejects them, and cost is unmeasurable. Every budgeting decision in the context
engine needs a number, so this module provides one -- exactly when a real tokenizer is
available, and by calibrated estimate when it is not.

Estimation quality matters more than it appears. A budget built on a 30 %-low estimate produces
prompts that overflow the context window, which surfaces as a provider error mid-conversation.
The heuristic here is therefore deliberately biased **high**: over-reserving wastes a little
context, under-reserving breaks the request.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, Sequence, runtime_checkable

__all__ = ["Tokenizer", "HeuristicTokenizer", "TiktokenTokenizer", "get_tokenizer"]


@runtime_checkable
class Tokenizer(Protocol):
    @property
    def name(self) -> str:
        ...

    @property
    def exact(self) -> bool:
        """Whether counts are exact or estimated. Surfaced in telemetry so a budget overrun can
        be attributed to estimation error rather than a logic bug."""
        ...

    def count(self, text: str) -> int:
        ...

    def count_messages(self, messages: Sequence[Any]) -> int:
        ...


class HeuristicTokenizer:
    """Dependency-free estimator, calibrated against BPE behaviour.

    Rather than the usual flat ``len/4``, this counts the units BPE actually tends to produce:

    * whitespace-delimited words, with long words costing extra sub-word pieces;
    * punctuation and symbols, which are almost always their own token;
    * digits, which BPE splits far more finely than letters;
    * CJK characters, which are roughly one token each and are badly served by ``len/4``.

    A fixed per-message overhead accounts for role and formatting framing, which every chat API
    adds and which a naive character count ignores entirely.
    """

    _WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
    _DIGIT_RUN = re.compile(r"\d+")
    _PUNCT = re.compile(r"[^\w\s]")
    _CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
    #: Arabic letter runs (main block letters, extended blocks and presentation forms; harakat are
    #: left to ``_PUNCT``, which counts them high). BPE vocabularies trained mostly on English split
    #: Arabic into far more pieces per word than English, so these are costed per letter, not per
    #: word: before this an Arabic prompt counted as almost nothing and could overflow the context
    #: window the budget was supposed to protect (docs/plans/arabic-language_plan.md, stage 1).
    _ARABIC = re.compile(
        r"[\u0620-\u064A\u066E-\u06D3\u06FA-\u06FF\u0750-\u077F\u08A0-\u08C9\uFB50-\uFDFB\uFE70-\uFEFC]+"
    )
    #: Letters of Arabic per token, deliberately pessimistic (measured BPE ratios run ~2.5-4 for
    #: current multilingual vocabularies and lower for English-centric ones).
    ARABIC_CHARS_PER_TOKEN = 2.0

    #: Tokens of framing per message (role marker, separators). Matches the ~4-token overhead
    #: OpenAI documents for chat formats; other vendors are within one or two of this.
    MESSAGE_OVERHEAD = 4

    def __init__(self, *, safety_factor: float = 1.05) -> None:
        # Biased high on purpose: an underestimate causes a provider-side context overflow,
        # which is a user-visible failure, while an overestimate only wastes a little room.
        self._safety = safety_factor

    @property
    def name(self) -> str:
        return "heuristic"

    @property
    def exact(self) -> bool:
        return False

    def count(self, text: str) -> int:
        if not text:
            return 0

        cjk = len(self._CJK.findall(text))
        tokens = float(cjk)

        for word in self._WORD.findall(text):
            length = len(word)
            if length <= 4:
                tokens += 1.0
            elif length <= 8:
                tokens += 1.6
            else:
                # Long words break into roughly one piece per 3.5 characters.
                tokens += 1.0 + (length - 4) / 3.5

        for run in self._ARABIC.findall(text):
            tokens += max(1.0, len(run) / self.ARABIC_CHARS_PER_TOKEN)

        for run in self._DIGIT_RUN.findall(text):
            # Digits tokenise much more finely than letters: ~1 token per 1-3 digits.
            tokens += max(1.0, len(run) / 2.0)

        tokens += len(self._PUNCT.findall(text)) * 0.85

        # Newlines are their own tokens in most vocabularies.
        tokens += text.count("\n") * 0.5

        return max(1, int(tokens * self._safety + 0.5))

    def count_messages(self, messages: Sequence[Any]) -> int:
        total = 0
        for message in messages:
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content", "")
            total += self.count(str(content or "")) + self.MESSAGE_OVERHEAD
        return total + 3  # priming tokens for the assistant's reply


class TiktokenTokenizer:
    """Exact counts via ``tiktoken`` when it is installed.

    Falls back to the heuristic on any failure, including an unknown model name -- a tokenizer
    that raises would take down every request over a detail that only affects budget precision.
    """

    def __init__(self, *, encoding: str = "cl100k_base", model: str | None = None) -> None:
        self._fallback = HeuristicTokenizer()
        self._encoder: Any = None
        self._label = encoding
        try:
            import tiktoken

            if model:
                try:
                    self._encoder = tiktoken.encoding_for_model(model)
                    self._label = f"model:{model}"
                except Exception:
                    self._encoder = tiktoken.get_encoding(encoding)
            else:
                self._encoder = tiktoken.get_encoding(encoding)
        except Exception:
            self._encoder = None

    @property
    def name(self) -> str:
        return f"tiktoken:{self._label}" if self._encoder else "heuristic"

    @property
    def exact(self) -> bool:
        return self._encoder is not None

    def count(self, text: str) -> int:
        if self._encoder is None:
            return self._fallback.count(text)
        try:
            return len(self._encoder.encode(text, disallowed_special=()))
        except Exception:  # pragma: no cover - defensive
            return self._fallback.count(text)

    def count_messages(self, messages: Sequence[Any]) -> int:
        if self._encoder is None:
            return self._fallback.count_messages(messages)
        total = 0
        for message in messages:
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content", "")
            total += self.count(str(content or "")) + HeuristicTokenizer.MESSAGE_OVERHEAD
        return total + 3


_cached: dict[str, Tokenizer] = {}


def get_tokenizer(*, model: str | None = None, prefer_exact: bool = True) -> Tokenizer:
    """Return a tokenizer, cached per model.

    Cached because constructing a tiktoken encoding reads and parses a vocabulary file; doing
    that per request would be a real cost for a value that never changes.
    """
    key = f"{model or 'default'}:{prefer_exact}"
    cached = _cached.get(key)
    if cached is not None:
        return cached

    tokenizer: Tokenizer = (
        TiktokenTokenizer(model=model) if prefer_exact else HeuristicTokenizer()
    )
    if prefer_exact and not tokenizer.exact:
        tokenizer = HeuristicTokenizer()
    _cached[key] = tokenizer
    return tokenizer
