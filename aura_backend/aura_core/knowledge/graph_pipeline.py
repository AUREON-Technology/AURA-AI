"""The knowledge-graph ingestion pipeline: nothing enters long-term knowledge unchecked.

An extractor (rule-based by default, optionally a model) produces a *candidate* delta. Before any
of it is stored it goes through, in order:

1. **Normalise** -- NFKC, straight quotes, collapsed whitespace, edge punctuation stripped; the type
   label mapped through the schema's synonyms; a canonical key that drops corporate suffixes and
   honorifics, with every surface form kept as an alias.
2. **Validate** -- closed-world against :data:`~aura_core.domain.graph.DEFAULT_GRAPH_SCHEMA`: the
   entity type and predicate must be declared, the relation's endpoint types must be allowed for
   that predicate, literal types (dates, amounts) are never subjects, names are bounded and free
   of control characters, and every name must actually occur in the source text. That last rule
   is what stops a model inventing a participant.
3. **Attach provenance** -- the pipeline sets the source (``{document_id}@v{version}``) itself;
   whatever an extractor put in ``sources`` is discarded, so a model cannot choose where a fact
   claims to come from. Extractor-supplied attributes are dropped too, except the extractor name.
4. **Calibrate confidence** -- from an explicit per-extractor prior (below), penalised when an
   endpoint is untyped; relations below ``min_relation_confidence`` are rejected.
5. **Deduplicate / merge** -- by canonical key within the delta; the store then merges with what
   is already known and recomputes confidence from all live provenance (noisy-OR).
6. **Insert** -- one ``GraphStore.apply`` call, transactional in the Postgres adapter.

Lifecycle is the other half: :meth:`retire_superseded` expires facts only an older version of a
document asserted, and :meth:`purge` removes every fact that only a deleted document asserted.

The model extractor's output is treated as DATA throughout -- parsed as JSON, never executed or
followed, and passed through exactly the validation above. What ends up in the graph can later
reach a prompt only through retrieval, which fences it as DATA again (ADR-006).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping

from ..domain.graph import (
    DEFAULT_GRAPH_SCHEMA,
    GraphSchema,
    has_control_characters,
    normalize_alias,
    normalize_surface,
    source_id,
)
from ..domain.identifiers import UserId
from ..domain.knowledge import Entity, GraphDelta, Relation
from ..observability.logging import get_logger

__all__ = ["GraphIngestReport", "KnowledgeGraphPipeline", "EXTRACTOR_PRIORS"]

logger = get_logger("knowledge.graph_pipeline")

#: Confidence priors per extractor. These are stated assumptions, not learned calibration: a rule
#: match on an unambiguous pattern is fairly reliable; a small local model's self-reported
#: confidence is not, so it is blended toward a lower prior and capped.
EXTRACTOR_PRIORS: Mapping[str, float] = {"rules-v1": 0.7, "llm-v1": 0.6}
_DEFAULT_PRIOR = 0.5
_LLM_CAP = 0.85
#: Literal types have unambiguous surface forms (an email address is an email address).
_LITERAL_ENTITY_CONFIDENCE = 0.95
_TYPED_ENTITY_CONFIDENCE = 0.7
_CONCEPT_ENTITY_CONFIDENCE = 0.5
_UNTYPED_ENDPOINT_PENALTY = 0.9


@dataclass(frozen=True, slots=True)
class GraphIngestReport:
    source: str
    entities_extracted: int = 0
    relations_extracted: int = 0
    entities_accepted: int = 0
    relations_accepted: int = 0
    rejected: tuple[tuple[str, str], ...] = ()
    expired: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "entities_extracted": self.entities_extracted,
            "relations_extracted": self.relations_extracted,
            "entities_accepted": self.entities_accepted,
            "relations_accepted": self.relations_accepted,
            "rejected": [list(r) for r in self.rejected],
            "expired": self.expired,
        }


@dataclass(slots=True)
class _Validated:
    entities: dict[str, Entity] = field(default_factory=dict)
    relations: dict[tuple[str, str, str], Relation] = field(default_factory=dict)
    rejected: list[tuple[str, str]] = field(default_factory=list)


class KnowledgeGraphPipeline:
    def __init__(
        self,
        *,
        graph: Any,
        extractor: Any,
        schema: GraphSchema = DEFAULT_GRAPH_SCHEMA,
        min_relation_confidence: float = 0.5,
        min_entity_confidence: float = 0.4,
        max_name_length: int = 200,
    ) -> None:
        self._graph = graph
        self._extractor = extractor
        self._schema = schema
        self._min_relation = min_relation_confidence
        self._min_entity = min_entity_confidence
        self._max_name = max_name_length

    @property
    def schema(self) -> GraphSchema:
        return self._schema

    # -- ingestion ---------------------------------------------------------------------

    async def ingest(
        self, *, document_id: str, version: int, text: str, owner_id: UserId | None
    ) -> GraphIngestReport:
        source = source_id(document_id, version)
        candidate = await self._extractor.extract(text, source=source)
        validated = self.process(candidate, text=text, source=source)
        delta = GraphDelta(
            entities=tuple(validated.entities.values()),
            relations=tuple(validated.relations.values()),
            source=source,
        )
        if delta:
            await self._graph.apply(delta, owner_id=owner_id)
        report = GraphIngestReport(
            source=source,
            entities_extracted=len(candidate.entities),
            relations_extracted=len(candidate.relations),
            entities_accepted=len(delta.entities),
            relations_accepted=len(delta.relations),
            rejected=tuple(validated.rejected),
        )
        if validated.rejected:
            logger.info(
                "graph candidates rejected",
                extra={"source": source, "rejected": len(validated.rejected),
                       "reasons": sorted({reason for _item, reason in validated.rejected})},
            )
        return report

    async def retire_superseded(
        self, *, document_id: str, current_version: int, owner_id: UserId | None,
        at: datetime | None = None,
    ) -> int:
        return await self._graph.retire_document(
            document_id, owner_id=owner_id, keep_version=current_version, at=at
        )

    async def purge(self, *, document_id: str, owner_id: UserId | None) -> int:
        return await self._graph.purge_document(document_id, owner_id=owner_id)

    # -- the pure part: normalise, validate, provenance, calibrate, dedupe --------------

    def process(self, candidate: GraphDelta, *, text: str, source: str) -> _Validated:
        out = _Validated()
        haystack = normalize_alias(text)
        # Extractor keys are not trusted as identity: every entity is re-keyed from its
        # normalised name and type, and relations are re-pointed through this map.
        rekey: dict[str, Entity] = {}

        for raw in candidate.entities:
            entity, reason = self._entity(raw, haystack, source)
            if entity is None:
                out.rejected.append((f"entity:{raw.name[:60]}", reason))
                continue
            rekey[raw.key] = entity
            existing = out.entities.get(entity.key)
            if existing is not None:
                merged = existing.merged_with(entity)
                entity = replace(merged, mention_count=existing.mention_count + 1,
                                 sources=(source,))
            out.entities[entity.key] = entity
            rekey[raw.key] = entity

        for raw in candidate.relations:
            relation, reason = self._relation(raw, rekey, out.entities, source)
            label = f"relation:{raw.subject_key}-{raw.predicate}->{raw.object_key}"[:120]
            if relation is None:
                out.rejected.append((label, reason))
                continue
            existing_rel = out.relations.get(relation.key)
            if existing_rel is None or relation.confidence > existing_rel.confidence:
                out.relations[relation.key] = relation
        return out

    def _entity(self, raw: Entity, haystack: str, source: str) -> tuple[Entity | None, str]:
        if has_control_characters(raw.name):
            return None, "control_characters"
        name = normalize_surface(raw.name)
        if not name:
            return None, "empty_name"
        if len(name) > self._max_name:
            return None, "name_too_long"
        entity_type = self._schema.canonical_entity_type(raw.type)
        if entity_type is None:
            return None, "unknown_entity_type"
        if normalize_alias(name) not in haystack:
            # The grounding rule: a name the source text does not contain is a hallucination
            # (or an injection), whichever extractor produced it.
            return None, "not_in_source"
        extractor = _extractor_of(raw)
        confidence = self._entity_confidence(entity_type, extractor)
        if confidence < self._min_entity:
            return None, "low_confidence"
        key_name = self._schema.key_name(name, entity_type)
        aliases = tuple(
            dict.fromkeys(
                a for a in (
                    *(normalize_surface(x) for x in raw.aliases if not has_control_characters(x)),
                    key_name,
                )
                if a and a != name and len(a) <= self._max_name
            )
        )
        return (
            Entity(
                key=self._schema.entity_key(name, entity_type),
                name=name,
                type=entity_type,
                aliases=aliases,
                attributes={"extractor": extractor},
                mention_count=max(1, raw.mention_count),
                sources=(source,),
                confidence=confidence,
            ),
            "",
        )

    def _relation(
        self,
        raw: Relation,
        rekey: Mapping[str, Entity],
        accepted: Mapping[str, Entity],
        source: str,
    ) -> tuple[Relation | None, str]:
        subject = rekey.get(raw.subject_key)
        obj = rekey.get(raw.object_key)
        if subject is None or obj is None:
            return None, "unknown_endpoint"
        subject = accepted.get(subject.key, subject)
        obj = accepted.get(obj.key, obj)
        if subject.key == obj.key:
            return None, "self_loop"
        predicate = self._schema.canonical_predicate(raw.predicate)
        if predicate is None:
            return None, "unknown_relation_type"
        subject_spec = self._schema.entity_type(subject.type)
        if subject_spec is not None and subject_spec.literal:
            return None, "literal_subject"
        if not self._schema.allows(predicate, subject.type, obj.type):
            return None, "type_constraint"
        extractor = _extractor_of(raw)
        confidence = self._relation_confidence(raw.confidence, extractor, subject.type, obj.type)
        if confidence < self._min_relation:
            return None, "low_confidence"
        return (
            Relation(
                subject_key=subject.key,
                predicate=predicate,
                object_key=obj.key,
                confidence=confidence,
                sources=(source,),
                attributes={"extractor": extractor},
            ),
            "",
        )

    # -- calibration -------------------------------------------------------------------

    @staticmethod
    def _entity_confidence(entity_type: str, extractor: str) -> float:
        if entity_type in ("date", "money", "email", "url"):
            base = _LITERAL_ENTITY_CONFIDENCE
        elif entity_type == "concept":
            base = _CONCEPT_ENTITY_CONFIDENCE
        else:
            base = _TYPED_ENTITY_CONFIDENCE
        if extractor.startswith("llm"):
            base = min(base, EXTRACTOR_PRIORS["llm-v1"])
        return base

    @staticmethod
    def _relation_confidence(
        reported: float, extractor: str, subject_type: str, object_type: str
    ) -> float:
        prior = EXTRACTOR_PRIORS.get(extractor, _DEFAULT_PRIOR)
        if extractor.startswith("llm"):
            # Self-reported model confidence is poorly calibrated: blend it toward the prior and
            # cap it, so "confidence": 1.0 from a 3B model cannot outrank corroborated evidence.
            value = min(_LLM_CAP, 0.5 * float(reported) + 0.5 * prior)
        else:
            value = prior
        for endpoint_type in (subject_type, object_type):
            if endpoint_type == "concept":
                value *= _UNTYPED_ENDPOINT_PENALTY
        return round(max(0.0, min(1.0, value)), 4)


def _extractor_of(item: Any) -> str:
    value = item.attributes.get("extractor") if isinstance(item.attributes, Mapping) else None
    return str(value) if value in EXTRACTOR_PRIORS else "unknown"
