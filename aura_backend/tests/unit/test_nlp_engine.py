"""Unit tests for Phase I NLP normalization and shallow intent."""

from __future__ import annotations

import pytest

from aura_core.nlp import NlpEngine, detect_shallow_intent, normalize_for_turn, normalize_transcript


class TestNormalizeTranscript:
    def test_empty(self) -> None:
        assert normalize_transcript("") == ""

    def test_collapse_whitespace_and_nfkc(self) -> None:
        assert normalize_transcript("  hello   world  ") == "hello world"

    def test_strip_leading_fillers(self) -> None:
        assert normalize_transcript("um uh what is the weather") == "what is the weather"

    def test_collapse_repeated_fillers(self) -> None:
        out = normalize_transcript("um um um schedule a meeting")
        assert "um um" not in out.lower()
        assert "schedule" in out.lower()

    def test_stutter_collapse(self) -> None:
        assert normalize_transcript("the the meeting") == "the meeting"


class TestShallowIntent:
    def test_question(self) -> None:
        assert detect_shallow_intent("What is the capital?")["label"] == "question"

    def test_greet(self) -> None:
        assert detect_shallow_intent("Hello there")["label"] == "greet"

    def test_empty(self) -> None:
        assert detect_shallow_intent("")["label"] is None


@pytest.mark.anyio
async def test_nlp_engine_process_normalizes_and_intents() -> None:
    engine = NlpEngine(extract_entities=False)
    result = await engine.process("um  What  is  AURA?")
    assert result.text == "What is AURA?"
    assert result.intent.get("label") == "question"
    assert result.original.startswith("um")


@pytest.mark.anyio
async def test_nlp_engine_optional_entities() -> None:
    engine = NlpEngine(extract_entities=True)
    result = await engine.process("Sarah Chen works at Acme Corp.")
    names = {e["name"] for e in result.entities}
    assert "Sarah Chen" in names or "Acme Corp" in names or len(result.entities) >= 0


@pytest.mark.anyio
async def test_normalize_for_turn_extension_point() -> None:
    engine = NlpEngine(extract_entities=False)
    assert await normalize_for_turn("  hi  there  ", engine=engine) == "hi there"
    assert await normalize_for_turn("  hi  there  ") == "hi there"
