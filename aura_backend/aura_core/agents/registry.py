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

from ..domain.agent_runs import AgentBudget
from ..domain.memory import SHARED_MEMORY_SCOPE
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
    #: Which memory partition this persona reads and writes (``domain/memory.py``). A persona
    #: with its own scope writes there and reads its own scope *plus* the shared one; a persona
    #: on ``"shared"`` reads and writes only the shared scope, so it never sees another
    #: persona's private memories. Isolation is per ``(user, scope)`` -- the owner check that
    #: already separated users is unchanged.
    memory_scope: str = SHARED_MEMORY_SCOPE
    #: How many plan-execute-observe-replan iterations this persona's turn may run
    #: (``AIKernel``/``AgenticExecutor``). ``1`` -- the default, and every persona below except
    #: ``engineer`` -- preserves the pre-agentic-loop behaviour exactly: plan once, execute once.
    #: ``> 1`` enables the replanning loop, with this many replans allowed after the first pass
    #: (the meaning ``PlanningEngine.can_replan(limit=...)`` has always given it).
    max_plan_iterations: int = 1
    #: The loop's other four bounds (GAP-024), as data. ``None`` means "not bounded by this
    #: budget" -- the execution engine's per-pass step cap and the loop deadline still apply.
    #: ``time_budget_s`` never loosens ``settings.reasoning.engineer_loop_deadline_s``.
    token_budget: int | None = None
    time_budget_s: float | None = None
    tool_budget: int | None = None
    risk_budget: int | None = None

    @property
    def loop_enabled(self) -> bool:
        """Whether this persona may replan (the multi-iteration agent loop)."""
        return self.max_plan_iterations > 1

    @property
    def budget(self) -> AgentBudget:
        """The persona's run budget. A single-pass persona gets ``max_iterations=0``: it runs
        through the same loop, and the loop makes exactly one pass."""
        return AgentBudget(
            max_iterations=self.max_plan_iterations if self.loop_enabled else 0,
            token_budget=self.token_budget,
            time_budget_s=self.time_budget_s,
            tool_budget=self.tool_budget,
            risk_budget=self.risk_budget,
        )


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
_READ_TOOLS = frozenset({"current_datetime", "search_knowledge", "recall_memory"})

# `remember_fact` writes a durable memory record (tools/builtin.py) -- a side effect, not a read.
# It used to sit in _READ_TOOLS, which handed it to every persona including the deliberately
# read-only `decision_agent` (audit FSA-017). Granted explicitly below to the personas that act
# on the user's behalf; absent from the ones whose whole point is to not act.
_MEMORY_WRITE_TOOLS = frozenset({"remember_fact"})


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
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {
                    "propose_task", "propose_event",
                    "create_task", "list_tasks", "update_task", "delete_task",
                    "create_calendar_event", "list_calendar_events",
                    "update_calendar_event", "delete_calendar_event",
                    "prioritize_tasks", "find_calendar_slots",
                },
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
            AgentSpec(
                id="finance_advisor", name="Finance Advisor",
                prompt_template_id=AGENT_PROMPT_IDS["finance_advisor"],
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {
                    "calculator", "propose_expense", "propose_income",
                    "create_finance_record", "list_finance_records",
                    "update_finance_record", "delete_finance_record",
                },
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="finance",
            ),
            AgentSpec(
                id="health_companion", name="Health Companion",
                prompt_template_id=AGENT_PROMPT_IDS["health_companion"],
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {
                    "propose_health_log", "create_health_record", "list_health_records",
                    "update_health_record", "delete_health_record",
                },
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="health",
            ),
            AgentSpec(
                id="research_agent", name="Research Agent",
                prompt_template_id=AGENT_PROMPT_IDS["research_agent"],
                # fetch_url is CONFIDENTIAL (reaches the network) and opt-in server-side; naming
                # it here only matters once a deployment configures AURA_HTTP_ALLOWED_HOSTS.
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {"calculator", "fetch_url"},
                risk_ceiling=ToolSensitivity.CONFIDENTIAL, memory_scope="shared",
            ),
            AgentSpec(
                id="coding_agent", name="Coding Agent",
                prompt_template_id=AGENT_PROMPT_IDS["coding_agent"],
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {"calculator"},
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
            AgentSpec(
                id="decision_agent", name="Decision Agent",
                prompt_template_id=AGENT_PROMPT_IDS["decision_agent"],
                # Read-only and deliberately so: a decision-support persona that can also file a
                # side-effecting propose_* call is a persona that can act on its own advice
                # before a human has weighed in. That includes `remember_fact`: a durable write
                # is a side effect, however small (audit FSA-017).
                allowed_tools=_READ_TOOLS | {"calculator"},
                risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="shared",
            ),
            AgentSpec(
                id="engineer", name="Engineer",
                prompt_template_id=AGENT_PROMPT_IDS["engineer"],
                # Sandboxed filesystem/command/git tools and GitHub read+write (tools/coding.py)
                # plus any MCP-bridged tool (registered dynamically at container startup, so a
                # name here is only meaningful once that server is actually configured -- same
                # "grant an id, not a guarantee it exists" rule _READ_TOOLS's own comment states).
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {
                    "calculator",
                    "write_file", "edit_file", "run_command", "clone_repository",
                    "git_diff", "git_commit", "git_checkout_branch", "git_status", "git_push",
                    # Persistent workspaces (ADR-013). Only registered when a deployment wires a
                    # workspace manager, so naming them here is a grant, not a guarantee they
                    # exist -- the same rule _READ_TOOLS' own comment states.
                    "open_workspace", "list_workspaces", "destroy_workspace",
                    "github_list_repository_files", "github_get_file_contents",
                    "github_get_issue", "github_get_pull_request",
                    "github_create_branch", "github_commit_files",
                    "github_create_draft_pull_request",
                },
                # required_capabilities gates the *persona* (AgentRegistry.resolve falls back to
                # orchestrator without it); required_capability on each tool spec then gates the
                # *tool* too, so a caller cannot reach these tools through some other persona
                # either. Both must be granted, which is deliberate belt-and-braces given how much
                # more this persona can do than every other one here.
                required_capabilities=frozenset({"engineer_agent"}),
                risk_ceiling=ToolSensitivity.SYSTEM,  # run_command needs SYSTEM
                max_plan_iterations=8,
                # The loop's tool and risk ceilings, made explicit at exactly the values the
                # pre-GAP-024 loop already implied, so moving the engineer onto the standard loop
                # changes nothing: 8 replans + the first pass = 9 passes, each capped at the
                # ExecutionEngine's default 24 step executions = 216 dispatches at most, each
                # costing at most 4 (SYSTEM sensitivity + side-effecting). Tighten from here as
                # evidence says; token and time stay unset (the settings deadline governs time).
                tool_budget=216,
                risk_budget=864,
                memory_scope="engineer",
            ),
        ]
    )
