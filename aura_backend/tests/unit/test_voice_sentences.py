"""Sentence accumulator tests (Phase H)."""

from __future__ import annotations

from aura_core.voice.sentences import SentenceAccumulator


class TestSentenceAccumulator:
    def test_split_across_tokens(self) -> None:
        acc = SentenceAccumulator()
        assert acc.feed("Hello ") == []
        assert acc.feed("world.") == ["Hello world."]
        assert acc.feed(" Next") == []
        assert acc.flush() == ["Next"]

    def test_arabic_question_mark(self) -> None:
        acc = SentenceAccumulator()
        assert acc.feed("مرحبا؟") == ["مرحبا؟"]

    def test_abbreviation_held(self) -> None:
        acc = SentenceAccumulator()
        assert acc.feed("Dr. Smith arrived.") == ["Dr. Smith arrived."]

    def test_end_flush(self) -> None:
        acc = SentenceAccumulator()
        acc.feed("incomplete")
        assert acc.flush() == ["incomplete"]
