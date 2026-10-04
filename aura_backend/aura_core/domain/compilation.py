"""What the blueprint compiler produces besides the blueprint itself (AP6, ADR-024).

* :class:`EvalSuiteSpec` / :class:`EvalCaseSpec` -- generated evaluation cases, as data. AP8 runs
  them through the existing harness (``evaluation/harness.py``, ``evaluation/agent_runs.py``)
  against a replica of the connected system; here they are only declared, so they can be
  reviewed and diffed with the blueprint they gate.
* :class:`DroppedItem` -- every capability, agent, workflow or view the compiler could *not*
  produce, with a reason code. A capability whose predicates were met but that has no safe
  compilation (a composite key, an API with no matching read operation) is dropped and named,
  never stubbed.
* :class:`BlueprintRecord` -- one stored blueprint version with its suites, drops and
  provenance (which twin, which plan, which capability registry).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .ausm import PROJECT_ID_PATTERN
from .blueprint import Blueprint, BlueprintState, blueprint_digest
from .errors import ConflictError, ValidationError
from .untrusted import UntrustedText

__all__ = [
    "EvalCaseKind", "EvalCaseSpec", "EvalSuiteSpec", "DroppedItem", "BlueprintRecord", "ActivePointer",
    "plan_activation",
]

_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CASE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]*(:[a-z0-9_.]+)?$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

#: The closed set of generated case kinds (ADR-024): what AP8's gate knows how to run.
EvalCaseKind = ("tool_contract", "permission_denied", "out_of_scope", "injection_refusal")


@dataclass(frozen=True, slots=True)
class EvalCaseSpec:
    """One generated case. ``query`` may quote the connected system's own labels, so it is
    untrusted text -- a test input, never an instruction to the evaluator."""

    id: str
    kind: str
    agent: str
    query: UntrustedText
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    expected_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    should_refuse: bool = False
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _CASE_ID.match(self.id):
            raise ValidationError("case ids are dotted slugs", details={"id": self.id})
        if self.kind not in EvalCaseKind:
            raise ValidationError("unknown case kind", details={"kind": self.kind})
        if not _SLUG.match(self.agent):
            raise ValidationError("case agent must be an agent id", details={"id": self.id})
        if self.kind == "tool_contract" and self.tool is None:
            raise ValidationError("a tool_contract case names its tool", details={"id": self.id})
        if set(self.expected_tools) & set(self.forbidden_tools):
            raise ValidationError("a tool cannot be both expected and forbidden", details={"id": self.id})
        for key, value in self.arguments.items():
            if not (value is None or isinstance(value, (str, int, float, bool))):
                raise ValidationError("case arguments are scalars", details={"id": self.id, "key": key})


@dataclass(frozen=True, slots=True)
class EvalSuiteSpec:
    name: str
    description: str
    cases: tuple[EvalCaseSpec, ...]

    def __post_init__(self) -> None:
        if not _SLUG.match(self.name):
            raise ValidationError("suite names are slugs", details={"name": self.name})
        ids = [c.id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise ValidationError("duplicate case ids", details={"suite": self.name})


@dataclass(frozen=True, slots=True)
class DroppedItem:
    """Something the compiler did not produce. ``key`` is the selection key, agent id, etc."""

    kind: str
    key: str
    reason: str

    def __post_init__(self) -> None:
        if self.kind not in ("capability", "agent", "workflow", "view"):
            raise ValidationError("unknown dropped-item kind", details={"kind": self.kind})
        if not _CODE.match(self.reason):
            raise ValidationError("drop reasons are codes", details={"reason": self.reason})


@dataclass(frozen=True, slots=True)
class BlueprintRecord:
    """One stored blueprint version. ``digest`` binds ``blueprint`` (what approvals cite);
    ``created_at`` is ISO-8601 UTC text."""

    project_id: str
    version: int
    state: BlueprintState
    digest: str
    created_at: str
    created_by: str
    plan_digest: str
    registry_digest: str
    blueprint: Blueprint
    suites: tuple[EvalSuiteSpec, ...] = ()
    dropped: tuple[DroppedItem, ...] = ()
    #: How the version passed (or skipped) its evaluation gate, e.g. ``ap8`` or ``ungated``.
    gate: str | None = None
    #: The ``BLUEPRINT_DEPLOYMENT`` approval that bound this digest (AP7). Kept after it goes live,
    #: so a rollback to this version is still traceable to the decision that allowed it.
    approval_id: str | None = None
    activated_at: str | None = None
    activated_by: str | None = None

    def __post_init__(self) -> None:
        if not PROJECT_ID_PATTERN.match(self.project_id):
            raise ValidationError("project_id must be a short slug")
        if self.blueprint.project.id != self.project_id or self.blueprint.project.version != self.version:
            raise ValidationError("record and blueprint disagree on project or version")
        if self.digest != blueprint_digest(self.blueprint):
            raise ValidationError("blueprint content does not match its recorded digest")
        for digest in (self.plan_digest, self.registry_digest):
            if not _SHA256.match(digest):
                raise ValidationError("digests are sha256:<64 hex>")
        if not self.created_by or len(self.created_by) > 256:
            raise ValidationError("created_by must be 1-256 characters")
        past_evaluation = {BlueprintState.EVALUATED, BlueprintState.APPROVED, BlueprintState.ACTIVE,
                           BlueprintState.SUPERSEDED}
        if self.state in past_evaluation and not self.gate:
            raise ValidationError("an evaluated blueprint records how it passed its gate")
        if self.state in past_evaluation - {BlueprintState.EVALUATED} and not self.approval_id:
            raise ValidationError("an approved blueprint records its approval")
        if self.state in (BlueprintState.ACTIVE, BlueprintState.SUPERSEDED) and not self.activated_at:
            raise ValidationError("a blueprint that went live records when")
        declared = set(self.blueprint.evaluation.suites)
        if {s.name for s in self.suites} != declared:
            raise ValidationError("stored suites must be exactly the suites the blueprint declares")

    def summary(self) -> dict[str, Any]:
        bp = self.blueprint
        return {
            "version": self.version, "state": self.state.value, "digest": self.digest,
            "gate": self.gate, "approval_id": self.approval_id, "activated_at": self.activated_at,
            "twin_version": bp.project.twin_version,
            "capabilities": len(bp.capabilities), "templates": len(bp.templates), "tools": len(bp.tools),
            "agents": len(bp.agents), "workflows": len(bp.workflows), "views": len(bp.ui.views),
            "eval_cases": sum(len(s.cases) for s in self.suites), "dropped": len(self.dropped),
        }


@dataclass(frozen=True, slots=True)
class ActivePointer:
    """Which blueprint version is live for a project (one row, updated atomically -- AP7).

    ``previous_version`` is what a plain rollback returns to. ``activated_at`` is ISO-8601 UTC.
    """

    project_id: str
    version: int
    digest: str
    activated_at: str
    activated_by: str
    previous_version: int | None = None

    def __post_init__(self) -> None:
        if not PROJECT_ID_PATTERN.match(self.project_id):
            raise ValidationError("project_id must be a short slug")
        if self.version < 1 or (self.previous_version is not None and self.previous_version < 1):
            raise ValidationError("versions start at 1")
        if self.previous_version == self.version:
            raise ValidationError("a version cannot be its own predecessor")
        if not _SHA256.match(self.digest):
            raise ValidationError("digest must be sha256:<64 hex>")


def plan_activation(
    target: BlueprintRecord, current: BlueprintRecord | None, *, by: str, at: str
) -> tuple[BlueprintRecord, BlueprintRecord | None, ActivePointer]:
    """The three writes of one activation, computed before any of them happens.

    ``target`` must be ``approved`` (a deploy) or ``superseded`` (a rollback); ``current`` is the
    version the pointer names now, if any. Both stores apply the result in one atomic step.
    """
    if target.state not in (BlueprintState.APPROVED, BlueprintState.SUPERSEDED):
        raise ConflictError(
            f"blueprint version {target.version} is {target.state.value}; only an approved or "
            "superseded version can go live", details={"state": target.state.value},
        )
    if current is not None and current.version == target.version:
        raise ConflictError("that blueprint version is already active", details={"version": target.version})
    live = replace(target, state=BlueprintState.ACTIVE, activated_at=at, activated_by=by)
    retired = replace(current, state=BlueprintState.SUPERSEDED) if current is not None else None
    pointer = ActivePointer(
        project_id=target.project_id, version=target.version, digest=target.digest, activated_at=at,
        activated_by=by, previous_version=current.version if current is not None else None,
    )
    return live, retired, pointer
