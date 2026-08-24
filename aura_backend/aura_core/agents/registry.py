"""Agent registry (Phase C1, ADR-006/ADR-002 compatible).

Before this, an "agent" was a key into :data:`aura_core.prompts.library.AGENT_PROMPT_IDS` -- a
system prompt, nothing else. Every persona shared the exact same tool universe, gated only by the
*caller's* capability grants; ``finance_advisor`` could invoke ``propose_health_log`` and
``health_companion`` could invoke ``propose_expense``, because nothing ever checked which persona
was asking. "Agents are prompts, not agents" was true by inspection, not metaphor.

:class:`AgentSpec` makes an agent's tool set, required capabilities, risk ceiling, and memory
scope *data*, not code -- adding an agent, or narrowing one, is an entry in
:func:`build_default_agent_registry`, not a new branch anywhere. Enforcement reuses the tool
registry's own machinery rather than inventing a second permission model: ``allowed_tools`` and
``risk_ceiling`` ride through :class:`~aura_core.ports.runtime.ToolContext` into
:meth:`ToolPolicy.evaluate`, which already audits every denial through the same
:class:`~aura_core.ports.runtime.AuditSink` every other policy refusal uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..domain.tools import ToolSensitivity
from ..prompts.library import AGENT_PROMPT_IDS

__all__ = ["AgentSpec", "AgentRegistry", "build_default_agent_registry"]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One agent persona's identity, prompt, and grant.

    ``allowed_tools=None`` means "no restriction beyond the caller's own capabilities" -- the
    orchestrator's grant, and the correct default for a general-purpose persona that should not
    be narrower than what the user themselves is allowed to do. Every other persona names an
    explicit set: a tool absent from it is refused even if the caller could otherwise use it.
    """

    id: str
    name: str
    prompt_template_id: str
    allowed_tools: frozenset[str] | None = None
    required_capabilities: frozenset[str] = frozenset()
    risk_ceiling: ToolSensitivity = ToolSensitivity.SYSTEM
    memory_scope: str = "shared"


class AgentRegistry:
    """Resolves an agent id to its :class:`AgentSpec`, data rather than an if/elif chain."""

    def __init__(self, agents: Iterable[AgentSpec]) -> None:
        by_id = {spec.id: spec for spec in agents}
        if "orchestrator" not in by_id:
            raise ValueError(
                "AgentRegistry requires an 'orchestrator' entry: every persona's "
                "required-capability fallback and every unknown agent id resolve to it"
            )
        self._by_id = by_id

    def get(self, agent_id: str) -> AgentSpec:
        """The named agent, or the orchestrator if the id is unknown."""
        return self._by_id.get(agent_id, self._by_id["orchestrator"])

    def resolve(self, agent_id: str, *, capabilities: Sequence[str] = ()) -> AgentSpec:
        """The named agent, or the orchestrator if unknown *or* under-privileged.

        A persona's ``required_capabilities`` gate whether the caller may use it at all, not just
        which tools it may then call -- a caller without the grant falls back to the
        orchestrator's own (equal-or-narrower) grant rather than being refused outright, since the
        turn itself is still answerable, just not by that persona.
        """
        spec = self.get(agent_id)
        granted = frozenset(capabilities)
        if spec.required_capabilities and not spec.required_capabilities.issubset(granted):
            return self._by_id["orchestrator"]
        return spec

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._by_id

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)


# Tool names read/write against `aura_core/tools/{builtin,personal_os}.py`'s current registrations.
# A persona naming a tool that is not actually configured in a given deployment (e.g. fetch_url,
# which needs AURA_HTTP_ALLOWED_HOSTS) simply never has it offered -- ToolRegistry.available()
# intersects an agent's declarations against what is actually registered.
_READ_TOOLS = frozenset({"current_datetime", "search_knowledge", "recall_memory", "remember_fact"})


def build_default_agent_registry() -> AgentRegistry:
    """The seven personas :data:`AGENT_PROMPT_IDS` used to define, now with real grants.

    Prompt template ids are read from ``AGENT_PROMPT_IDS`` rather than repeated here, so which
    template a persona renders has one source of truth; only the grant (tools, risk, capability,
    memory scope) is new. Risk ceilings and tool sets are drawn conservatively from what each
    persona's prompt already claims to do -- narrowing later, as more tools exist, is the expected
    direction; broadening a persona's grant is a decision to make deliberately, not a default to
    fall into.
    """
    return AgentRegistry(
        [
            AgentSpec(
                id="orchestrator", name="Orchestrator",
                prompt_template_id=AGENT_PROMPT_IDS["orchestrator"],
                allowed_tools=None, risk_ceiling=ToolSensitivity.SYSTEM, memory_scope="shared",
            ),
            AgentSpec(
                id="task_planner", name="Task Planner",
                prompt_template_id=AGENT_PROMPT_IDS["task_planner"],
                # propose_* for a client-confirmed write, create_*/list_* (Phase C2) for one this
                # persona is trusted to make directly -- both reach real storage, the difference
                # is only whether a human approves first.
                allowed_tools=_READ_TOOLS | {
                    "propose_task", "propose_event",
                    "create_task", "list_tasks", "create_calendar_event", "list_calendar_events",
                    "prioritize_tasks", "find_calendar_slots",
                },
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
            AgentSpec(
                id="finance_advisor", name="Finance Advisor",
                prompt_template_id=AGENT_PROMPT_IDS["finance_advisor"],
                allowed_tools=_READ_TOOLS | {
                    "calculator", "propose_expense", "propose_income",
                    "create_finance_record", "list_finance_records",
                },
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
            AgentSpec(
                id="health_companion", name="Health Companion",
                prompt_template_id=AGENT_PROMPT_IDS["health_companion"],
                allowed_tools=_READ_TOOLS | {
                    "propose_health_log", "create_health_record", "list_health_records",
                },
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
            AgentSpec(
                id="research_agent", name="Research Agent",
                prompt_template_id=AGENT_PROMPT_IDS["research_agent"],
                # fetch_url is CONFIDENTIAL (reaches the network) and opt-in server-side; naming
                # it here only matters once a deployment configures AURA_HTTP_ALLOWED_HOSTS.
                allowed_tools=_READ_TOOLS | {"calculator", "fetch_url"},
                risk_ceiling=ToolSensitivity.CONFIDENTIAL, memory_scope="shared",
            ),
            AgentSpec(
                id="coding_agent", name="Coding Agent",
                prompt_template_id=AGENT_PROMPT_IDS["coding_agent"],
                allowed_tools=_READ_TOOLS | {"calculator"},
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
            AgentSpec(
                id="decision_agent", name="Decision Agent",
                prompt_template_id=AGENT_PROMPT_IDS["decision_agent"],
                # Read-only and deliberately so: a decision-support persona that can also file a
                # side-effecting propose_* call is a persona that can act on its own advice
                # before a human has weighed in.
                allowed_tools=_READ_TOOLS | {"calculator"},
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
        ]
    )
