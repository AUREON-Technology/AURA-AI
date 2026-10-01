"""AUSM -- the AURA Universal Semantic Model (ADR-020).

A connected system describes the same concept in different shapes: a SQL table, a REST path, a
CSV file, an ORM model. Discovery (AP3) and normalisation (AP4) turn all of them into one
vocabulary of **elements**, so every later stage -- capability planning, the factories, drift
impact -- is written once against AUSM and never against a source technology.

The vocabulary is ADR-020's fourteen concepts: thirteen :class:`ElementKind`\\ s, plus the
:class:`Provenance` record every element must carry (provenance is attached to elements rather
than being one).

Three rules make the model safe to build on:

* **Stable identity.** An element's id is ``{source_id}:{locator}`` -- the connector it came from
  and where in that source it lives. Re-discovering an unchanged system yields the same ids, which
  is what makes twins diffable (AP4) and evidence links durable (AP5).
* **Mandatory provenance.** Every element says which extractor asserted it and how confidently.
  A model may *propose* labels, but anything an ``llm`` extractor asserted is below certainty
  (ADR-026) -- structure comes from deterministic extractors.
* **Untrusted text stays wrapped.** Labels and descriptions are
  :class:`~aura_core.domain.untrusted.UntrustedText` (ADR-022).

This module is the data model only. Discovery, normalisation and diffing are engine code (AP3/AP4).
"""

from __future__ import annotations

import bisect
import enum
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ValidationError
from .serialization import content_digest, json_schema, to_data
from .untrusted import UntrustedText

__all__ = [
    "ElementKind",
    "Provenance",
    "AusmElement",
    "ProjectTwin",
    "element_id",
    "SOURCE_LOCATOR",
    "MODEL_EXTRACTOR_PREFIX",
    "PROJECT_ID_PATTERN",
    "project_twin_json_schema",
]

_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
#: A project id: one per install (ADR-019), so short and boring.
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_EXTRACTOR = re.compile(r"^[a-z][a-z0-9_.:@-]{0,63}$")
_PROPERTY_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_LOCATOR = 512
_MAX_PROPERTY_STR = 256

#: The locator of the one SOURCE element each connector contributes.
SOURCE_LOCATOR = "source"
#: Extractors whose name starts with this are model-driven; their assertions stay below 1.0.
MODEL_EXTRACTOR_PREFIX = "llm"


class ElementKind(str, enum.Enum):
    ENTITY = "entity"            # a thing with identity: table, collection, schema object, file
    ATTRIBUTE = "attribute"      # a field of an entity (parent: ENTITY)
    RELATION = "relation"        # a typed link between two entities (refs: from, to)
    EVENT = "event"              # something that happens to an entity over time (refs: entity)
    ACTION = "action"            # an operation the system exposes (optional ref: entity)
    WORKFLOW = "workflow"        # an inferred or declared process over states (optional ref: entity)
    ROLE = "role"                # a principal class in the source system
    PERMISSION = "permission"    # a role's right over an element (refs: role, target)
    DOCUMENT = "document"        # unstructured content destined for knowledge
    METRIC = "metric"            # a measurable aggregate (optional ref: entity)
    STATE = "state"              # one value of a status-like attribute (parent: ATTRIBUTE)
    CONSTRAINT = "constraint"    # a rule the source enforces (parent: the constrained element)
    SOURCE = "source"            # the connected source itself; one per connector

    def __str__(self) -> str:
        return self.value


# kind -> the kind its parent must be (None: a parent is not allowed; ... : any kind).
_PARENT_RULES: Mapping[ElementKind, Any] = {
    ElementKind.ATTRIBUTE: frozenset({ElementKind.ENTITY}),
    ElementKind.STATE: frozenset({ElementKind.ATTRIBUTE}),
    ElementKind.CONSTRAINT: ...,
}
# kind -> {ref name: allowed target kinds (... = any), required?}
_REF_RULES: Mapping[ElementKind, Mapping[str, tuple[Any, bool]]] = {
    ElementKind.RELATION: {
        "from": (frozenset({ElementKind.ENTITY}), True),
        "to": (frozenset({ElementKind.ENTITY}), True),
        "via": (frozenset({ElementKind.ATTRIBUTE}), False),
    },
    ElementKind.EVENT: {"entity": (frozenset({ElementKind.ENTITY}), True)},
    ElementKind.ACTION: {"entity": (frozenset({ElementKind.ENTITY}), False)},
    ElementKind.WORKFLOW: {
        "entity": (frozenset({ElementKind.ENTITY}), False),
        "state_attribute": (frozenset({ElementKind.ATTRIBUTE}), False),
    },
    ElementKind.PERMISSION: {
        "role": (frozenset({ElementKind.ROLE}), True),
        "target": (..., True),
    },
    ElementKind.METRIC: {"entity": (frozenset({ElementKind.ENTITY}), False)},
}


def element_id(source_id: str, locator: str) -> str:
    """The stable id of the element at ``locator`` in source ``source_id``."""
    return f"{source_id}:{locator}"


def _check_locator(locator: Any, what: str) -> None:
    if not isinstance(locator, str) or not locator or len(locator) > _MAX_LOCATOR:
        raise ValidationError(f"{what} must be a non-empty string of at most {_MAX_LOCATOR} chars")
    UntrustedText(locator)  # same character rules as any other discovered text


@dataclass(frozen=True, slots=True)
class Provenance:
    """One extractor's assertion of one element: where, by what, and how confidently."""

    source_id: str
    locator: str
    extractor: str
    confidence: float
    evidence_hash: str

    def __post_init__(self) -> None:
        if not _SLUG.match(self.source_id):
            raise ValidationError("provenance source_id must be a slug", details={"source_id": self.source_id})
        _check_locator(self.locator, "provenance locator")
        if not _EXTRACTOR.match(self.extractor):
            raise ValidationError("provenance extractor must be a slug", details={"extractor": self.extractor})
        if not 0.0 <= self.confidence <= 1.0:
            raise ValidationError("confidence must be within [0, 1]", details={"confidence": self.confidence})
        if self.is_model_asserted and self.confidence >= 1.0:
            # ADR-026: a model labels and proposes; it never asserts with certainty.
            raise ValidationError(
                "a model-driven extractor cannot assert with confidence 1.0",
                details={"extractor": self.extractor},
            )
        if not _SHA256.match(self.evidence_hash):
            raise ValidationError("evidence_hash must be sha256:<64 hex>")

    @property
    def is_model_asserted(self) -> bool:
        return self.extractor.startswith(MODEL_EXTRACTOR_PREFIX)


@dataclass(frozen=True, slots=True)
class AusmElement:
    """One normalised element of a connected system.

    ``properties`` holds kind-specific structured facts (a column's ``data_type`` and
    ``nullable``, an operation's ``http_method``, a profile's ``distinct_ratio``) as JSON scalars.
    ``refs`` names the elements this one points at; which names a kind requires is fixed by
    ``_REF_RULES`` and checked against the twin by :class:`ProjectTwin`.
    """

    source_id: str
    locator: str
    kind: ElementKind
    label: UntrustedText
    provenance: tuple[Provenance, ...]
    description: UntrustedText | None = None
    parent: str | None = None
    refs: Mapping[str, str] = field(default_factory=dict)
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _SLUG.match(self.source_id):
            raise ValidationError("source_id must be a slug", details={"source_id": self.source_id})
        _check_locator(self.locator, "locator")
        if self.kind is ElementKind.SOURCE and self.locator != SOURCE_LOCATOR:
            raise ValidationError(f"a SOURCE element's locator must be {SOURCE_LOCATOR!r}")
        if not self.provenance:
            raise ValidationError("every element needs provenance", details={"id": self.id})
        if self.source_id not in {p.source_id for p in self.provenance}:
            raise ValidationError(
                "an element's own source must be among its provenance", details={"id": self.id}
            )
        parent_rule = _PARENT_RULES.get(self.kind)
        if parent_rule is not None and self.parent is None:
            raise ValidationError(f"a {self.kind} element needs a parent", details={"id": self.id})
        if parent_rule is None and self.parent is not None:
            raise ValidationError(f"a {self.kind} element cannot have a parent", details={"id": self.id})
        rules = _REF_RULES.get(self.kind, {})
        unknown = sorted(set(self.refs) - set(rules))
        if unknown:
            raise ValidationError(
                f"unknown refs for a {self.kind} element", details={"id": self.id, "refs": unknown}
            )
        missing = sorted(name for name, (_, required) in rules.items() if required and name not in self.refs)
        if missing:
            raise ValidationError(
                f"missing required refs for a {self.kind} element", details={"id": self.id, "refs": missing}
            )
        for key, value in self.properties.items():
            _check_property(self.id, key, value)
        object.__setattr__(self, "refs", MappingProxyType(dict(self.refs)))
        object.__setattr__(self, "properties", MappingProxyType(dict(self.properties)))

    @property
    def id(self) -> str:
        return element_id(self.source_id, self.locator)

    @property
    def confidence(self) -> float:
        """The strongest assertion of this element across its sources."""
        return max(p.confidence for p in self.provenance)


def _check_property(owner: str, key: Any, value: Any) -> None:
    if not isinstance(key, str) or not _PROPERTY_KEY.match(key):
        raise ValidationError("property keys must be snake_case slugs", details={"id": owner, "key": key})
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        to_data(value)  # rejects NaN / infinity
        return
    if isinstance(value, str):
        if len(value) > _MAX_PROPERTY_STR:
            raise ValidationError("property value too long", details={"id": owner, "key": key})
        UntrustedText(value)
        return
    raise ValidationError("property values must be JSON scalars", details={"id": owner, "key": key})


@dataclass(frozen=True, slots=True)
class ProjectTwin:
    """One versioned snapshot of a connected system in AUSM (ADR-020).

    ``elements`` must be in canonical order (sorted by id, unique) -- use :meth:`build` to sort.
    Every parent and ref must resolve inside the twin with an allowed kind, and every element's
    source must contribute a SOURCE element. :attr:`digest` covers the project and its elements
    but **not** the version, so re-discovering an unchanged system yields the same digest.
    """

    project_id: str
    version: int
    elements: tuple[AusmElement, ...]

    def __post_init__(self) -> None:
        if not PROJECT_ID_PATTERN.match(self.project_id):
            raise ValidationError("project_id must be a short slug", details={"project_id": self.project_id})
        if self.version < 1:
            raise ValidationError("twin versions start at 1", details={"version": self.version})
        ids = [e.id for e in self.elements]
        if ids != sorted(ids):
            raise ValidationError("twin elements must be sorted by id; use ProjectTwin.build")
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValidationError("duplicate element ids", details={"ids": dupes[:10]})
        by_id = {e.id: e for e in self.elements}
        sources = {e.source_id for e in self.elements if e.kind is ElementKind.SOURCE}
        for element in self.elements:
            if element.source_id not in sources:
                raise ValidationError(
                    "element's source has no SOURCE element", details={"id": element.id}
                )
            if element.parent is not None:
                _check_target(by_id, element, "parent", element.parent, _PARENT_RULES[element.kind])
            rules = _REF_RULES.get(element.kind, {})
            for name, target in element.refs.items():
                _check_target(by_id, element, f"refs.{name}", target, rules[name][0])

    @classmethod
    def build(cls, project_id: str, version: int, elements: tuple[AusmElement, ...] | list[AusmElement]) -> "ProjectTwin":
        return cls(project_id, version, tuple(sorted(elements, key=lambda e: e.id)))

    @property
    def digest(self) -> str:
        return content_digest({"project_id": self.project_id, "elements": to_data(self.elements)})

    def get(self, element: str) -> AusmElement | None:
        index = bisect.bisect_left(self.elements, element, key=lambda e: e.id)
        if index < len(self.elements) and self.elements[index].id == element:
            return self.elements[index]
        return None

    def of_kind(self, kind: ElementKind) -> tuple[AusmElement, ...]:
        return tuple(e for e in self.elements if e.kind is kind)

    def ids(self) -> frozenset[str]:
        return frozenset(e.id for e in self.elements)


def _check_target(by_id: Mapping[str, AusmElement], owner: AusmElement, what: str, target: str, allowed: Any) -> None:
    resolved = by_id.get(target)
    if resolved is None:
        raise ValidationError(f"{what} does not resolve", details={"id": owner.id, "target": target})
    if allowed is not ... and resolved.kind not in allowed:
        raise ValidationError(
            f"{what} points at a {resolved.kind}, which is not allowed here",
            details={"id": owner.id, "target": target},
        )


def project_twin_json_schema() -> dict[str, Any]:
    """The published JSON Schema of :class:`ProjectTwin` (``docs/schemas/project_twin.v1.json``)."""
    return json_schema(
        ProjectTwin,
        schema_id="https://aura.local/schemas/project_twin.v1.json",
        title="AURA Project Twin (AUSM snapshot, v1)",
    )
