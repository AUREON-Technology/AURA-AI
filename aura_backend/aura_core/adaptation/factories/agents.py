"""Agent factory: the planner's agent proposals -> blueprint agents with explicit tool grants."""

from __future__ import annotations

from ...domain.blueprint import BlueprintAgent
from ...domain.capabilities import AgentProposal
from ...domain.compilation import DroppedItem
from ...domain.tools import ToolSensitivity
from .common import slug, unique_slug
from .tools import CompiledTool

__all__ = ["build_agents", "AGENT_MAX_PLAN_ITERATIONS", "AGENT_TOOL_BUDGET"]

AGENT_MAX_PLAN_ITERATIONS = 2
AGENT_TOOL_BUDGET = 8


def build_agents(proposals: tuple[AgentProposal, ...], tools: list[CompiledTool]) -> tuple[list[BlueprintAgent], list[DroppedItem]]:
    """One agent per proposal that kept at least one compiled tool. The grant is always the exact
    list of its tools -- a generated agent can never be "all tools" (ADR-024)."""
    by_key: dict[str, list[CompiledTool]] = {}
    for compiled in tools:
        by_key.setdefault(compiled.selection.key, []).append(compiled)
    agents: list[BlueprintAgent] = []
    dropped: list[DroppedItem] = []
    taken: set[str] = set()
    for proposal in proposals:
        chosen = [c for key in proposal.capabilities for c in by_key.get(key, [])]
        if not chosen:
            dropped.append(DroppedItem("agent", proposal.id, "no_compiled_tools"))
            continue
        agent_id = unique_slug(slug(proposal.id.removeprefix("agent.")), taken)
        agents.append(BlueprintAgent(
            id=agent_id, name=proposal.label,
            tools=tuple(dict.fromkeys(c.tool.name for c in chosen)),
            memory_scope=agent_id,
            capabilities=tuple(dict.fromkeys(c.selection.key for c in chosen)),
            risk_ceiling=max(max(c.tool.sensitivity for c in chosen), ToolSensitivity.PERSONAL),
            max_plan_iterations=AGENT_MAX_PLAN_ITERATIONS,
            tool_budget=AGENT_TOOL_BUDGET,
            role=proposal.role,
        ))
    return agents, dropped
