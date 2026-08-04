"""Entity and relation extraction for the knowledge graph.

Two extractors behind one interface:

:class:`RuleEntityExtractor` -- pattern-based, zero dependency, zero cost, deterministic. Catches
the entity types that have reliable surface forms (dates, money, emails, URLs, capitalised
multi-word names) and the relation patterns that are genuinely unambiguous in English. Runs on
every ingestion.

:class:`LLMEntityExtractor` -- model-driven, higher recall, real cost. Used selectively: on
high-trust sources, or when the rule extractor finds little in a document that clearly contains
structure.

The rule extractor is the default because graph extraction runs on **every chunk of every
document in the nightly refresh**. An LLM call per chunk would dominate the platform's entire
operating cost, and for the entity types above it would not be more accurate.

Both are conservative by design. A knowledge graph full of low-confidence guesses is worse than a
small accurate one: the graph is used to answer questions, and a wrong edge produces a wrong
answer stated with the confidence of structured data.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence

from ..domain.knowledge import Entity, GraphDelta, Relation
from ..observability.logging import get_logger

__all__ = ["RuleEntityExtractor", "LLMEntityExtractor", "CompositeExtractor"]

logger = get_logger("knowledge.extraction")


# Sequences of capitalised words, allowing internal lowercase connectors ("Bank of England").
_PROPER_NOUN = re.compile(
    r"\b([A-Z][a-zA-Z'’\-]+(?:\s+(?:of|the|and|for|de|van|von|al)\s+[A-Z][a-zA-Z'’\-]+|"
    r"\s+[A-Z][a-zA-Z'’\-]+){0,4})\b"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_URL = re.compile(r"https?://[^\s<>\"')]+")
_DATE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}|(?:January|February|March|April|May|June|"
    r"July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b"
)
_MONEY = re.compile(r"[$£€]\s?\d[\d,]*(?:\.\d{2})?(?:\s?(?:million|billion|k|m|bn))?\b", re.I)

_ORG_SUFFIXES = (
    "Inc", "Ltd", "LLC", "GmbH", "Corp", "Corporation", "Company", "Foundation", "Institute",
    "University", "College", "Bank", "Group", "Holdings", "Partners", "Association", "Agency",
    "Ministry", "Department", "Committee",
)
_PLACE_HINTS = (
    "City", "Street", "Avenue", "Road", "District", "Province", "County", "Region", "Airport",
    "Office", "Building",
)

# Words that begin sentences and are therefore capitalised without being proper nouns. Without
# this filter, "The", "This", and every sentence-initial verb become entities, and the graph
# fills with noise that makes the real entities unfindable.
_SENTENCE_STARTERS = frozenset(
    """The A An This That These Those It He She They We You I There Here When Where While If
    However Although Because Since After Before During Their Our Your His Her Its Some Any Each
    Every Most Many Few Both Either Neither Not No Yes But And Or So For Nor Yet Then Than As At
    By In On To Of With From Into Over Under Above Below About Between Among Through""".split()
)

_RELATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?P<s>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)\s+(?:is|was)\s+(?:the\s+)?"
                r"(?:CEO|CTO|CFO|COO|director|manager|head|lead|founder|president|chair)\s+of\s+"
                r"(?P<o>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"), "leads"),
    (re.compile(r"(?P<s>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)\s+works?\s+(?:at|for)\s+"
                r"(?P<o>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"), "works_at"),
    (re.compile(r"(?P<s>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)\s+(?:is|are)\s+(?:located|based)\s+in\s+"
                r"(?P<o>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"), "located_in"),
    (re.compile(r"(?P<s>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)\s+(?:acquired|bought|purchased)\s+"
                r"(?P<o>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"), "acquired"),
    (re.compile(r"(?P<s>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)\s+(?:owns|manages|operates)\s+"
                r"(?P<o>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"), "manages"),
    (re.compile(r"(?P<s>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)\s+reports?\s+to\s+"
                r"(?P<o>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"), "reports_to"),
    (re.compile(r"(?P<s>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)\s+depends?\s+on\s+"
                r"(?P<o>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"), "depends_on"),
)


def _classify(name: str) -> str:
    words = name.split()
    if any(word.rstrip(".,") in _ORG_SUFFIXES for word in words):
        return "organisation"
    if any(word.rstrip(".,") in _PLACE_HINTS for word in words):
        return "place"
    if len(words) == 2 and all(w[:1].isupper() for w in words):
        # Two capitalised words with no organisational or place marker is most often a personal
        # name. Marked as a heuristic in the relation confidence rather than asserted.
        return "person"
    return "concept"


class RuleEntityExtractor:
    """Pattern-based extraction. Deterministic, free, and conservative."""

    name = "rules-v1"

    def __init__(self, *, min_name_length: int = 3, max_entities: int = 200) -> None:
        self._min_length = min_name_length
        self._max_entities = max_entities

    async def extract(self, text: str, *, source: str = "") -> GraphDelta:
        entities: dict[str, Entity] = {}
        relations: list[Relation] = []

        def add_entity(name: str, entity_type: str, confidence_note: str = "") -> Entity | None:
            cleaned = name.strip(" .,:;()[]\"'")
            if len(cleaned) < self._min_length or len(entities) >= self._max_entities:
                return None
            if cleaned.split()[0] in _SENTENCE_STARTERS and len(cleaned.split()) == 1:
                return None
            key = Entity.canonical_key(cleaned, entity_type)
            existing = entities.get(key)
            if existing is not None:
                entities[key] = Entity(
                    key=key, name=existing.name, type=entity_type,
                    mention_count=existing.mention_count + 1,
                    sources=(source,) if source else (),
                    attributes=existing.attributes,
                )
                return entities[key]
            entity = Entity(
                key=key, name=cleaned, type=entity_type,
                sources=(source,) if source else (),
                attributes={"extractor": self.name, **({"note": confidence_note} if confidence_note else {})},
            )
            entities[key] = entity
            return entity

        for match in _EMAIL.finditer(text):
            add_entity(match.group(0), "email")
        for match in _URL.finditer(text):
            add_entity(match.group(0)[:200], "url")
        for match in _DATE.finditer(text):
            add_entity(match.group(0), "date")
        for match in _MONEY.finditer(text):
            add_entity(match.group(0), "money")

        for match in _PROPER_NOUN.finditer(text):
            candidate = match.group(1)
            first = candidate.split()[0]
            # Skip a single sentence-initial capitalised word; keep multi-word phrases even if
            # they start a sentence, since "Cairo Regional Office is..." is a real entity.
            if first in _SENTENCE_STARTERS and len(candidate.split()) == 1:
                continue
            add_entity(candidate, _classify(candidate))

        for pattern, predicate in _RELATION_PATTERNS:
            for match in pattern.finditer(text):
                subject_name = match.group("s").strip()
                object_name = match.group("o").strip()
                if subject_name == object_name:
                    continue
                subject = add_entity(subject_name, _classify(subject_name))
                obj = add_entity(object_name, _classify(object_name))
                if subject is None or obj is None:
                    continue
                relations.append(
                    Relation(
                        subject_key=subject.key,
                        predicate=predicate,
                        object_key=obj.key,
                        # Deliberately below 1.0: a surface-pattern match is evidence, not proof.
                        # Downstream consumers can require a higher bar for consequential answers.
                        confidence=0.7,
                        sources=(source,) if source else (),
                        attributes={"extractor": self.name},
                    )
                )

        return GraphDelta(
            entities=tuple(entities.values()), relations=tuple(relations), source=source
        )


class LLMEntityExtractor:
    """Model-driven extraction, for documents where recall matters more than cost.

    Uses the ``task.entity_extraction`` prompt with a deterministic sampling setting and a JSON
    response, then validates the result. A malformed or hallucinated response yields an empty
    delta rather than an exception -- extraction is an enhancement to ingestion, and failing it
    must not fail the ingestion the user asked for.
    """

    name = "llm-v1"

    def __init__(
        self,
        *,
        router: Any,
        prompts: Any,
        max_chars: int = 6000,
        min_confidence: float = 0.6,
        policy: Any = None,
    ) -> None:
        self._router = router
        self._prompts = prompts
        self._max_chars = max_chars
        self._min_confidence = min_confidence
        self._policy = policy

    async def extract(self, text: str, *, source: str = "") -> GraphDelta:
        from ..domain.llm import CompletionRequest, SamplingParams
        from ..domain.messages import Conversation, Message

        # Truncate rather than chunk: extraction over a document's opening is representative
        # enough for graph purposes, and per-chunk LLM extraction is the cost blow-up this class
        # is explicitly scoped to avoid.
        excerpt = text[: self._max_chars]
        rendered = self._prompts.render("task.entity_extraction", {"text": excerpt})

        try:
            response = await self._router.complete(
                CompletionRequest(
                    conversation=Conversation.of([Message.user(rendered.text)]),
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=1024),
                    policy=self._policy,
                )
            )
            payload = _parse_json_object(response.text)
        except Exception as exc:
            logger.warning(
                "LLM entity extraction failed", extra={"error": type(exc).__name__, "source": source}
            )
            return GraphDelta(source=source)

        entities: dict[str, Entity] = {}
        for raw in payload.get("entities", []) or []:
            name = str(raw.get("name", "")).strip()
            entity_type = str(raw.get("type", "concept")).strip() or "concept"
            if not name:
                continue
            key = Entity.canonical_key(name, entity_type)
            entities[key] = Entity(
                key=key,
                name=name,
                type=entity_type,
                attributes={**(raw.get("attributes") or {}), "extractor": self.name},
                sources=(source,) if source else (),
            )

        relations = []
        for raw in payload.get("relations", []) or []:
            confidence = float(raw.get("confidence", 0.5) or 0.0)
            if confidence < self._min_confidence:
                continue
            subject_name = str(raw.get("subject", "")).strip()
            object_name = str(raw.get("object", "")).strip()
            predicate = str(raw.get("predicate", "")).strip()
            if not (subject_name and object_name and predicate):
                continue
            subject = _find_entity(entities, subject_name)
            obj = _find_entity(entities, object_name)
            if subject is None or obj is None:
                # A relation naming an entity the model did not also extract is unreliable --
                # commonly a hallucinated participant. Dropped rather than inventing a node.
                continue
            relations.append(
                Relation(
                    subject_key=subject.key,
                    predicate=predicate.replace(" ", "_").lower(),
                    object_key=obj.key,
                    confidence=min(confidence, 0.95),
                    sources=(source,) if source else (),
                    attributes={"extractor": self.name},
                )
            )

        return GraphDelta(
            entities=tuple(entities.values()), relations=tuple(relations), source=source
        )


class CompositeExtractor:
    """Run several extractors and merge their deltas.

    Rules first, then optionally the LLM: entities found by both are merged, and rule-derived
    entities anchor the LLM's relations, which reduces the hallucinated-participant problem.
    """

    name = "composite"

    def __init__(self, extractors: Sequence[Any]) -> None:
        self._extractors = list(extractors)

    async def extract(self, text: str, *, source: str = "") -> GraphDelta:
        delta = GraphDelta(source=source)
        for extractor in self._extractors:
            try:
                delta = delta.merge(await extractor.extract(text, source=source))
            except Exception:
                logger.warning("extractor failed", extra={"extractor": getattr(extractor, "name", "?")})
        return delta


def _find_entity(entities: dict[str, Entity], name: str) -> Entity | None:
    needle = name.lower()
    for entity in entities.values():
        if entity.name.lower() == needle or needle in (a.lower() for a in entity.aliases):
            return entity
    return None


def _parse_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from a model response.

    Models wrap JSON in prose or fences often enough that requiring a bare object would make
    structured extraction unreliable on smaller models. Falls back to the first balanced
    ``{...}`` span.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.S).strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    if start < 0:
        return {}
    depth = 0
    for index in range(start, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(stripped[start : index + 1])
                    return parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}


def iter_entity_names(delta: GraphDelta) -> Iterable[str]:
    return (entity.name for entity in delta.entities)
