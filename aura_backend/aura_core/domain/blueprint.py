"""The Project Blueprint: the compiled, approvable representation of one connected system.

A blueprint says what AURA may do on a project -- which connectors it reads, which AUSM elements
it models, which capabilities were selected and why, and the declarative tools, agents,
workflows, knowledge sources, UI manifest, policies and evaluation suites that follow from them.
It contains **no code**: every section is inert data the unchanged core executes (ADR-018).

Lifecycle (ADR-024): a :class:`BlueprintVersion` moves ``proposed -> evaluated -> approved ->
active -> superseded`` (or ``rejected``); a superseded version may become active again, which is
rollback. Every transition names the digest the caller acted on, and a mismatch is refused -- so a
blueprint edited after its approval cannot be deployed under that approval.

Invariants checked here are the ones that need nothing but the blueprint itself (unique ids, every
cross-section reference resolves, generated tools are read-only and namespaced, every agent has an
explicit tool grant). :func:`check_against_twin` adds the ones that need the Project Twin.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .ausm import PROJECT_ID_PATTERN, ElementKind, ProjectTwin
from .connectors import ConnectorKind, IdentityMode
from .errors import ConflictError, ValidationError
from .serialization import content_digest, from_data, json_schema, to_data
from .tools import ParameterSpec, ToolSensitivity
from .untrusted import UntrustedText

__all__ = [
    "BLUEPRINT_SCHEMA",
    "BlueprintProject",
    "ConnectorRef",
    "PermissionGrant",
    "SecuritySection",
    "CapabilitySelection",
    "BlueprintTool",
    "BlueprintAgent",
    "WorkflowKind",
    "Transition",
    "BlueprintWorkflow",
    "KnowledgeSource",
    "KnowledgeSection",
    "ViewType",
    "UiView",
    "NavItem",
    "UiManifest",
    "PolicySection",
    "EvaluationSection",
    "Blueprint",
    "BlueprintState",
    "BlueprintVersion",
    "blueprint_digest",
    "check_against_twin",
    "blueprint_json_schema",
    "generated_tool_name",
]

BLUEPRINT_SCHEMA = "aura.blueprint/v1"

_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CONNECTOR_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_PLAN_ITERATIONS = 8
#: The highest sensitivity a generated read tool may declare. SYSTEM is infrastructure
#: (terminal, database administration) and is never generated from a connected system.
_MAX_GENERATED_SENSITIVITY = ToolSensitivity.CONFIDENTIAL


def generated_tool_name(version: int, name: str) -> str:
    """The registry name of blueprint tool ``name`` in blueprint ``version``: ``bp<ver>_<name>``.

    Versioned so the next version's tools can be registered beside the current ones before the
    swap (ADR-024). Tool names are lower-case snake_case only (``ToolSpec``) and providers cap
    function names at 64 characters, which is why ``name`` is limited to 48.
    """
    return f"bp{version}_{name}"


def _slug(value: str, what: str) -> None:
    if not isinstance(value, str) or not _SLUG.match(value):
        raise ValidationError(f"{what} must be a lower-case slug of at most 48 chars", details={what: value})


def _non_empty(values: tuple[Any, ...], what: str) -> None:
    if not values:
        raise ValidationError(f"{what} must not be empty")


def _unique(values: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        (dupes if value in seen else seen).add(value)
    if dupes:
        raise ValidationError(f"duplicate {what}", details={"duplicates": sorted(dupes)})


def _sha256(value: str, what: str) -> None:
    if not _SHA256.match(value):
        raise ValidationError(f"{what} must be sha256:<64 hex>")


# ------------------------------------------------------------------------------------- sections


@dataclass(frozen=True, slots=True)
class BlueprintProject:
    """Which project this is, which blueprint version, and which twin it was compiled from."""

    id: str
    name: str
    version: int
    twin_version: int
    twin_digest: str

    def __post_init__(self) -> None:
        if not PROJECT_ID_PATTERN.match(self.id):
            raise ValidationError("project id must be a short slug", details={"id": self.id})
        if not self.name.strip() or len(self.name) > 120:
            raise ValidationError("project name must be 1-120 characters")
        if self.version < 1 or self.twin_version < 1:
            raise ValidationError("blueprint and twin versions start at 1")
        _sha256(self.twin_digest, "twin_digest")


@dataclass(frozen=True, slots=True)
class ConnectorRef:
    """A connector the blueprint reads through, and whose permissions apply (ADR-021)."""

    id: str
    kind: ConnectorKind
    identity_mode: IdentityMode

    def __post_init__(self) -> None:
        if not _CONNECTOR_ID.match(self.id):
            raise ValidationError("connector id must be a slug", details={"id": self.id})


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    """A source-system role (AUSM ROLE id) allowed to use one selected capability."""

    role: str
    capability: str


@dataclass(frozen=True, slots=True)
class SecuritySection:
    permission_map: tuple[PermissionGrant, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilitySelection:
    """One capability primitive applied to one AUSM element, with the evidence for it (ADR-023)."""

    id: str
    target: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _CAPABILITY_ID.match(self.id):
            raise ValidationError("capability id must be dotted, e.g. entity.search", details={"id": self.id})
        _non_empty(self.evidence, f"evidence for {self.id}")

    @property
    def key(self) -> str:
        return f"{self.id}@{self.target}"


@dataclass(frozen=True, slots=True)
class BlueprintTool:
    """A generated **read-only** tool (ADR-021, ADR-022).

    There is deliberately no ``side_effecting`` field: a v1 blueprint cannot declare a write.
    ``template`` names the compiled query template (AP6) the tool executes through
    ``connector``; the model never supplies query text.
    """

    name: str
    description: UntrustedText
    capability: str
    connector: str
    template: str
    output_schema: Mapping[str, Any]
    parameters: tuple[ParameterSpec, ...] = ()
    sensitivity: ToolSensitivity = ToolSensitivity.PUBLIC

    def __post_init__(self) -> None:
        _slug(self.name, "tool name")
        if not self.description:
            raise ValidationError("a tool needs a description; the model selects on it", details={"tool": self.name})
        _slug(self.template, "template id")
        if self.sensitivity > _MAX_GENERATED_SENSITIVITY:
            raise ValidationError(
                "generated tools cannot be SYSTEM-sensitive", details={"tool": self.name}
            )
        if not isinstance(self.output_schema, Mapping) or self.output_schema.get("type") not in {"object", "array"}:
            raise ValidationError(
                "output_schema must be a JSON Schema object or array", details={"tool": self.name}
            )
        _unique((p.name for p in self.parameters), f"parameters of {self.name}")
        object.__setattr__(self, "output_schema", MappingProxyType(dict(self.output_schema)))

    def output_fields(self) -> frozenset[str] | None:
        """Top-level (or per-item) property names the result exposes, when the schema says."""
        schema: Any = self.output_schema
        if schema.get("type") == "array":
            schema = schema.get("items") or {}
        props = schema.get("properties") if isinstance(schema, Mapping) else None
        return frozenset(props) if isinstance(props, Mapping) else None


@dataclass(frozen=True, slots=True)
class BlueprintAgent:
    """A composed agent: an explicit tool grant, a risk ceiling and the capabilities it serves.

    ``tools`` is never empty and never "everything" -- the ``allowed_tools=None`` shape of a core
    persona is not expressible for a generated agent (ADR-024).
    """

    id: str
    name: UntrustedText
    tools: tuple[str, ...]
    memory_scope: str
    capabilities: tuple[str, ...] = ()
    risk_ceiling: ToolSensitivity = ToolSensitivity.PUBLIC
    max_plan_iterations: int = 1
    tool_budget: int | None = None

    def __post_init__(self) -> None:
        _slug(self.id, "agent id")
        _slug(self.memory_scope, "memory scope")
        _non_empty(self.tools, f"tool grant of agent {self.id}")
        _unique(self.tools, f"tools of agent {self.id}")
        if not 1 <= self.max_plan_iterations <= _MAX_PLAN_ITERATIONS:
            raise ValidationError(f"max_plan_iterations must be 1..{_MAX_PLAN_ITERATIONS}", details={"agent": self.id})
        if self.tool_budget is not None and self.tool_budget < 1:
            raise ValidationError("tool_budget must be positive", details={"agent": self.id})


class WorkflowKind(str, enum.Enum):
    #: Reports where a record is in a process; reads only (AP11 runs it).
    READ_STATUS = "read_status"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Transition:
    from_state: str
    to_state: str


@dataclass(frozen=True, slots=True)
class BlueprintWorkflow:
    id: str
    kind: WorkflowKind
    entity: str
    state_attribute: str
    states: tuple[str, ...]
    evidence: tuple[str, ...]
    transitions: tuple[Transition, ...] = ()

    def __post_init__(self) -> None:
        _slug(self.id, "workflow id")
        if len(self.states) < 2:
            raise ValidationError("a workflow needs at least two states", details={"workflow": self.id})
        _unique(self.states, f"states of workflow {self.id}")
        _non_empty(self.evidence, f"evidence for workflow {self.id}")
        states = set(self.states)
        for t in self.transitions:
            if t.from_state not in states or t.to_state not in states:
                raise ValidationError("transition between unknown states", details={"workflow": self.id})


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    """A DOCUMENT element (or document collection) to index into project knowledge."""

    connector: str
    element: str


@dataclass(frozen=True, slots=True)
class KnowledgeSection:
    sources: tuple[KnowledgeSource, ...] = ()
    retrieval_strategy: str = "simple"

    def __post_init__(self) -> None:
        _slug(self.retrieval_strategy, "retrieval strategy")


class ViewType(str, enum.Enum):
    """The closed UI vocabulary (ADR-027). Clients render only these; nothing is executable."""

    ENTITY_LIST = "entity_list"
    ENTITY_DETAIL = "entity_detail"
    METRIC_CARD = "metric_card"
    TIMELINE = "timeline"
    SEARCH = "search"
    CHAT = "chat"
    APPROVAL_QUEUE = "approval_queue"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class UiView:
    id: str
    type: ViewType
    title: UntrustedText
    tool: str | None = None
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _slug(self.id, "view id")
        if self.type is ViewType.CHAT:
            if self.tool is not None or self.fields:
                raise ValidationError("a chat view binds no tool or fields", details={"view": self.id})
        elif self.tool is None:
            raise ValidationError("a data view must bind a tool", details={"view": self.id})
        _unique(self.fields, f"fields of view {self.id}")


@dataclass(frozen=True, slots=True)
class NavItem:
    id: str
    label: UntrustedText
    view: str

    def __post_init__(self) -> None:
        _slug(self.id, "navigation id")


@dataclass(frozen=True, slots=True)
class UiManifest:
    navigation: tuple[NavItem, ...] = ()
    views: tuple[UiView, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicySection:
    default_risk_ceiling: ToolSensitivity = ToolSensitivity.PUBLIC
    max_plan_iterations: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.max_plan_iterations <= _MAX_PLAN_ITERATIONS:
            raise ValidationError(f"max_plan_iterations must be 1..{_MAX_PLAN_ITERATIONS}")


@dataclass(frozen=True, slots=True)
class EvaluationSection:
    """Generated suites (AP6) and the minimum pass rate each must reach to deploy (AP8)."""

    suites: tuple[str, ...] = ()
    gate: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for suite in self.suites:
            _slug(suite, "suite name")
        _unique(self.suites, "evaluation suites")
        unknown = sorted(set(self.gate) - set(self.suites))
        if unknown:
            raise ValidationError("gate thresholds for unknown suites", details={"suites": unknown})
        for suite, threshold in self.gate.items():
            if not 0.0 <= threshold <= 1.0:
                raise ValidationError("gate thresholds are pass rates in [0, 1]", details={"suite": suite})
        object.__setattr__(self, "gate", MappingProxyType(dict(self.gate)))


# ------------------------------------------------------------------------------------ blueprint


@dataclass(frozen=True, slots=True)
class Blueprint:
    """One compiled blueprint. See the module docstring for what is and is not checked here."""

    project: BlueprintProject
    integrations: tuple[ConnectorRef, ...]
    schema: str = BLUEPRINT_SCHEMA
    data_model: tuple[str, ...] = ()
    security: SecuritySection = field(default_factory=SecuritySection)
    capabilities: tuple[CapabilitySelection, ...] = ()
    tools: tuple[BlueprintTool, ...] = ()
    native_tools: tuple[str, ...] = ()
    agents: tuple[BlueprintAgent, ...] = ()
    workflows: tuple[BlueprintWorkflow, ...] = ()
    knowledge: KnowledgeSection = field(default_factory=KnowledgeSection)
    ui: UiManifest = field(default_factory=UiManifest)
    policies: PolicySection = field(default_factory=PolicySection)
    evaluation: EvaluationSection = field(default_factory=EvaluationSection)

    def __post_init__(self) -> None:
        if self.schema != BLUEPRINT_SCHEMA:
            raise ValidationError(
                "unsupported blueprint schema", details={"schema": self.schema, "supported": BLUEPRINT_SCHEMA}
            )
        _non_empty(self.integrations, "integrations")
        _unique((c.id for c in self.integrations), "connector ids")
        if list(self.data_model) != sorted(set(self.data_model)):
            raise ValidationError("data_model must be sorted and unique")
        capability_keys = [c.key for c in self.capabilities]
        _unique(capability_keys, "capability selections")
        _unique((t.name for t in self.tools), "tool names")
        for name in self.native_tools:
            _slug(name, "native tool name")
        _unique(self.native_tools, "native tools")
        _unique((a.id for a in self.agents), "agent ids")
        _unique((w.id for w in self.workflows), "workflow ids")
        _unique((v.id for v in self.ui.views), "view ids")
        _unique((n.id for n in self.ui.navigation), "navigation ids")

        connectors = {c.id for c in self.integrations}
        capabilities = set(capability_keys)
        tools = {t.name: t for t in self.tools}
        clash = sorted(set(tools) & set(self.native_tools))
        if clash:
            raise ValidationError("generated and native tools share names", details={"names": clash})
        callable_tools = set(tools) | set(self.native_tools)

        for tool in self.tools:
            _ref(tool.connector in connectors, "tool connector", tool.name, tool.connector)
            _ref(tool.capability in capabilities, "tool capability", tool.name, tool.capability)
        for agent in self.agents:
            for name in agent.tools:
                _ref(name in callable_tools, "agent tool", agent.id, name)
                if name in tools and tools[name].sensitivity > agent.risk_ceiling:
                    raise ValidationError(
                        "an agent's risk ceiling is below one of its tools' sensitivity",
                        details={"agent": agent.id, "tool": name},
                    )
            for capability in agent.capabilities:
                _ref(capability in capabilities, "agent capability", agent.id, capability)
            if not agent.capabilities and any(name in tools for name in agent.tools):
                raise ValidationError(
                    "an agent using generated tools must name the capabilities it serves",
                    details={"agent": agent.id},
                )
        for grant in self.security.permission_map:
            _ref(grant.capability in capabilities, "permission capability", grant.role, grant.capability)
        for source in self.knowledge.sources:
            _ref(source.connector in connectors, "knowledge connector", source.element, source.connector)
        views = {v.id for v in self.ui.views}
        for view in self.ui.views:
            if view.tool is None:
                continue
            _ref(view.tool in callable_tools, "view tool", view.id, view.tool)
            exposed = tools[view.tool].output_fields() if view.tool in tools else None
            if exposed is not None:
                missing = sorted(set(view.fields) - exposed)
                if missing:
                    raise ValidationError(
                        "view binds fields the tool's output_schema does not expose",
                        details={"view": view.id, "fields": missing},
                    )
        for item in self.ui.navigation:
            _ref(item.view in views, "navigation view", item.id, item.view)

    @property
    def digest(self) -> str:
        return blueprint_digest(self)

    def tool_registry_names(self) -> Mapping[str, str]:
        """Blueprint tool name -> the namespaced name it is registered under (ADR-024)."""
        return {t.name: generated_tool_name(self.project.version, t.name) for t in self.tools}

    def elevated_agents(self) -> tuple[BlueprintAgent, ...]:
        """Agents whose ceiling is above PERSONAL; an approval request lists them separately."""
        return tuple(a for a in self.agents if a.risk_ceiling > ToolSensitivity.PERSONAL)

    def to_data(self) -> dict[str, Any]:
        return to_data(self)

    @classmethod
    def from_data(cls, data: Any) -> "Blueprint":
        return from_data(cls, data)


def _ref(ok: bool, what: str, owner: str, target: str) -> None:
    if not ok:
        raise ValidationError(f"{what} does not resolve", details={"owner": owner, "target": target})


def blueprint_digest(blueprint: Blueprint) -> str:
    """``sha256:<hex>`` over the blueprint's canonical JSON; what approvals bind to."""
    return content_digest(blueprint)


def check_against_twin(blueprint: Blueprint, twin: ProjectTwin) -> None:
    """Raise :class:`ValidationError` unless every AUSM reference resolves in ``twin``.

    Also requires the blueprint to name exactly this twin (project, version and digest): a
    blueprint compiled from an older twin must be recompiled, not re-pointed.
    """
    project = blueprint.project
    if (project.id, project.twin_version, project.twin_digest) != (twin.project_id, twin.version, twin.digest):
        raise ValidationError(
            "blueprint was compiled from a different twin",
            details={"blueprint_twin": project.twin_version, "twin": twin.version},
        )
    by_id = {e.id: e for e in twin.elements}

    def need(element: str, owner: str, kinds: frozenset[ElementKind] | None = None) -> None:
        found = by_id.get(element)
        if found is None:
            raise ValidationError("AUSM reference does not resolve", details={"owner": owner, "element": element})
        if kinds is not None and found.kind not in kinds:
            raise ValidationError(
                f"AUSM reference points at a {found.kind}",
                details={"owner": owner, "element": element, "expected": sorted(str(k) for k in kinds)},
            )

    sources = {e.source_id for e in twin.of_kind(ElementKind.SOURCE)}
    for connector in blueprint.integrations:
        if connector.id not in sources:
            raise ValidationError("connector has no SOURCE in the twin", details={"connector": connector.id})
    for element in blueprint.data_model:
        need(element, "data_model")
    for selection in blueprint.capabilities:
        need(selection.target, selection.key)
        for evidence in selection.evidence:
            need(evidence, selection.key)
    for grant in blueprint.security.permission_map:
        need(grant.role, grant.capability, frozenset({ElementKind.ROLE}))
    for workflow in blueprint.workflows:
        need(workflow.entity, workflow.id, frozenset({ElementKind.ENTITY}))
        need(workflow.state_attribute, workflow.id, frozenset({ElementKind.ATTRIBUTE}))
        for state in workflow.states:
            need(state, workflow.id, frozenset({ElementKind.STATE}))
        for evidence in workflow.evidence:
            need(evidence, workflow.id)
    for source in blueprint.knowledge.sources:
        need(source.element, source.connector, frozenset({ElementKind.DOCUMENT}))


def blueprint_json_schema() -> dict[str, Any]:
    """The published JSON Schema of :class:`Blueprint` (``docs/schemas/blueprint.v1.json``)."""
    return json_schema(
        Blueprint,
        schema_id="https://aura.local/schemas/blueprint.v1.json",
        title=f"AURA Project Blueprint ({BLUEPRINT_SCHEMA})",
    )


# ------------------------------------------------------------------------------------ lifecycle


class BlueprintState(str, enum.Enum):
    PROPOSED = "proposed"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"

    def __str__(self) -> str:
        return self.value


_TRANSITIONS: Mapping[BlueprintState, frozenset[BlueprintState]] = {
    BlueprintState.PROPOSED: frozenset({BlueprintState.EVALUATED, BlueprintState.REJECTED}),
    BlueprintState.EVALUATED: frozenset({BlueprintState.APPROVED, BlueprintState.REJECTED}),
    BlueprintState.APPROVED: frozenset({BlueprintState.ACTIVE, BlueprintState.REJECTED}),
    BlueprintState.ACTIVE: frozenset({BlueprintState.SUPERSEDED}),
    # Rollback: an earlier version becomes active again. Its approval still binds its digest.
    BlueprintState.SUPERSEDED: frozenset({BlueprintState.ACTIVE}),
    BlueprintState.REJECTED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class BlueprintVersion:
    """A blueprint plus where it is in its lifecycle. ``digest`` must match the blueprint."""

    blueprint: Blueprint
    state: BlueprintState = BlueprintState.PROPOSED
    digest: str = ""

    def __post_init__(self) -> None:
        actual = blueprint_digest(self.blueprint)
        if not self.digest:
            object.__setattr__(self, "digest", actual)
        elif self.digest != actual:
            raise ValidationError(
                "blueprint content does not match its recorded digest",
                details={"recorded": self.digest, "actual": actual},
            )

    def advance(self, to: BlueprintState, *, expected_digest: str) -> "BlueprintVersion":
        """Move to ``to``, refusing if the caller acted on a different digest (ADR-024)."""
        if expected_digest != self.digest:
            raise ConflictError(
                "blueprint changed since it was reviewed",
                details={"expected": expected_digest, "actual": self.digest},
            )
        if to not in _TRANSITIONS[self.state]:
            raise ConflictError(
                f"cannot move a {self.state} blueprint to {to}",
                details={"from": str(self.state), "to": str(to)},
            )
        return replace(self, state=to)
