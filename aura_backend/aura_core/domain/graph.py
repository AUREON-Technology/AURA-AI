"""The knowledge graph's vocabulary, provenance, and the pure rules facts are held to.

Stdlib only, like the rest of the domain layer. Three things live here because both adapters and
the ingestion pipeline must agree on them exactly:

1. **The schema** (:data:`DEFAULT_GRAPH_SCHEMA`): which entity types and which relation types may
   enter long-term knowledge, and which entity types each relation may connect. The Alembic
   migration seeds ``aura_graph_entity_types`` / ``aura_graph_relation_types`` with the same
   values (``tests/unit/test_graph_schema_migration.py`` pins them together).

2. **Provenance** (:class:`FactProvenance`, :func:`source_id`, :func:`parse_source`): every fact
   carries one provenance record per source that asserted it -- ``document_id`` and version for
   a document, so deleting or superseding that document can find and remove or expire exactly
   the facts derived from it.

3. **Confidence** (:func:`combine_confidence`): a fact's confidence is recomputed from its *live*
   evidence, so it rises when an independent document corroborates it and falls when a source is
   withdrawn. It is never a sticky maximum that outlives the evidence behind it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping

from .knowledge import Entity

__all__ = [
    "EntityTypeSpec",
    "RelationTypeSpec",
    "GraphSchema",
    "DEFAULT_GRAPH_SCHEMA",
    "FactProvenance",
    "source_id",
    "parse_source",
    "normalize_surface",
    "normalize_alias",
    "combine_confidence",
    "MAX_COMBINED_CONFIDENCE",
]

#: Corroboration never produces certainty. Extracted facts are evidence, not proof, and a value of
#: 1.0 would let downstream consumers treat an extraction as ground truth.
MAX_COMBINED_CONFIDENCE = 0.99

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_EDGE_PUNCT = " \t.,:;()[]{}\"'"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SOURCE = re.compile(r"^(?P<doc>.+)@v(?P<ver>\d+)$")


def normalize_surface(text: str) -> str:
    """The display form of a mention: NFKC, straight quotes, single spaces, no edge punctuation.

    Case is preserved -- "Acme Corp" is the name a person expects to read back.
    """
    cleaned = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return " ".join(cleaned.split()).strip(_EDGE_PUNCT)


def normalize_alias(text: str) -> str:
    """The comparison form: :func:`normalize_surface` plus lower case.

    ``lower()`` rather than ``casefold()`` so it agrees with Postgres ``lower()``, which the
    migration uses to backfill ``aura_graph_entity_aliases.alias_norm`` from legacy rows.
    """
    return normalize_surface(text).lower()


def has_control_characters(text: str) -> bool:
    return bool(_CONTROL.search(text))


def source_id(document_id: str, version: int) -> str:
    """The provenance id of one document version, as the ingestion pipeline writes it."""
    return f"{document_id}@v{version}"


def parse_source(source: str) -> tuple[str | None, int | None]:
    """Split a ``{document_id}@v{version}`` source id. Anything else has no document."""
    match = _SOURCE.match(source or "")
    if match is None:
        return None, None
    return match.group("doc"), int(match.group("ver"))


def combine_confidence(values: Iterable[float]) -> float:
    """Noisy-OR over independent sources, capped below certainty.

    One source keeps its own confidence exactly; each further independent source raises it:
    ``1 - prod(1 - c_i)``. Two documents each asserting a fact at 0.7 give 0.91.
    """
    items = [min(max(float(v), 0.0), 1.0) for v in values]
    if not items:
        return 0.0
    if len(items) == 1:
        return items[0]
    remainder = 1.0
    for value in items:
        remainder *= 1.0 - value
    # Never below the strongest single source (the cap must not *lower* a lone 0.995 assertion
    # once a weak second source appears), never above the cap otherwise.
    return max(max(items), min(MAX_COMBINED_CONFIDENCE, 1.0 - remainder))


@dataclass(frozen=True, slots=True)
class FactProvenance:
    """One source's assertion of one fact.

    ``retired_at`` is set when the source is superseded (a newer version of the same document no
    longer states the fact): the record stays, so the history is inspectable and a later delete of
    the document still finds it, but it no longer counts as live evidence.
    """

    source_id: str
    document_id: str | None = None
    document_version: int | None = None
    extractor: str = ""
    confidence: float = 0.5
    recorded_at: datetime | None = None
    retired_at: datetime | None = None

    @classmethod
    def from_source(
        cls, source: str, *, extractor: str = "", confidence: float = 0.5,
        recorded_at: datetime | None = None,
    ) -> "FactProvenance":
        document_id, version = parse_source(source)
        return cls(
            source_id=source, document_id=document_id, document_version=version,
            extractor=extractor, confidence=confidence, recorded_at=recorded_at,
        )

    @property
    def is_live(self) -> bool:
        return self.retired_at is None


@dataclass(frozen=True, slots=True)
class EntityTypeSpec:
    name: str
    #: Literal values (a date, an amount, an address) rather than things. They can be mentioned,
    #: but never be the subject of a relation.
    literal: bool = False
    description: str = ""
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationTypeSpec:
    predicate: str
    subject_types: frozenset[str]
    object_types: frozenset[str]
    description: str = ""
    synonyms: tuple[str, ...] = ()


# Corporate suffixes and honorifics are dropped from the *key* (not the name), so "Acme Corp",
# "Acme Corporation" and "Acme Inc." resolve to one organisation and "Dr. Sarah Chen" to the same
# person as "Sarah Chen". The surface forms are kept as aliases.
_ORG_SUFFIX = re.compile(
    r"(?:,?\s+(?:inc|incorporated|ltd|limited|llc|plc|gmbh|corp|corporation|co|company))\.?$",
    re.I,
)
_HONORIFIC = re.compile(r"^(?:dr|mr|mrs|ms|mx|prof|sir|dame)\.?\s+", re.I)
_LEADING_THE = re.compile(r"^the\s+", re.I)


@dataclass(frozen=True, slots=True)
class GraphSchema:
    """What may enter long-term knowledge.

    Validation is closed-world: an entity type or predicate not declared here is rejected, not
    stored under a guessed name. That is the property that makes the graph queryable -- a
    relation stored as ``"is_the_boss_of"`` by one extraction and ``"leads"`` by another is two
    facts nobody can join.
    """

    entity_types: tuple[EntityTypeSpec, ...]
    relation_types: tuple[RelationTypeSpec, ...]
    _entity_index: Mapping[str, str] = field(init=False, repr=False, compare=False)
    _relation_index: Mapping[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        entity_index: dict[str, str] = {}
        for spec in self.entity_types:
            for label in (spec.name, *spec.synonyms):
                entity_index[_label(label)] = spec.name
        relation_index: dict[str, str] = {}
        for spec in self.relation_types:
            for label in (spec.predicate, *spec.synonyms):
                relation_index[_label(label)] = spec.predicate
        object.__setattr__(self, "_entity_index", entity_index)
        object.__setattr__(self, "_relation_index", relation_index)

    # -- vocabulary ---------------------------------------------------------------------

    def canonical_entity_type(self, raw: str) -> str | None:
        return self._entity_index.get(_label(raw))

    def canonical_predicate(self, raw: str) -> str | None:
        return self._relation_index.get(_label(raw))

    def entity_type(self, name: str) -> EntityTypeSpec | None:
        return next((s for s in self.entity_types if s.name == name), None)

    def relation_type(self, predicate: str) -> RelationTypeSpec | None:
        return next((s for s in self.relation_types if s.predicate == predicate), None)

    def allows(self, predicate: str, subject_type: str, object_type: str) -> bool:
        spec = self.relation_type(predicate)
        if spec is None:
            return False
        return subject_type in spec.subject_types and object_type in spec.object_types

    # -- identity -----------------------------------------------------------------------

    def entity_key(self, name: str, entity_type: str) -> str:
        """The canonical merge key: type plus a normalised name with noise removed."""
        return Entity.canonical_key(self.key_name(name, entity_type), entity_type)

    @staticmethod
    def key_name(name: str, entity_type: str) -> str:
        surface = normalize_surface(name)
        if entity_type == "organisation":
            surface = _LEADING_THE.sub("", surface)
            stripped = _ORG_SUFFIX.sub("", surface).strip()
            surface = stripped or surface
        elif entity_type == "person":
            surface = _HONORIFIC.sub("", surface).strip() or surface
        elif entity_type in ("email", "url"):
            surface = surface.lower()
        return surface


def _label(raw: str) -> str:
    return re.sub(r"[\s\-]+", "_", (raw or "").strip().lower())


_THINGS = frozenset({"person", "organisation", "place", "project", "product", "event", "concept"})
_AGENTS = frozenset({"person", "organisation", "concept"})
_BODIES = frozenset({"organisation", "project", "concept"})

DEFAULT_GRAPH_SCHEMA = GraphSchema(
    entity_types=(
        EntityTypeSpec("person", description="A named individual.",
                       synonyms=("people", "individual", "human")),
        EntityTypeSpec("organisation", description="A company, institution or team.",
                       synonyms=("organization", "org", "company", "institution", "team")),
        EntityTypeSpec("place", description="A location.",
                       synonyms=("location", "city", "country", "region", "site")),
        EntityTypeSpec("project", description="A named initiative or codebase."),
        EntityTypeSpec("product", description="A product, service or system.",
                       synonyms=("service", "system", "software", "tool")),
        EntityTypeSpec("event", description="A named occurrence.", synonyms=("meeting",)),
        EntityTypeSpec("concept", description="A named thing of no more specific type.",
                       synonyms=("thing", "other", "topic")),
        EntityTypeSpec("date", literal=True, description="A calendar date."),
        EntityTypeSpec("money", literal=True, description="A monetary amount.",
                       synonyms=("amount", "currency")),
        EntityTypeSpec("email", literal=True, description="An email address."),
        EntityTypeSpec("url", literal=True, description="A web address.", synonyms=("link",)),
    ),
    relation_types=(
        RelationTypeSpec(
            "works_at", frozenset({"person", "concept"}),
            frozenset({"organisation", "place", "project", "concept"}),
            "Employment or affiliation.", ("works_for", "employed_by", "employee_of"),
        ),
        RelationTypeSpec(
            "leads", frozenset({"person", "concept"}), _BODIES | frozenset({"place"}),
            "Heads an organisation, team or project.",
            ("heads", "runs", "ceo_of", "director_of", "head_of", "leader_of"),
        ),
        RelationTypeSpec(
            "reports_to", frozenset({"person", "concept"}), frozenset({"person", "concept"}),
            "Line-management relation between people.", ("reports_into", "managed_by"),
        ),
        RelationTypeSpec(
            "manages", _AGENTS, _THINGS,
            "Owns, manages or operates something.", ("owns", "operates", "oversees"),
        ),
        RelationTypeSpec(
            "located_in", _THINGS, frozenset({"place", "concept"}),
            "Physical or organisational location.", ("based_in", "located_at"),
        ),
        RelationTypeSpec(
            "acquired", _AGENTS, frozenset({"organisation", "product", "project", "concept"}),
            "Acquisition or purchase.", ("bought", "purchased", "acquires"),
        ),
        RelationTypeSpec(
            "depends_on", frozenset({"product", "project", "organisation", "concept"}),
            frozenset({"product", "project", "organisation", "concept"}),
            "A technical or operational dependency.", ("requires", "relies_on", "uses"),
        ),
        RelationTypeSpec(
            "part_of", _THINGS, _THINGS, "Membership or containment.",
            ("member_of", "belongs_to", "subsidiary_of", "component_of"),
        ),
        RelationTypeSpec(
            "founded", _AGENTS, _BODIES | frozenset({"product"}),
            "Founded or created.", ("founder_of", "created", "established"),
        ),
    ),
)
