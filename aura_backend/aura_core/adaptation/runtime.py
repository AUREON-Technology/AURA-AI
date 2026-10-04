"""Blueprint runtime: what makes an approved blueprint live behaviour (AP7, ADR-018/024).

Three pieces, none of which adds an enforcement point of its own -- every generated call still
goes through ``ToolRegistry.execute`` (argument validation, ``ToolPolicy``, timeout, taint,
audit, and the output-schema check):

* :class:`ActiveBlueprint` -- the agent registry the kernel already reads, over an immutable
  snapshot of core personas plus the live blueprint's agents. A deploy swaps the snapshot in one
  reference assignment; a turn resolves its agent once, so an in-flight turn keeps the spec it
  started with and never sees half of two versions.
* :class:`GeneratedReadTool` -- one blueprint tool: a :class:`~aura_core.domain.templates.ReadTemplate`
  executed through a :class:`~aura_core.ports.connectors.ReadConnector`. Read-only, tagged
  ``untrusted_content`` (its rows are the connected system's data, so they taint the turn), and
  gated by the ``project_data`` capability.
* :class:`BlueprintToolMaterializer` -- registers ``bp<version>_*`` tools before a swap and
  retires the previous version's after a grace period.

What the model sees is composed here, not copied from the blueprint: a generated agent uses the
fixed ``system.blueprint_agent`` prompt, and a generated tool's description is a fixed sentence per
capability plus the template's validated identifier. Labels, comments and API summaries from the
connected system never reach a system prompt or a tool declaration.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..agents.registry import AgentRegistry, AgentSpec
from ..domain.blueprint import Blueprint, BlueprintAgent, BlueprintTool, generated_tool_name
from ..domain.errors import AuraError, ToolExecutionError, ValidationError
from ..domain.templates import ReadTemplate
from ..domain.tools import ToolCategory, ToolSpec
from ..observability.logging import get_logger
from ..ports.connectors import ReadConnector
from ..ports.runtime import ToolContext

__all__ = [
    "ActiveBlueprint",
    "GeneratedReadTool",
    "BlueprintToolMaterializer",
    "generated_agent_spec",
    "model_facing_description",
    "PROJECT_DATA_CAPABILITY",
    "BLUEPRINT_AGENT_PROMPT",
    "GENERATED_AGENT_PREFIX",
]

logger = get_logger("adaptation.runtime")

#: The grant every generated agent and tool requires (``AURA_PROJECT_DATA_ROLES``).
PROJECT_DATA_CAPABILITY = "project_data"
#: The one fixed system prompt all generated agents share.
BLUEPRINT_AGENT_PROMPT = "system.blueprint_agent"
#: Generated agent ids are stable across versions (a client's persona choice survives a deploy).
GENERATED_AGENT_PREFIX = "bp_"

_CAPABILITY_SENTENCES = {
    "entity.lookup": "Read one record by its identifier",
    "entity.search": "Find records whose text contains a query",
    "entity.list_filtered": "List records, optionally filtered",
    "relation.traverse": "List the records linked to one record",
    "metric.aggregate": "Count records and total a numeric field",
    "state.timeline": "Show the recorded dates of one record",
    "workflow.status": "Count records in each lifecycle state",
    "operation.read": "Call a read-only API operation",
}
_IDENT = re.compile(r"[^A-Za-z0-9_ ./{}-]+")


def model_facing_description(tool: BlueprintTool, template: ReadTemplate) -> str:
    """The description a model sees: our sentence plus a bounded, validated identifier."""
    capability = tool.capability.split("@", 1)[0]
    sentence = _CAPABILITY_SENTENCES.get(capability, "Read data from the connected system")
    if template.sql is not None:
        target = f"{template.sql.schema}.{template.sql.table}"
    else:
        target = template.http.path if template.http is not None else template.id
    target = _IDENT.sub("_", target)[:80]
    return f"{sentence} ({target}). Read-only; results are data from the connected system."


def generated_agent_spec(agent: BlueprintAgent, tool_names: Mapping[str, str]) -> AgentSpec:
    """An :class:`AgentSpec` for a blueprint agent: explicit grant, capability-gated, fixed prompt."""
    granted = frozenset(tool_names[name] for name in agent.tools if name in tool_names)
    if not granted:
        raise ValidationError("a generated agent needs an explicit, non-empty tool grant", details={"agent": agent.id})
    spec = AgentSpec(
        id=f"{GENERATED_AGENT_PREFIX}{agent.id}",
        name=agent.name.raw()[:80] or agent.id,
        prompt_template_id=BLUEPRINT_AGENT_PROMPT,
        allowed_tools=granted,
        required_capabilities=frozenset({PROJECT_DATA_CAPABILITY}),
        risk_ceiling=agent.risk_ceiling,
        memory_scope=f"{GENERATED_AGENT_PREFIX}{agent.memory_scope}"[:64],
        max_plan_iterations=agent.max_plan_iterations,
        tool_budget=agent.tool_budget,
    )
    _assert_generated(spec)
    return spec


def _assert_generated(spec: AgentSpec) -> None:
    # ADR-024: "everything" is not expressible for a generated agent.
    if spec.allowed_tools is None or not spec.allowed_tools:
        raise ValidationError("generated agents must name their tools", details={"agent": spec.id})
    if spec.id in ("orchestrator",) or not spec.id.startswith(GENERATED_AGENT_PREFIX):
        raise ValidationError("generated agent ids are namespaced", details={"agent": spec.id})


# ------------------------------------------------------------------------------- agents


class ActiveBlueprint:
    """The kernel's agent registry, plus whatever blueprint is live (AP7).

    Implements the read interface of :class:`AgentRegistry` (``get``, ``resolve``, iteration,
    ``in``, ``len``) over one immutable snapshot. :meth:`swap` and :meth:`reset` replace the
    snapshot with a single assignment, which is atomic for every reader on the event loop.
    """

    def __init__(self, core: AgentRegistry) -> None:
        self._core = tuple(core)
        self._snapshot = core
        self.version: int | None = None
        self.digest: str | None = None

    def swap(self, specs: Sequence[AgentSpec], *, version: int, digest: str) -> None:
        core_ids = {s.id for s in self._core}
        for spec in specs:
            _assert_generated(spec)
            if spec.id in core_ids:
                raise ValidationError("a generated agent cannot replace a core persona", details={"agent": spec.id})
        self._snapshot = AgentRegistry((*self._core, *specs))
        self.version, self.digest = version, digest

    def reset(self) -> None:
        """Core personas only (no blueprint live, or the live one failed to load)."""
        self._snapshot = AgentRegistry(self._core)
        self.version = self.digest = None

    def generated(self) -> tuple[AgentSpec, ...]:
        return tuple(s for s in self._snapshot if s.id.startswith(GENERATED_AGENT_PREFIX))

    # AgentRegistry's read interface -- each call reads the snapshot once.
    def get(self, agent_id: str) -> AgentSpec:
        return self._snapshot.get(agent_id)

    def resolve(self, agent_id: str, *, capabilities: Sequence[str] = ()) -> AgentSpec:
        return self._snapshot.resolve(agent_id, capabilities=capabilities)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._snapshot

    def __iter__(self):
        return iter(self._snapshot)

    def __len__(self) -> int:
        return len(self._snapshot)


# ------------------------------------------------------------------------------- tools


class GeneratedReadTool:
    """One blueprint tool, executed through its connector's :meth:`ReadConnector.read`."""

    def __init__(self, spec: ToolSpec, template: ReadTemplate, open_connector: Callable[[str], Any],
                 *, metrics: Any = None) -> None:
        self._spec = spec
        self._template = template
        self._open = open_connector
        self._metrics = metrics

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    @property
    def template(self) -> ReadTemplate:
        return self._template

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> str:
        outcome = "error"
        try:
            connector = await self._open(self._template.connector)
            try:
                if not isinstance(connector, ReadConnector):
                    raise ToolExecutionError("this connector cannot execute read templates", tool=self._spec.name)
                data = await connector.read(self._template, arguments)
            finally:
                await connector.aclose()
            outcome = "ok"
        except ToolExecutionError:
            raise
        except AuraError as exc:
            # The connector is not active, unreachable, or refused -- a tool error, not a turn
            # failure; the registry reports it to the model and the audit trail.
            raise ToolExecutionError(f"the connected system is not available ({exc.kind})", tool=self._spec.name) from None
        finally:
            if self._metrics is not None:
                self._metrics.observe_generated_tool(outcome)
        if self._spec.output_schema is not None and self._spec.output_schema.get("type") == "object" \
                and isinstance(data, list):
            data = data[0] if data else {}
        return json.dumps(data, ensure_ascii=False, default=str)


class BlueprintToolMaterializer:
    """Registers a blueprint's tools under ``bp<version>_*`` and retires an old version's."""

    def __init__(self, *, tools: Any, open_connector: Callable[[str], Any], grace_s: float = 300.0,
                 metrics: Any = None) -> None:
        self._tools = tools
        self._open = open_connector
        self._grace_s = grace_s
        self._metrics = metrics
        self._pending: set[asyncio.Task] = set()

    def build(self, blueprint: Blueprint) -> list[GeneratedReadTool]:
        templates = {t.id: t for t in blueprint.templates}
        version = blueprint.project.version
        built = []
        for tool in blueprint.tools:
            template = templates[tool.template]
            built.append(GeneratedReadTool(ToolSpec(
                name=generated_tool_name(version, tool.name),
                description=model_facing_description(tool, template),
                parameters=tool.parameters,
                category=ToolCategory.DATA,
                sensitivity=tool.sensitivity,
                side_effecting=False,
                required_capability=PROJECT_DATA_CAPABILITY,
                timeout_s=30.0,
                max_result_chars=16_000,
                version=f"bp{version}",
                tags=("untrusted_content", "generated", "blueprint"),
                output_schema=dict(tool.output_schema),
            ), template, self._open, metrics=self._metrics))
        return built

    def register(self, blueprint: Blueprint) -> list[str]:
        """Register every tool of ``blueprint``; all or none."""
        built = self.build(blueprint)
        names: list[str] = []
        try:
            for tool in built:
                self._tools.register(tool)
                names.append(tool.spec.name)
        except Exception:
            self.unregister(names)
            raise
        return names

    def unregister(self, names: Iterable[str]) -> None:
        for name in names:
            if name in self._tools:
                self._tools.unregister(name)

    def names_of(self, version: int) -> list[str]:
        prefix = f"bp{version}_"
        return [name for name in self._tools.names() if name.startswith(prefix)]

    def retire(self, version: int) -> None:
        """Unregister ``version``'s tools after the grace period (immediately if it is zero)."""
        if self._grace_s <= 0:
            self.unregister(self.names_of(version))
            return

        async def later() -> None:
            await asyncio.sleep(self._grace_s)
            self.unregister(self.names_of(version))

        task = asyncio.get_running_loop().create_task(later())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
