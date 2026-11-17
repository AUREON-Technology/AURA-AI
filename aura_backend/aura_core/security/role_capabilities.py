"""The capabilities a person's role grants -- one rule, read by every place that needs it.

The gateway's authenticator (``aura_service._build_authenticator``) stamps these on every
:class:`Principal`, and the host-agent socket refuses a principal without ``engineer_agent``. The
pairing approval route (``api/v2/auth.py``) has only token claims, not a Principal, yet must refuse
exactly the accounts that socket would refuse (integration audit INT-003): a daemon paired to such
an account is closed with 4401 on connect and gives up for good. Both therefore read this function
instead of each keeping its own copy of the role lists.
"""

from __future__ import annotations

from typing import Any

#: Gates the `engineer` persona, every tool in tools/coding.py and the host-agent socket.
ENGINEER_AGENT = "engineer_agent"


def capabilities_for_role(security: Any, role: str) -> tuple[str, ...]:
    """The capability grants ``role`` carries under ``security`` (``SecuritySettings``)."""
    capabilities: list[str] = []
    if role in security.admin_roles:
        capabilities.append("admin.act")
    if role == "SuperAdmin":
        capabilities.append("admin.super")
    # Empty by default, so nobody has it until an operator explicitly opts a role in via
    # AURA_ENGINEER_AGENT_ROLES.
    if role in security.engineer_agent_roles:
        capabilities.append(ENGINEER_AGENT)
    # Gates agents and tools compiled from a deployed Project Blueprint (Track AP7) -- empty by
    # default; AURA_PROJECT_DATA_ROLES opts roles in.
    if role in security.project_data_roles:
        capabilities.append("project_data")
    return tuple(capabilities)
