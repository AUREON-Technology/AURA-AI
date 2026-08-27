"""Incremental sentence splitter over a token stream (Phase H)."""

from __future__ import annotations

__all__ = ["SentenceAccumulator"]

_TERMINATORS = frozenset(".!?\n؟۔")
_ABBREVIATIONS = frozenset(
    {
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "prof.",
        "sr.",
        "jr.",
        "vs.",
        "etc.",
        "e.g.",
        "i.e.",
        "u.s.",
        "u.k.",
    }
)


class SentenceAccumulator:
    """Feeds tokens; yields complete sentences. Flushes remainder at stream end."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, token: str) -> list[str]:
        if not token:
            return []
        self._buf += token
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[str]:
        out: list[str] = []
        while True:
            idx = self._next_boundary(self._buf)
            if idx < 0:
                break
            sentence = self._buf[: idx + 1].strip()
            self._buf = self._buf[idx + 1 :]
            if sentence:
                out.append(sentence)
        if final:
            tail = self._buf.strip()
            self._buf = ""
            if tail:
                out.append(tail)
        return out

    def _next_boundary(self, text: str) -> int:
        for i, ch in enumerate(text):
            if ch not in _TERMINATORS:
                continue
            # Hold back common English abbreviations ending in '.'
            if ch == ".":
                # Look back for the token before the period.
                start = i
                while start > 0 and not text[start - 1].isspace():
                    start -= 1
                token = text[start : i + 1].lower()
                if token in _ABBREVIATIONS:
                    continue
                # Decimal-ish: digit before and after
                if i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
                    continue
            return i
        return -1
