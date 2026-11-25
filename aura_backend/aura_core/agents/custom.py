"""Turn a custom agent into the ``AgentSpec`` a turn runs under.

A custom agent (``aura_core/domain/custom_agents.py``) is a built-in persona plus what its owner
adds. This is the only place that join happens, and it is built to **narrow and never widen**:

* tools: the custom list is intersected with what the *resolved* base persona permits, at the
  moment of the turn. A tool the base never had cannot appear, and a tool later removed from the
  base disappears from every agent built on it. An explicit list also drops the base's prefix
  grants (the MCP bridge's ``mcp_*`` family): naming exactly which tools an agent has means it has
  exactly those.
* risk ceiling: the lower of the two, never higher.
* everything else (prompt template, required capabilities, memory scope, plan-loop budgets) is the
  base's, untouched.

The spec's id is ``custom:<agent id>``, so telemetry and run records say which agent ran and a
custom agent can never equal a built-in's id (which several code paths special-case).

The caller's own capabilities still bound the turn after this: ``AgentRegistry.resolve`` has
already fallen back to the orchestrator for a base persona the caller may not use, and the tool
registry intersects whatever grant results with what the caller's capabilities allow.
"""

from __future__ import annotations

from dataclasses import replace

from ..domain.custom_agents import CustomAgent, custom_spec_id
from ..domain.tools import ToolSensitivity
from .registry import AgentSpec

__all__ = ["apply_custom_agent"]


def apply_custom_agent(base: AgentSpec, custom: CustomAgent) -> AgentSpec:
    if custom.allowed_tools is None:
        tools, prefixes = base.allowed_tools, base.allowed_tool_prefixes
    else:
        tools = frozenset(name for name in custom.allowed_tools if base.permits_tool(name))
        prefixes = frozenset()

    risk = base.risk_ceiling
    if custom.risk_ceiling is not None:
        risk = min(base.risk_ceiling, ToolSensitivity[custom.risk_ceiling])

    return replace(
        base,
        id=custom_spec_id(custom.id),
        name=custom.name,
        allowed_tools=tools,
        allowed_tool_prefixes=prefixes,
        risk_ceiling=risk,
        extra_instruction=custom.instruction,
    )
