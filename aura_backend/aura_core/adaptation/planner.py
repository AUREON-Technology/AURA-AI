"""Capability registry and evidence-based planner (AP5, ADR-023).

:class:`CapabilityRegistry` loads the capability primitives -- one YAML file each, in
``capabilities/`` next to this module -- through the strict codec, so a file with an unknown key
or a wrong type is refused rather than half-read. Its digest pins exactly which primitives a plan
was made from.

:func:`plan_capabilities` matches every primitive against one Project Twin:

1. **Candidates** are the twin's elements of the primitive's ``target`` kind that pass its
   ``target_filter``. A primitive with no candidate at all is reported ``no_candidates``.
2. **Requirements** are checked per candidate. Each matched element becomes evidence; elements
   below the primitive's ``min_confidence`` do not count (a proposal or a weak inference cannot
   carry a capability on its own). Unmet requirements are reasons, never stubs.
3. **Score** = relevance x the weakest evidence confidence, minus small cost and risk penalties.
   Below :data:`SELECTION_THRESHOLD` the candidate is reported ``below_threshold``.
4. **Agents** are composed from what was selected, never declared: one project-wide proposal,
   and one per source-system ROLE that can read something -- covering the entities that role's
   PERMISSIONs reach, extended through proposed ``same_as`` matches to the same entity in another
   source.

There is no domain knowledge anywhere in this module or the capability files
(``test_capabilities_are_domain_agnostic``): the same primitives plan every connected system.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..domain.ausm import AusmElement, ElementKind, ProjectTwin
from ..domain.capabilities import (
    AgentProposal,
    CapabilityPlan,
    CapabilitySpec,
    PlannedCapability,
    RejectedCapability,
    Requirement,
    RequirementRelation,
)
from ..domain.errors import NotFoundError, ValidationError
from ..domain.serialization import content_digest, from_data, to_data
from ..domain.tools import ToolSensitivity
from ..domain.untrusted import UntrustedText
from .normalize import humanize

__all__ = ["CapabilityRegistry", "PlanningService", "plan_capabilities", "SELECTION_THRESHOLD", "CAPABILITIES_DIR"]

CAPABILITIES_DIR = Path(__file__).resolve().parent / "capabilities"
SELECTION_THRESHOLD = 0.3
_MAX_EVIDENCE_PER_REQUIREMENT = 12
_READ_ACCESS = ("read", "read_write")


class CapabilityRegistry:
    """An immutable set of capability primitives, keyed by id."""

    def __init__(self, specs: Iterable[CapabilitySpec]) -> None:
        ordered = sorted(specs, key=lambda s: s.id)
        ids = [s.id for s in ordered]
        if len(set(ids)) != len(ids):
            raise ValidationError("duplicate capability ids", details={"ids": ids})
        self._specs = tuple(ordered)
        self.digest = content_digest(to_data(self._specs))

    @classmethod
    def from_directory(cls, directory: Path = CAPABILITIES_DIR) -> "CapabilityRegistry":
        import yaml  # lazy: only the loader needs it

        specs = []
        for path in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = from_data(CapabilitySpec, data, path=path.name)
            if path.stem != spec.id:
                raise ValidationError("a capability file is named after its id", details={"file": path.name})
            specs.append(spec)
        return cls(specs)

    def __iter__(self):
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def get(self, capability_id: str) -> CapabilitySpec | None:
        return next((s for s in self._specs if s.id == capability_id), None)

    def ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self._specs)


# ------------------------------------------------------------------------------- planning


class _Index:
    def __init__(self, twin: ProjectTwin) -> None:
        self.twin = twin
        self.by_id = {e.id: e for e in twin.elements}
        self.children: dict[str, list[AusmElement]] = {}
        self.referrers: dict[tuple[str, str], list[AusmElement]] = {}
        for element in twin.elements:
            if element.parent:
                self.children.setdefault(element.parent, []).append(element)
            for name, target in element.refs.items():
                self.referrers.setdefault((name, target), []).append(element)

    def related(self, requirement: Requirement, target: AusmElement) -> list[AusmElement]:
        if requirement.relation is RequirementRelation.CHILD:
            found = self.children.get(target.id, [])
        elif requirement.relation is RequirementRelation.REFERENCED_BY:
            found = self.referrers.get((requirement.ref, target.id), [])
        else:
            ref = target.refs.get(requirement.ref or "")
            found = [self.by_id[ref]] if ref in self.by_id else []
        return [e for e in found if e.kind is requirement.kind and requirement.filter.matches(e)]

    def entity_of(self, element: AusmElement) -> str | None:
        """The entity a selection on ``element`` is about -- what roles and agents group by."""
        if element.kind is ElementKind.ENTITY:
            return element.id
        if element.kind is ElementKind.ATTRIBUTE:
            return element.parent
        if element.kind is ElementKind.RELATION:
            return element.refs.get("from")
        return element.refs.get("entity")


def plan_capabilities(twin: ProjectTwin, registry: CapabilityRegistry, *,
                      threshold: float = SELECTION_THRESHOLD) -> CapabilityPlan:
    """Plan ``twin`` against ``registry``. Pure and deterministic."""
    base = dict(project_id=twin.project_id, twin_version=twin.version, twin_digest=twin.digest,
                registry_digest=registry.digest)
    if not any(e.kind is not ElementKind.SOURCE for e in twin.elements):
        return CapabilityPlan(**base, reason="empty_twin")
    index = _Index(twin)
    readers = _readers(index)
    selected: list[PlannedCapability] = []
    rejected: list[RejectedCapability] = []
    for spec in registry:
        candidates = [e for e in twin.of_kind(spec.target) if spec.target_filter.matches(e)]
        if not candidates:
            rejected.append(RejectedCapability(spec.id, "", ("no_candidates",)))
            continue
        for target in candidates:
            outcome = _match(spec, target, index, threshold)
            if isinstance(outcome, RejectedCapability):
                rejected.append(outcome)
                continue
            entity = index.entity_of(target)
            outcome_roles = tuple(sorted(readers.get(entity, ()))) if entity else ()
            selected.append(_with_roles(outcome, outcome_roles))
    selected.sort(key=lambda s: (s.capability, s.target))
    rejected.sort(key=lambda r: (r.capability, r.target))
    agents = _agents(selected, index, readers) if selected else []
    return CapabilityPlan(**base, selected=tuple(selected), rejected=tuple(rejected), agents=tuple(agents))


def _match(spec: CapabilitySpec, target: AusmElement, index: _Index, threshold: float):
    reasons: list[str] = []
    evidence: list[str] = [target.id]
    confidences = [target.confidence]
    if target.confidence < spec.min_confidence:
        reasons.append("low_confidence")
    for requirement in spec.requires:
        matches = sorted(
            (e for e in index.related(requirement, target) if e.confidence >= spec.min_confidence),
            key=lambda e: (-e.confidence, e.id),
        )
        if len(matches) < requirement.min:
            reasons.append(f"unmet:{requirement.name}")
            continue
        chosen = matches[:max(requirement.min, _MAX_EVIDENCE_PER_REQUIREMENT)]
        evidence.extend(e.id for e in chosen)
        # The requirement is carried by its strongest `min` matches.
        confidences.append(chosen[requirement.min - 1].confidence)
    if not spec.available:
        reasons.append(f"unavailable:{spec.unavailable_reason}")
    if reasons:
        return RejectedCapability(spec.id, target.id, tuple(reasons))
    confidence = round(min(confidences), 4)
    score = round(spec.relevance * confidence - 0.05 * (spec.cost - 1) - 0.05 * int(spec.risk), 4)
    if score < threshold:
        return RejectedCapability(spec.id, target.id, ("below_threshold",))
    return PlannedCapability(
        capability=spec.id, target=target.id, evidence=tuple(dict.fromkeys(evidence)),
        score=score, confidence=confidence, risk=spec.risk, cost=spec.cost,
    )


def _with_roles(selection: PlannedCapability, roles: tuple[str, ...]) -> PlannedCapability:
    return PlannedCapability(
        selection.capability, selection.target, selection.evidence, selection.score,
        selection.confidence, selection.risk, selection.cost, roles,
    )


def _same_as(index: _Index) -> dict[str, list[tuple[str, str]]]:
    """entity id -> [(matched entity id, proposal id)], both directions."""
    out: dict[str, list[tuple[str, str]]] = {}
    for element in index.twin.of_kind(ElementKind.RELATION):
        if element.properties.get("relation_type") != "same_as":
            continue
        a, b = element.refs.get("from"), element.refs.get("to")
        if a and b:
            out.setdefault(a, []).append((b, element.id))
            out.setdefault(b, []).append((a, element.id))
    return out


def _readers(index: _Index) -> dict[str, set[str]]:
    """entity id -> ROLE ids with a read PERMISSION on it (directly or through ``same_as``)."""
    direct: dict[str, set[str]] = {}
    for permission in index.twin.of_kind(ElementKind.PERMISSION):
        if permission.properties.get("access") in _READ_ACCESS:
            direct.setdefault(permission.refs["target"], set()).add(permission.refs["role"])
    matches = _same_as(index)
    out = {k: set(v) for k, v in direct.items()}
    for entity, roles in direct.items():
        for other, _ in matches.get(entity, ()):
            out.setdefault(other, set()).update(roles)
    return out


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "role"
    return slug if slug[0].isalpha() else f"r_{slug}"


def _agents(selected: Sequence[PlannedCapability], index: _Index, readers: dict[str, set[str]]) -> list[AgentProposal]:
    entity_of = {s.key: index.entity_of(index.by_id[s.target]) for s in selected}
    sources = sorted(e.id for e in index.twin.of_kind(ElementKind.SOURCE))
    agents = [AgentProposal(
        id="agent.project", label=UntrustedText("Project assistant"), role=None,
        capabilities=tuple(s.key for s in selected),
        entities=tuple(sorted({e for e in entity_of.values() if e})),
        evidence=tuple(sources),
        risk_ceiling=max((s.risk for s in selected), default=ToolSensitivity.PUBLIC),
    )]
    matches = _same_as(index)
    used: set[str] = {"agent.project"}
    for role in index.twin.of_kind(ElementKind.ROLE):
        permissions = [
            p for p in index.twin.of_kind(ElementKind.PERMISSION)
            if p.refs["role"] == role.id and p.properties.get("access") in _READ_ACCESS
        ]
        entities = {p.refs["target"] for p in permissions}
        evidence = {p.id for p in permissions}
        for entity in sorted(entities):
            for other, proposal in matches.get(entity, ()):
                entities.add(other)
                evidence.add(proposal)
        chosen = [s for s in selected if entity_of[s.key] in entities]
        if not chosen:
            continue
        identifier = role.properties.get("identifier")
        name = identifier if isinstance(identifier, str) else role.locator
        agent_id = f"agent.role.{_slug(name)}"[:64]
        suffix = 2
        while agent_id in used:
            agent_id = f"agent.role.{_slug(name)}_{suffix}"[:64]
            suffix += 1
        used.add(agent_id)
        agents.append(AgentProposal(
            id=agent_id, label=UntrustedText.of(f"{humanize(name)} assistant", max_chars=200), role=role.id,
            capabilities=tuple(s.key for s in chosen),
            entities=tuple(sorted(e for e in entities if any(entity_of[s.key] == e for s in chosen))),
            evidence=tuple(sorted(evidence)),
            risk_ceiling=max(s.risk for s in chosen),
        ))
    return agents


class PlanningService:
    """Plans a stored twin version. Plans are recomputed, not stored: same twin + registry
    digests always give the same plan, so a stored copy could only ever go stale."""

    def __init__(self, *, registry: CapabilityRegistry, twins: Any, metrics: Any = None) -> None:
        self._registry = registry
        self._twins = twins
        self._metrics = metrics

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry

    async def plan(self, version: int | None = None) -> CapabilityPlan:
        if version is None:
            latest = await self._twins.versions(limit=1)
            if not latest:
                raise NotFoundError("no twin version to plan from; take a snapshot first")
            version = latest[0].version
        found = await self._twins.get(version)
        if found is None:
            raise NotFoundError("twin version not found", details={"version": version})
        plan = plan_capabilities(found[1], self._registry)
        if self._metrics is not None:
            self._metrics.observe_capability_plan(plan)
        return plan
