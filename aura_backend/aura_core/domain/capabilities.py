"""Capability primitives and capability plans (AP5, ADR-023).

A **capability** is a domain-agnostic thing AURA can do over a connected system -- look an entity
up by its identifier, search it, aggregate a metric, show a workflow's status. It is declared as
data (``aura_core/adaptation/capabilities/*.yaml``), never as a per-industry class, and says:

* what it applies to (``target``: an AUSM element kind, narrowed by property filters);
* what the twin must also contain for it to work (``requires``: predicates relative to the
  target -- "at least one child attribute whose semantic type is ``identifier``");
* what it would produce downstream (``produces``: a tool template, UI views, eval case kinds);
* how risky and costly it is (``risk`` sets the generated tool's sensitivity floor).

A :class:`CapabilityPlan` is the planner's output for one twin version: every capability it
recommends, with the AUSM ids that satisfied it (``evidence``) and a score; every one it did not,
with reasons; and agent proposals composed from the recommended ones. A plan is a proposal --
nothing in it executes, and AP6 compiles it into a blueprint a Super Admin approves (ADR-024).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .ausm import PROJECT_ID_PATTERN, AusmElement, ElementKind
from .errors import ValidationError
from .serialization import content_digest
from .tools import ToolSensitivity
from .untrusted import UntrustedText

__all__ = [
    "RequirementRelation",
    "ElementFilter",
    "Requirement",
    "Produces",
    "CapabilitySpec",
    "PlannedCapability",
    "RejectedCapability",
    "AgentProposal",
    "CapabilityPlan",
]

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_AGENT_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_PROPERTY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_SUMMARY = 400


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


@dataclass(frozen=True, slots=True)
class ElementFilter:
    """Property filters on an element: every ``where`` key must hold one of its listed values
    (a missing property counts as ``null``); no ``exclude`` key may hold one of its values."""

    where: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    exclude: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for mapping in (self.where, self.exclude):
            for key, values in mapping.items():
                if not _PROPERTY.match(key):
                    raise ValidationError("filter keys are property names", details={"key": key})
                if not isinstance(values, tuple) or not values or not all(_scalar(v) for v in values):
                    raise ValidationError("filter values are a non-empty list of scalars", details={"key": key})

    def matches(self, element: AusmElement) -> bool:
        props = element.properties
        if any(props.get(key) not in values for key, values in self.where.items()):
            return False
        return not any(props.get(key) in values for key, values in self.exclude.items())


class RequirementRelation(str, enum.Enum):
    CHILD = "child"                  # element.parent == target
    REFERENCED_BY = "referenced_by"  # element.refs[ref] == target
    REF = "ref"                      # target.refs[ref] == element

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Requirement:
    """At least ``min`` elements of ``kind``, related to the target as ``relation`` says, that
    pass ``filter``. Elements below the capability's ``min_confidence`` do not count."""

    name: str
    relation: RequirementRelation
    kind: ElementKind
    ref: str | None = None
    filter: ElementFilter = field(default_factory=ElementFilter)
    min: int = 1

    def __post_init__(self) -> None:
        if not _SLUG.match(self.name):
            raise ValidationError("requirement names are snake_case", details={"name": self.name})
        if self.min < 1:
            raise ValidationError("a requirement needs min >= 1", details={"name": self.name})
        needs_ref = self.relation in (RequirementRelation.REFERENCED_BY, RequirementRelation.REF)
        if needs_ref != (self.ref is not None):
            raise ValidationError("'ref' is required exactly for referenced_by and ref", details={"name": self.name})


@dataclass(frozen=True, slots=True)
class Produces:
    """What AP6's factories derive from a selection. Names only -- never code."""

    tool: str | None = None
    views: tuple[str, ...] = ()
    evals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ((self.tool,) if self.tool else ()) + self.views + self.evals:
            if not re.match(r"^[a-z][a-z0-9_.]{0,63}$", name):
                raise ValidationError("produced artifact names are dotted slugs", details={"name": name})


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """One primitive, as declared in a capability file. Written and reviewed by humans."""

    id: str
    version: int
    summary: str
    target: ElementKind
    target_filter: ElementFilter = field(default_factory=ElementFilter)
    requires: tuple[Requirement, ...] = ()
    risk: ToolSensitivity = ToolSensitivity.PERSONAL
    cost: int = 1
    relevance: float = 0.5
    min_confidence: float = 0.5
    produces: Produces = field(default_factory=Produces)
    #: ``False`` for a primitive the platform deliberately does not offer yet (writes, ADR-021):
    #: it is still matched, so the plan can say *why* it was not recommended.
    available: bool = True
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not _CAPABILITY_ID.match(self.id):
            raise ValidationError("capability ids are dotted slugs, e.g. entity.search", details={"id": self.id})
        if self.version < 1:
            raise ValidationError("capability versions start at 1", details={"id": self.id})
        if not self.summary or len(self.summary) > _MAX_SUMMARY:
            raise ValidationError("summary must be 1-400 characters", details={"id": self.id})
        if not 1 <= self.cost <= 3:
            raise ValidationError("cost is 1 (cheap) to 3 (expensive)", details={"id": self.id})
        if not 0.0 < self.relevance <= 1.0 or not 0.0 <= self.min_confidence <= 1.0:
            raise ValidationError("relevance and min_confidence are within (0, 1]", details={"id": self.id})
        names = [r.name for r in self.requires]
        if len(set(names)) != len(names):
            raise ValidationError("requirement names must be unique", details={"id": self.id})
        if self.available == (self.unavailable_reason is not None):
            raise ValidationError("an unavailable capability needs a reason, an available one none",
                                  details={"id": self.id})
        if self.unavailable_reason is not None and not _SLUG.match(self.unavailable_reason):
            raise ValidationError("unavailable_reason is a snake_case code", details={"id": self.id})


@dataclass(frozen=True, slots=True)
class PlannedCapability:
    """A recommended capability on one target, with the AUSM ids that justify it."""

    capability: str
    target: str
    evidence: tuple[str, ...]
    score: float
    confidence: float
    risk: ToolSensitivity
    cost: int
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.evidence or self.target not in self.evidence:
            raise ValidationError("a selection's evidence must include its target", details={"capability": self.capability})

    @property
    def key(self) -> str:
        return f"{self.capability}@{self.target}"


@dataclass(frozen=True, slots=True)
class RejectedCapability:
    """A capability that was not recommended -- on one target, or at all (``target`` empty)."""

    capability: str
    target: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValidationError("a rejection needs at least one reason", details={"capability": self.capability})
        for reason in self.reasons:
            if not re.match(r"^[a-z][a-z0-9_]*(:[a-z0-9_]+)?$", reason):
                raise ValidationError("reasons are codes, e.g. unmet:identifier", details={"reason": reason})


@dataclass(frozen=True, slots=True)
class AgentProposal:
    """A proposed agent: recommended capabilities grouped by who may use them and what they touch.

    ``role`` is the source-system ROLE the grouping came from (``None`` for the whole-project
    agent); ``evidence`` names the PERMISSION and ``same_as`` elements that justified each entity's
    membership. ``risk_ceiling`` is the highest risk among its capabilities.
    """

    id: str
    label: UntrustedText
    role: str | None
    capabilities: tuple[str, ...]
    entities: tuple[str, ...]
    evidence: tuple[str, ...]
    risk_ceiling: ToolSensitivity

    def __post_init__(self) -> None:
        if not _AGENT_ID.match(self.id):
            raise ValidationError("agent ids are slugs", details={"id": self.id})
        if not self.capabilities:
            raise ValidationError("an agent proposal needs at least one capability", details={"id": self.id})


@dataclass(frozen=True, slots=True)
class CapabilityPlan:
    """The planner's output for one twin version. Deterministic: same twin + registry, same plan."""

    project_id: str
    twin_version: int
    twin_digest: str
    registry_digest: str
    selected: tuple[PlannedCapability, ...] = ()
    rejected: tuple[RejectedCapability, ...] = ()
    agents: tuple[AgentProposal, ...] = ()
    #: Set when the plan is empty for a reason that is not "nothing matched" (e.g. ``empty_twin``).
    reason: str | None = None

    def __post_init__(self) -> None:
        if not PROJECT_ID_PATTERN.match(self.project_id):
            raise ValidationError("project_id must be a short slug")
        for digest in (self.twin_digest, self.registry_digest):
            if not _SHA256.match(digest):
                raise ValidationError("digests are sha256:<64 hex>")
        keys = [s.key for s in self.selected]
        if len(set(keys)) != len(keys):
            raise ValidationError("duplicate selections")
        if not self.selected and self.agents:
            raise ValidationError("agents need selected capabilities")

    @property
    def digest(self) -> str:
        return content_digest(self)

    def summary(self) -> dict[str, Any]:
        per: dict[str, int] = {}
        for selection in self.selected:
            per[selection.capability] = per.get(selection.capability, 0) + 1
        evidence = [len(s.evidence) for s in self.selected]
        return {
            "selected": len(self.selected),
            "rejected": len(self.rejected),
            "agents": len(self.agents),
            "by_capability": dict(sorted(per.items())),
            "evidence_min": min(evidence) if evidence else 0,
            "evidence_mean": round(sum(evidence) / len(evidence), 2) if evidence else 0.0,
        }
