"""NlpEngine — Phase I preprocessing for chat and live voice transcripts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .intent import detect_shallow_intent
from .normalize import normalize_transcript

__all__ = ["NlpResult", "NlpEngine", "normalize_for_turn"]


@dataclass(frozen=True, slots=True)
class NlpResult:
    """Normalized text plus optional shallow metadata.

    ``entities`` are display/debug hints only — Phase I does **not** write them to the graph.
    """

    text: str
    original: str
    intent: Mapping[str, object] = field(default_factory=dict)
    entities: tuple[Mapping[str, Any], ...] = ()


class NlpEngine:
    """Stdlib-first NLP. Safe to construct always; no binary or model gate."""

    name = "nlp-v1"

    def __init__(self, *, extract_entities: bool = True, max_entities: int = 32) -> None:
        self._extract_entities = extract_entities
        self._max_entities = max_entities

    async def process(self, text: str, *, source: str = "chat") -> NlpResult:
        """Normalize ``text`` and attach shallow intent / optional entity metadata."""
        original = text or ""
        normalized = normalize_transcript(original)
        intent = detect_shallow_intent(normalized)
        entities: tuple[Mapping[str, Any], ...] = ()
        if self._extract_entities and normalized:
            entities = await self._entity_pass(normalized, source=source)
        return NlpResult(
            text=normalized,
            original=original,
            intent=intent,
            entities=entities,
        )

    async def _entity_pass(self, text: str, *, source: str) -> tuple[Mapping[str, Any], ...]:
        # Lazy import keeps module import cheap and avoids pulling knowledge at import time
        # for callers that only need normalize_for_turn.
        from ..knowledge.extraction import RuleEntityExtractor

        delta = await RuleEntityExtractor(max_entities=self._max_entities).extract(
            text, source=source or "nlp"
        )
        out: list[Mapping[str, Any]] = []
        for entity in delta.entities:
            out.append({"name": entity.name, "type": entity.type})
            if len(out) >= self._max_entities:
                break
        return tuple(out)


async def normalize_for_turn(text: str, *, engine: NlpEngine | None = None) -> str:
    """Extension point for chat and Phase H live voice.

    Returns only the normalized string suitable for ``TurnRequest.message``. Callers that need
    intent/entity metadata should use :meth:`NlpEngine.process` instead.
    """
    if engine is None:
        return normalize_transcript(text or "")
    result = await engine.process(text or "", source="turn")
    return result.text
