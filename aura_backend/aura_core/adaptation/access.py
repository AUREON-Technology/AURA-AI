"""Who may use which generated agent and tool (AP10, ADR-021 ``SERVICE_ACCOUNT``).

A service-account connector reads with one vaulted credential, so the source system cannot tell
its users apart: AURA is the enforcing authority. The source's own roles are discovered (each
role's grants become ``security.permission_map``), and the blueprint's ``role_bindings`` say which
AURA role acts as which source role. This module turns that into capability names the existing
enforcement already checks -- nothing here is a new enforcement point:

* a generated tool's ``required_capability`` (``ToolPolicy`` refuses the call, and the registry
  hides the declaration, without it);
* a generated agent's ``required_capabilities`` (``AgentRegistry.resolve`` falls back to the
  orchestrator without them).

With no bindings every name is plain ``project_data`` -- exactly the behaviour before this
module, so a blueprint approved without bindings means what it meant when it was approved. With
bindings, a tool that ``permission_map`` grants to some role needs ``project_data:<capability>``
and a role agent needs ``project_role:<role>``; a caller receives those names only through a
binding of their AURA role. A capability no discovered role governs (an API or file source has
no roles) stays at ``project_data``: role mapping restricts what the source restricts, no more.
"""

from __future__ import annotations

from typing import Mapping

from ..domain.blueprint import Blueprint, BlueprintAgent, BlueprintTool

__all__ = [
    "PROJECT_DATA_CAPABILITY",
    "PROJECT_ROLE_PREFIX",
    "PROJECT_CAPABILITY_PREFIX",
    "tool_capability",
    "agent_capabilities",
    "grants_by_aura_role",
    "all_grants",
    "expand_capabilities",
]

#: The grant every generated agent and tool requires (``AURA_PROJECT_DATA_ROLES``).
PROJECT_DATA_CAPABILITY = "project_data"
#: A caller's binding to one source-system role: ``project_role:<AUSM ROLE id>``.
PROJECT_ROLE_PREFIX = "project_role:"
#: One role-governed capability selection: ``project_data:<selection key>``.
PROJECT_CAPABILITY_PREFIX = "project_data:"


def _governed(blueprint: Blueprint) -> frozenset[str]:
    if not blueprint.security.role_bindings:
        return frozenset()
    return frozenset(g.capability for g in blueprint.security.permission_map)


def tool_capability(blueprint: Blueprint, tool: BlueprintTool) -> str:
    """The capability a caller needs to call ``tool``."""
    if tool.capability in _governed(blueprint):
        return f"{PROJECT_CAPABILITY_PREFIX}{tool.capability}"
    return PROJECT_DATA_CAPABILITY


def agent_capabilities(blueprint: Blueprint, agent: BlueprintAgent) -> frozenset[str]:
    """The capabilities a caller needs to be served by ``agent``."""
    if blueprint.security.role_bindings and agent.role is not None:
        return frozenset({PROJECT_DATA_CAPABILITY, f"{PROJECT_ROLE_PREFIX}{agent.role}"})
    return frozenset({PROJECT_DATA_CAPABILITY})


def _grants_for_roles(blueprint: Blueprint, roles: set[str]) -> frozenset[str]:
    return frozenset(
        {f"{PROJECT_ROLE_PREFIX}{r}" for r in roles}
        | {f"{PROJECT_CAPABILITY_PREFIX}{g.capability}" for g in blueprint.security.permission_map if g.role in roles}
    )


def grants_by_aura_role(blueprint: Blueprint) -> dict[str, frozenset[str]]:
    """AURA role -> the role-derived capabilities its holders receive under ``blueprint``."""
    roles: dict[str, set[str]] = {}
    for binding in blueprint.security.role_bindings:
        roles.setdefault(binding.aura_role, set()).add(binding.role)
    return {aura_role: _grants_for_roles(blueprint, bound) for aura_role, bound in roles.items()}


def all_grants(blueprint: Blueprint) -> tuple[str, ...]:
    """Every capability ``blueprint`` can grant: what the evaluation gate acts with, so it
    exercises each agent's own grant rather than one caller's."""
    roles = {g.role for g in blueprint.security.permission_map} | {a.role for a in blueprint.agents if a.role}
    granted = _grants_for_roles(blueprint, roles) if blueprint.security.role_bindings else frozenset()
    return (PROJECT_DATA_CAPABILITY, *sorted(granted))


def expand_capabilities(capabilities: tuple[str, ...], aura_role: str,
                        grants: Mapping[str, frozenset[str]]) -> tuple[str, ...]:
    """A caller's capabilities plus what their AURA role is bound to -- only for a caller who
    already holds ``project_data``, and never a role-derived name the caller sent themselves
    (capabilities come from the authenticator; this strips any look-alike before adding)."""
    own = tuple(c for c in capabilities
                if not c.startswith((PROJECT_ROLE_PREFIX, PROJECT_CAPABILITY_PREFIX)))
    if PROJECT_DATA_CAPABILITY not in own:
        return own
    extra = sorted(grants.get(aura_role, frozenset()))
    return (*own, *extra)
