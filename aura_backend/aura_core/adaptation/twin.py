"""Project Twin: build, diff and version (AP4, ADR-020).

:func:`build_twin` is the whole deterministic pipeline -- normalise the discovery reports,
propose cross-source matches, propose workflows -- and yields a
:class:`~aura_core.domain.ausm.ProjectTwin` whose digest depends only on what was discovered.
:func:`diff_twins` compares two twins. :class:`TwinService` runs discovery on the active
connectors, builds, labels (optionally), diffs against the latest stored version and appends a
new version -- or, when nothing changed, returns the latest one rather than minting a duplicate.

Rename detection (:func:`diff_twins`) works on removed x added pairs of the same kind:

* **entities** pair up by the overlap of their attribute names (a renamed table keeps its
  columns);
* **attributes** under the same -- or a renamed -- parent pair up by profile similarity: data
  type, nullability, semantic type, value set, position and the sampled ratios;
* anything else (a relation, a state, a constraint) is *implied* when its locator differs only by
  an already-matched rename.

Each match is a :class:`~aura_core.domain.twin.RenameCandidate` with a score and a reason; the
diff still lists both ids as removed and added, because a candidate is not a fact.
"""

from __future__ import annotations

import dataclasses
from datetime import timezone
from typing import Any, Sequence

from ..domain.ausm import AusmElement, ElementKind, ProjectTwin
from ..domain.discovery import DiscoveryBudget, DiscoveryReport
from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..domain.serialization import to_data
from ..domain.twin import ElementChange, RenameCandidate, TwinDiff, TwinIssue, TwinVersion
from ..observability.logging import get_logger
from ..ports.adaptation import TwinStore
from ..ports.runtime import AuditEvent, Clock
from .labeling import LabelingPass
from .matching import merge_proposals
from .normalize import Normalized, name_tokens, normalize
from .process_mining import workflow_candidates

__all__ = ["build_twin", "BuiltTwin", "diff_twins", "TwinService", "SnapshotResult", "RENAME_THRESHOLD"]

logger = get_logger("adaptation.twin")

RENAME_THRESHOLD = 0.6
_COMPARED_FIELDS = ("kind", "label", "description", "parent", "refs", "properties", "provenance")


@dataclasses.dataclass(frozen=True, slots=True)
class BuiltTwin:
    twin: ProjectTwin
    issues: tuple[TwinIssue, ...]
    sources: dict[str, str]


def build_twin(project_id: str, version: int, reports: Sequence[DiscoveryReport]) -> BuiltTwin:
    """The deterministic part of a snapshot: same reports in, same twin (and digest) out."""
    normalized: Normalized = normalize(reports)
    elements = list(normalized.elements)
    elements.extend(merge_proposals(elements))
    elements.extend(workflow_candidates(elements))
    twin = ProjectTwin.build(project_id, version, elements)
    return BuiltTwin(twin, normalized.issues, dict(normalized.sources))


# ------------------------------------------------------------------------------------- diff


def diff_twins(old: ProjectTwin | None, new: ProjectTwin) -> TwinDiff:
    if old is not None and old.project_id != new.project_id:
        raise ValidationError("cannot diff twins of different projects")
    before = {e.id: e for e in old.elements} if old else {}
    after = {e.id: e for e in new.elements}
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = []
    for element_id in sorted(set(before) & set(after)):
        a, b = before[element_id], after[element_id]
        fields = tuple(f for f in _COMPARED_FIELDS if to_data(getattr(a, f)) != to_data(getattr(b, f)))
        if fields:
            changed.append(ElementChange(element_id, b.kind, fields))
    renames = _renames([before[i] for i in removed], [after[i] for i in added], before, after) if old else []
    return TwinDiff(
        project_id=new.project_id,
        from_version=old.version if old else None, to_version=new.version,
        from_digest=old.digest if old else None, to_digest=new.digest,
        added=tuple(added), removed=tuple(removed), changed=tuple(changed), renames=tuple(renames),
    )


def _children(elements: dict[str, AusmElement], parent: str) -> set[str]:
    return {
        "_".join(name_tokens(str(e.properties.get("identifier") or e.locator)))
        for e in elements.values() if e.kind is ElementKind.ATTRIBUTE and e.parent == parent
    }


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


_PROFILE_KEYS = ("data_type", "nullable", "semantic_type", "value_set", "format", "pattern", "primary_key", "unique")


def _profile_similarity(a: AusmElement, b: AusmElement) -> float:
    score, weight = 0.0, 0.0
    for key in _PROFILE_KEYS:
        if key in a.properties or key in b.properties:
            weight += 1.0
            score += 1.0 if a.properties.get(key) == b.properties.get(key) else 0.0
    for key in ("distinct_ratio", "null_ratio"):
        x, y = a.properties.get(key), b.properties.get(key)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            weight += 1.0
            score += 1.0 - min(1.0, abs(float(x) - float(y)))
    x, y = a.properties.get("position"), b.properties.get("position")
    if isinstance(x, int) and isinstance(y, int):
        weight += 1.0
        score += 1.0 if x == y else 0.5 if abs(x - y) == 1 else 0.0
    return score / weight if weight else 0.0


def _pair(old: list[AusmElement], new: list[AusmElement], scorer) -> list[tuple[AusmElement, AusmElement, float]]:
    """Greedy one-to-one matching by descending score, ties broken by ids (deterministic)."""
    scored = sorted(
        ((scorer(a, b), a.id, b.id, a, b) for a in old for b in new),
        key=lambda t: (-t[0], t[1], t[2]),
    )
    used_old: set[str] = set()
    used_new: set[str] = set()
    out = []
    for score, a_id, b_id, a, b in scored:
        if score < RENAME_THRESHOLD or a_id in used_old or b_id in used_new:
            continue
        used_old.add(a_id)
        used_new.add(b_id)
        out.append((a, b, round(score, 4)))
    return out


def _renames(removed, added, before, after) -> list[RenameCandidate]:
    out: list[RenameCandidate] = []
    parent_map: dict[str, str] = {}
    by_kind = lambda items, kind: [e for e in items if e.kind is kind]  # noqa: E731

    for a, b, score in _pair(
        by_kind(removed, ElementKind.ENTITY), by_kind(added, ElementKind.ENTITY),
        lambda a, b: _jaccard(_children(before, a.id), _children(after, b.id)) if a.source_id == b.source_id else 0.0,
    ):
        parent_map[a.id] = b.id
        out.append(RenameCandidate(a.id, b.id, ElementKind.ENTITY, score, "attribute_overlap"))

    def attribute_score(a: AusmElement, b: AusmElement) -> float:
        if a.parent != b.parent and parent_map.get(a.parent or "") != b.parent:
            return 0.0
        same_name = a.properties.get("identifier") == b.properties.get("identifier")
        return 1.0 if same_name else _profile_similarity(a, b)

    for a, b, score in _pair(by_kind(removed, ElementKind.ATTRIBUTE), by_kind(added, ElementKind.ATTRIBUTE), attribute_score):
        reason = "parent_renamed" if a.properties.get("identifier") == b.properties.get("identifier") else "profile_similarity"
        parent_map[a.id] = b.id
        out.append(RenameCandidate(a.id, b.id, ElementKind.ATTRIBUTE, score, reason))

    # Everything else follows a rename already found: same kind, locator differs only by it.
    # Longest first, so ``public.employees_history`` is not rewritten by ``public.employees``.
    renamed_suffixes = sorted(
        ((_locator(old), _locator(new)) for old, new in parent_map.items()), key=lambda p: (-len(p[0]), p)
    )
    matched_old = {c.old_id for c in out}
    matched_new = {c.new_id for c in out}
    added_ids = {e.id: e for e in added}
    for element in removed:
        if element.id in matched_old or element.kind in (ElementKind.ENTITY, ElementKind.ATTRIBUTE):
            continue
        for old_loc, new_loc in renamed_suffixes:
            if old_loc not in element.id:
                continue
            candidate = element.id.replace(old_loc, new_loc)
            target = added_ids.get(candidate)
            if target is not None and target.kind is element.kind and candidate not in matched_new:
                out.append(RenameCandidate(element.id, candidate, element.kind, 1.0, "follows_rename"))
                matched_old.add(element.id)
                matched_new.add(candidate)
                break
    return sorted(out, key=lambda c: (c.old_id, c.new_id))


def _locator(element_id: str) -> str:
    """The part of an element id after its kind prefix: ``public.employees`` for a table."""
    locator = element_id.split(":", 1)[1] if ":" in element_id else element_id
    return locator.split(":", 1)[1] if ":" in locator else locator


# ------------------------------------------------------------------------------------- service


@dataclasses.dataclass(frozen=True, slots=True)
class SnapshotResult:
    version: TwinVersion
    diff: TwinDiff
    created: bool  # False: nothing changed since the latest version, which is returned instead


class TwinService:
    """Discover every active connector, build, label, diff, append."""

    def __init__(
        self,
        *,
        project_id: str,
        registry: Any,
        discovery: Any,
        store: TwinStore,
        labeling: LabelingPass,
        clock: Clock,
        audit: Any = None,
        metrics: Any = None,
    ) -> None:
        self._project_id = project_id
        self._registry = registry
        self._discovery = discovery
        self._store = store
        self._labeling = labeling
        self._clock = clock
        self._audit = audit
        self._metrics = metrics

    @property
    def project_id(self) -> str:
        return self._project_id

    async def snapshot(
        self, *, by: str, budget: DiscoveryBudget, connector_ids: Sequence[str] | None = None
    ) -> SnapshotResult:
        from ..domain.connectors import ConnectorState

        if connector_ids is None:
            records = await self._registry.list(state=ConnectorState.ACTIVE)
            connector_ids = [r.id for r in records]
        if not connector_ids:
            raise ValidationError("no active connectors to snapshot", details={"project": self._project_id})
        reports = [await self._discovery.discover(c, budget=budget, by=by) for c in sorted(set(connector_ids))]
        return await self.snapshot_reports(reports, by=by)

    async def snapshot_reports(self, reports: Sequence[DiscoveryReport], *, by: str) -> SnapshotResult:
        latest = await self._store.latest(self._project_id)
        previous = None
        if latest is not None:
            stored = await self._store.get(self._project_id, latest.version)
            previous = stored[1] if stored else None
        version = (latest.version + 1) if latest else 1
        built = build_twin(self._project_id, version, reports)
        elements, labeling, label_issues = await self._labeling.apply(built.twin.elements, previous=previous)
        twin = ProjectTwin.build(self._project_id, version, elements)
        if previous is not None and latest is not None and twin.digest == previous.digest:
            return SnapshotResult(latest, diff_twins(previous, previous), created=False)
        counts: dict[str, int] = {}
        for element in twin.elements:
            counts[str(element.kind)] = counts.get(str(element.kind), 0) + 1
        record = TwinVersion(
            project_id=self._project_id, version=version, digest=twin.digest,
            created_at=self._now(), created_by=by, counts=dict(sorted(counts.items())),
            sources=built.sources, issues=tuple(sorted(set(built.issues) | set(label_issues),
                                                       key=lambda i: (i.source_id, i.code, i.locator))),
            labeling=labeling, parent_version=latest.version if latest else None,
        )
        diff = diff_twins(previous, twin)
        try:
            await self._store.append(record, twin)
        except ConflictError:
            raise ConflictError(
                "another snapshot was taken concurrently; retry", details={"version": version}
            ) from None
        await self._record(record, diff)
        return SnapshotResult(record, diff, created=True)

    async def versions(self, *, limit: int = 50) -> Sequence[TwinVersion]:
        return await self._store.versions(self._project_id, limit=limit)

    async def get(self, version: int) -> tuple[TwinVersion, ProjectTwin] | None:
        return await self._store.get(self._project_id, version)

    async def diff(self, from_version: int, to_version: int) -> TwinDiff:
        old = await self._store.get(self._project_id, from_version)
        new = await self._store.get(self._project_id, to_version)
        if old is None or new is None:
            raise NotFoundError("twin version not found", details={"from": from_version, "to": to_version})
        return diff_twins(old[1], new[1])

    def _now(self) -> str:
        return self._clock.now().astimezone(timezone.utc).isoformat()

    async def _record(self, record: TwinVersion, diff: TwinDiff) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(AuditEvent(
                action="adaptation.twin.snapshot", actor=record.created_by,
                resource=f"twin:{record.project_id}:{record.version}", outcome="ok",
                detail={
                    "digest": record.digest, "elements": record.element_count,
                    "issues": len(record.issues), "labeling_coverage": round(record.labeling.coverage, 4),
                    **diff.summary(),
                },
            ))
        except Exception:  # pragma: no cover - defensive
            logger.debug("twin audit write failed", exc_info=True)
