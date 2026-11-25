"""Token counting behind the ``Tokenizer`` port, selected per provider (KAN-103).

Every budgeting decision needs a number: the context engine's prompt budget, the router's
context-window check, the quota reservation, a provider's usage fallback. This module provides
one -- exactly when a real tokenizer for the routed model is available, and by calibrated
estimate (:mod:`aura_core.domain.tokens`) when it is not -- and says which (:attr:`exact`).

Selection is by a **tokenizer spec**, declared per provider (``ProviderCapabilities.tokenizer``,
``AURA_PROVIDER_<NAME>_TOKENIZER``)::

    ""                         infer the family from the model id (qwen -> qwen2, gpt-oss ->
                               o200k, claude -> claude), else generic
    "qwen2"                    a family's calibrated heuristic profile
    "qwen2@/models/tok.json"   that family, counted exactly by a Hugging Face tokenizer.json
                               (the ``tokenizers`` package, already in requirements.lock);
                               falls back to the family's heuristic if the file cannot be loaded
    "o200k" / "cl100k"         exact via tiktoken when it is installed (it is not, today),
                               otherwise the family's heuristic

A ``cl100k_base`` count used to be the only "exact" option, and it is the wrong vocabulary for
every model this deployment runs (Qwen, gpt-oss, Claude); it is no longer anyone's default.

The estimate is biased **high** on purpose: an underestimate causes a context overflow or a
quota overrun the user sees as a failed turn, while an overestimate only wastes a little room.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, Sequence, runtime_checkable

from ..domain.tokens import (
    GENERIC,
    MESSAGE_OVERHEAD,
    PROFILES,
    REPLY_PRIMING,
    TokenProfile,
    estimate_tokens,
    infer_family,
    profile_named,
)

__all__ = [
    "Tokenizer",
    "HeuristicTokenizer",
    "HFTokenizer",
    "TiktokenTokenizer",
    "get_tokenizer",
    "tokenizer_for",
    "parse_tokenizer_spec",
]


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


def _content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content", "")
    return str(content or "")


def _count_messages(counter: Any, messages: Sequence[Any]) -> int:
    """Content plus per-message framing plus reply priming -- the same arithmetic as
    ``Conversation.approx_tokens``, so the router and the context engine agree exactly."""
    if not messages:
        return 0
    return sum(counter(_content(m)) + MESSAGE_OVERHEAD for m in messages) + REPLY_PRIMING


class HeuristicTokenizer:
    """Dependency-free estimator over one :class:`~aura_core.domain.tokens.TokenProfile`.

    The default (generic) profile counts exactly what this class counted before per-provider
    profiles existed, plus letters of scripts it used to ignore (Cyrillic, Hebrew, ...),
    underscores and indentation runs -- each of which made code and non-Latin text undercount.
    """

    #: Kept as class attributes: callers and tests read them.
    ARABIC_CHARS_PER_TOKEN = GENERIC.arabic_chars_per_token
    MESSAGE_OVERHEAD = MESSAGE_OVERHEAD

    def __init__(
        self, *, safety_factor: float | None = None, profile: TokenProfile | str | None = None
    ) -> None:
        resolved = profile if isinstance(profile, TokenProfile) else profile_named(profile)
        if safety_factor is not None:
            from dataclasses import replace

            resolved = replace(resolved, safety=safety_factor)
        self._profile = resolved

    @property
    def name(self) -> str:
        return "heuristic" if self._profile.name == "generic" else f"heuristic:{self._profile.name}"

    @property
    def exact(self) -> bool:
        return False

    @property
    def profile(self) -> TokenProfile:
        return self._profile

    def count(self, text: str) -> int:
        return estimate_tokens(text, self._profile)

    def count_messages(self, messages: Sequence[Any]) -> int:
        return _count_messages(self.count, messages)


class HFTokenizer:
    """Exact content counts from a Hugging Face ``tokenizer.json`` (e.g. the Qwen Space's).

    ``tokenizers`` is imported lazily (it is a vendor module, ADR-001) and the file is read once.
    Any failure -- package missing, file absent, unparsable -- leaves this on its fallback
    heuristic with ``exact`` false: a tokenizer that raised would take down every request over
    a detail that only affects budget precision. Message framing is still the documented
    estimate (chat templates differ by a token or two); content counts are exact.
    """

    def __init__(self, path: str, *, fallback: HeuristicTokenizer, family: str) -> None:
        self._fallback = fallback
        self._family = family
        self._encoder: Any = None
        try:
            from tokenizers import Tokenizer as _HFTokenizer

            self._encoder = _HFTokenizer.from_file(path)
        except Exception:
            self._encoder = None

    @property
    def name(self) -> str:
        return f"hf:{self._family}" if self._encoder is not None else self._fallback.name

    @property
    def exact(self) -> bool:
        return self._encoder is not None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._encoder is None:
            return self._fallback.count(text)
        try:
            return len(self._encoder.encode(text, add_special_tokens=False).ids)
        except Exception:  # pragma: no cover - defensive
            return self._fallback.count(text)

    def count_messages(self, messages: Sequence[Any]) -> int:
        return _count_messages(self.count, messages)


class TiktokenTokenizer:
    """Exact counts via ``tiktoken`` when it is installed (it is not in requirements.lock).

    Falls back to ``fallback`` (the generic heuristic by default) on any failure, including an
    unknown model name.
    """

    def __init__(
        self,
        *,
        encoding: str = "cl100k_base",
        model: str | None = None,
        fallback: HeuristicTokenizer | None = None,
    ) -> None:
        self._fallback = fallback or HeuristicTokenizer()
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
        return f"tiktoken:{self._label}" if self._encoder else self._fallback.name

    @property
    def exact(self) -> bool:
        return self._encoder is not None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._encoder is None:
            return self._fallback.count(text)
        try:
            return len(self._encoder.encode(text, disallowed_special=()))
        except Exception:  # pragma: no cover - defensive
            return self._fallback.count(text)

    def count_messages(self, messages: Sequence[Any]) -> int:
        return _count_messages(self.count, messages)


#: Families whose exact vocabulary tiktoken ships. gpt-oss's ``o200k_harmony`` is
#: ``o200k_base`` plus chat special tokens, so plain-text counts are the same.
_TIKTOKEN_ENCODINGS = {"o200k": "o200k_base", "cl100k": "cl100k_base"}


def parse_tokenizer_spec(spec: str | None, model_id: str | None = None) -> tuple[str, str | None]:
    """``(family, tokenizer.json path or None)`` for a spec; see the module docstring."""
    raw = (spec or "").strip()
    family, _, path = raw.partition("@")
    family = family.strip().lower()
    if not family or family == "auto" or family not in PROFILES:
        family = infer_family(model_id) if not family or family == "auto" else "generic"
    return family, (path.strip() or None)


_cached: dict[tuple[str, str], Tokenizer] = {}
_cache_lock = threading.Lock()


def tokenizer_for(spec: str | None = "", *, model_id: str | None = None) -> Tokenizer:
    """The tokenizer a provider's ``spec`` (and model id) selects. Cached: loading a vocabulary
    file per request would be a real cost for a value that never changes."""
    family, path = parse_tokenizer_spec(spec, model_id)
    key = (family, path or "")
    cached = _cached.get(key)
    if cached is not None:
        return cached
    fallback = HeuristicTokenizer(profile=family)
    tokenizer: Tokenizer = fallback
    if path:
        tokenizer = HFTokenizer(path, fallback=fallback, family=family)
    elif family in _TIKTOKEN_ENCODINGS:
        candidate = TiktokenTokenizer(encoding=_TIKTOKEN_ENCODINGS[family], fallback=fallback)
        if candidate.exact:
            tokenizer = candidate
    with _cache_lock:
        _cached.setdefault(key, tokenizer)
        return _cached[key]


def get_tokenizer(*, model: str | None = None, prefer_exact: bool = True) -> Tokenizer:
    """The tokenizer for ``model``, by the family its id names (see :func:`tokenizer_for`).

    ``prefer_exact=False`` returns the generic heuristic regardless.
    """
    if not prefer_exact:
        return tokenizer_for("generic")
    return tokenizer_for("", model_id=model)
