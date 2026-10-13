"""Blueprint compiler (AP6, ADR-024): Project Twin + capability plan -> a proposed blueprint.

:func:`compile_blueprint` is pure and deterministic: the same twin, plan and connectors always
compile to the same blueprint (and digest). It runs the factories in dependency order -- tools
(each with its read template), then agents over those tools, workflows, the UI manifest and the
evaluation suites -- and finishes with the blueprint's own invariants plus
:func:`~aura_core.domain.blueprint.check_against_twin`, so every identifier a template names is
proven to be an element of the twin before anything is stored.

:class:`CompilerService` loads a stored twin, plans it, compiles, and appends the result as a
new ``proposed`` blueprint version. Nothing it produces is active: approval, evaluation and
deployment are AP7/AP8, and nothing auto-applies in v1 (ADR-024).
"""

from __future__ import annotations

import dataclasses
from datetime import timezone
from typing import Any, Sequence

from ..domain.ausm import ElementKind, ProjectTwin
from ..domain.blueprint import (
    PROJECT_KNOWLEDGE_TOOL,
    Blueprint,
    BlueprintProject,
    BlueprintState,
    CapabilitySelection,
    ConnectorRef,
    EvaluationSection,
    KnowledgeSection,
    KnowledgeSource,
    PermissionGrant,
    PolicySection,
    SecuritySection,
    blueprint_digest,
    check_against_twin,
)
from ..domain.capabilities import CapabilityPlan
from ..domain.compilation import BlueprintRecord, DroppedItem, EvalSuiteSpec
from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..domain.tools import ToolSensitivity
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent
from .factories.agents import AGENT_MAX_PLAN_ITERATIONS, build_agents
from .factories.common import TwinView
from .factories.evals import SUITE_GATES, build_suites
from .factories.tools import CompiledTool, build_tool
from .factories.ui import build_ui
from .factories.workflows import build_workflows

__all__ = ["compile_blueprint", "Compiled", "CompilerService"]

logger = get_logger("adaptation.compiler")


class Compiled:
    """A compiled blueprint with its suites and everything dropped along the way."""

    __slots__ = ("blueprint", "suites", "dropped")

    def __init__(self, blueprint: Blueprint, suites: tuple[EvalSuiteSpec, ...], dropped: tuple[DroppedItem, ...]) -> None:
        self.blueprint = blueprint
        self.suites = suites
        self.dropped = dropped


def compile_blueprint(
    twin: ProjectTwin,
    plan: CapabilityPlan,
    *,
    connectors: Sequence[ConnectorRef],
    version: int,
    name: str = "Connected system",
) -> Compiled:
    if (plan.project_id, plan.twin_version, plan.twin_digest) != (twin.project_id, twin.version, twin.digest):
        raise ValidationError("the plan was made from a different twin", details={"plan_twin": plan.twin_version})
    sources = sorted(e.source_id for e in twin.of_kind(ElementKind.SOURCE))
    refs = {c.id: c for c in connectors}
    missing = [s for s in sources if s not in refs]
    if missing:
        raise ValidationError("every twin source needs its connector", details={"missing": missing})
    view = TwinView(twin, {s: refs[s].kind for s in sources})

    dropped: list[DroppedItem] = []
    tools: list[CompiledTool] = []
    # Reserved: a table called "project" must not compile a tool that shadows the knowledge tool.
    taken: set[str] = {PROJECT_KNOWLEDGE_TOOL}
    for selection in plan.selected:
        result = build_tool(selection, view, taken)
        if isinstance(result, str):
            dropped.append(DroppedItem("capability", selection.key, result))
        else:
            tools.append(result)

    agents, agent_drops = build_agents(plan.agents, tools)
    # AP10: every DOCUMENT in the twin is a knowledge source, and every agent may search them --
    # through one native tool whose grant is written into the blueprint, so the approval covers it.
    knowledge_sources = tuple(KnowledgeSource(e.source_id, e.id)
                              for e in sorted(twin.of_kind(ElementKind.DOCUMENT), key=lambda e: e.id))
    native_tools: tuple[str, ...] = ()
    if knowledge_sources:
        native_tools = (PROJECT_KNOWLEDGE_TOOL,)
        agents = [dataclasses.replace(a, tools=a.tools + (PROJECT_KNOWLEDGE_TOOL,)) for a in agents]
    workflows, workflow_drops = build_workflows(tools, view)
    ui = build_ui(tools, view)
    suites = build_suites(tools, agents)
    dropped.extend(agent_drops)
    dropped.extend(workflow_drops)

    compiled_keys = {t.selection.key for t in tools}
    selections = [s for s in plan.selected if s.key in compiled_keys]
    capabilities = tuple(CapabilitySelection(s.capability, s.target, s.evidence) for s in selections)
    grants = tuple(
        PermissionGrant(role, s.key) for s in selections for role in s.roles
    )
    data_model = sorted({element for s in selections for element in s.evidence
                         if view.get(element) is not None
                         and view.get(element).kind in (ElementKind.ENTITY, ElementKind.ATTRIBUTE)})
    blueprint = Blueprint(
        project=BlueprintProject(twin.project_id, name, version, twin.version, twin.digest),
        integrations=tuple(refs[s] for s in sources),
        data_model=tuple(data_model),
        security=SecuritySection(grants),
        capabilities=capabilities,
        templates=tuple(t.template for t in tools),
        tools=tuple(t.tool for t in tools),
        native_tools=native_tools,
        agents=tuple(agents),
        workflows=tuple(workflows),
        knowledge=KnowledgeSection(knowledge_sources),
        ui=ui,
        policies=PolicySection(default_risk_ceiling=ToolSensitivity.PERSONAL,
                               max_plan_iterations=AGENT_MAX_PLAN_ITERATIONS),
        evaluation=EvaluationSection(
            tuple(s.name for s in suites), {s.name: SUITE_GATES[s.name] for s in suites},
        ),
    )
    check_against_twin(blueprint, twin)
    return Compiled(blueprint, tuple(suites), tuple(sorted(dropped, key=lambda d: (d.kind, d.key))))


# ------------------------------------------------------------------------------- service


class CompilerService:
    """Plan and compile a stored twin version into a new ``proposed`` blueprint version."""

    def __init__(self, *, twins: Any, planner: Any, connectors: Any, store: Any, clock: Any,
                 name: str = "Connected system", audit: Any = None, metrics: Any = None) -> None:
        self._twins = twins
        self._planner = planner
        self._connectors = connectors
        self._store = store
        self._clock = clock
        self._name = name
        self._audit = audit
        self._metrics = metrics

    async def compile(self, twin_version: int, *, by: str) -> BlueprintRecord:
        found = await self._twins.get(twin_version)
        if found is None:
            raise NotFoundError("twin version not found", details={"version": twin_version})
        twin = found[1]
        plan = await self._planner.plan(twin_version)
        refs = []
        for record in await self._connectors.list():
            d = record.descriptor
            refs.append(ConnectorRef(d.id, d.kind, d.identity_mode))
        latest = await self._store.latest(twin.project_id)
        version = (latest.version + 1) if latest else 1
        compiled = compile_blueprint(twin, plan, connectors=refs, version=version, name=self._name)
        record = BlueprintRecord(
            project_id=twin.project_id, version=version, state=BlueprintState.PROPOSED,
            digest=blueprint_digest(compiled.blueprint),
            created_at=self._clock.now().astimezone(timezone.utc).isoformat(), created_by=by,
            plan_digest=plan.digest, registry_digest=plan.registry_digest,
            blueprint=compiled.blueprint, suites=compiled.suites, dropped=compiled.dropped,
        )
        try:
            await self._store.append(record)
        except ConflictError:
            raise ConflictError("another blueprint was compiled concurrently; retry",
                                details={"version": version}) from None
        if self._metrics is not None:
            self._metrics.observe_blueprint(record.summary())
        await self._record(record)
        return record

    async def versions(self, project_id: str, *, limit: int = 50):
        return await self._store.versions(project_id, limit=limit)

    async def get(self, project_id: str, version: int):
        return await self._store.get(project_id, version)

    async def _record(self, record: BlueprintRecord) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.record(AuditEvent(
                action="adaptation.blueprint.compile", actor=record.created_by,
                resource=f"blueprint:{record.project_id}:{record.version}", outcome="proposed",
                detail=record.summary(),
            ))
        except Exception:  # pragma: no cover - defensive
            logger.debug("blueprint audit write failed", exc_info=True)
