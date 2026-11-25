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
from ..domain.tools import ToolSensitivity, tool_grant_permits
from ..prompts.library import AGENT_PROMPT_IDS

__all__ = ["AgentSpec", "AgentRegistry", "build_default_agent_registry"]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One agent persona's identity, prompt, and grant.

    ``allowed_tools=None`` means "no restriction beyond the caller's own capabilities" -- the
    orchestrator's grant, and the correct default for a general-purpose persona that should not
    be narrower than what the user themselves is allowed to do. Every other persona names an
    explicit set: a tool absent from it is refused even if the caller could otherwise use it.

    ``allowed_tool_prefixes`` widens an explicit set by name prefix, for a tool family whose
    names only exist at runtime (today: ``mcp_``, the MCP bridge's ``mcp_{server}_{tool}``,
    granted to ``engineer`` only; audit T-03). It is ignored when ``allowed_tools`` is ``None``,
    and every checkpoint asks :meth:`permits_tool` (or the same
    :func:`~aura_core.domain.tools.tool_grant_permits`) rather than testing set membership.
    """

    id: str
    name: str
    prompt_template_id: str
    allowed_tools: frozenset[str] | None = None
    allowed_tool_prefixes: frozenset[str] = frozenset()
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

    def __post_init__(self) -> None:
        # An empty prefix matches every name: "everything" spelled so that it reads like a
        # narrow grant. The unrestricted grant is ``allowed_tools=None``, said out loud.
        if any(not p for p in self.allowed_tool_prefixes):
            raise ValueError(f"agent {self.id!r}: an empty tool prefix would grant every tool")

    def permits_tool(self, name: str) -> bool:
        """Whether this persona's own grant permits ``name`` (before any delegation narrowing)."""
        return tool_grant_permits(name, self.allowed_tools, self.allowed_tool_prefixes)

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

# The ADR-036 analysis tools (`tools/analysis.py`), written out rather than imported: `agents` does
# not depend on `tools`, and `tests/unit/test_agents.py` pins that every name here is a tool the
# builder really registers. All are pure text-in/JSON-out computations -- no I/O, no side effects --
# so granting them widens what a persona can *see*, not what it can *do*. `decision_agent` and the
# personal-data personas are deliberately left out: nothing here is about their domain.
_CODE_ANALYSIS_TOOLS = frozenset({
    "analyze_python_ast", "analyze_code_complexity", "verify_python_symbols", "review_code_diff",
    "scan_code_security", "analyze_change_impact", "generate_diagram", "check_requirements",
    "scan_dependency_licenses", "compare_api_specs", "inspect_sql", "evaluate_quality_gate",
    "plan_rollout",
})
_DATA_ANALYSIS_TOOLS = frozenset({"analyze_logs", "validate_data_records", "check_model_drift"})

# ADR-042 (`tools/open_pure.py`): fourteen more pure text-in/JSON-out tools, the same risk profile as
# the analysis tools above. None is tagged `untrusted_content`, so `coding_agent` may hold them.
_OPEN_PURE_CODE_TOOLS = frozenset({
    "check_architecture", "check_diff_scope", "explain_source", "analyze_source_structure",
    "parse_structured_data", "detect_file_type", "validate_workflow", "verify_acceptance_criteria",
    "collect_verification_evidence", "generate_artifact", "apply_plan_feedback", "detect_ambiguity",
    "generate_quiz",
})
_OPEN_PURE_PLANNING_TOOLS = frozenset({
    "detect_ambiguity", "validate_workflow", "generate_artifact", "apply_plan_feedback",
    "verify_acceptance_criteria", "summarize_for_mobile",
})
_OPEN_PURE_RESEARCH_TOOLS = frozenset({
    "detect_file_type", "parse_structured_data", "explain_source", "generate_quiz", "detect_ambiguity",
})

# ADR-036 groups B and C. Both are `engineer_agent`-gated and side-effecting: B runs project code
# (`tools/open_exec.py`), C acts on the user's paired desktop (`tools/desktop_ops.py`).
_ENGINEER_OPEN_TOOLS = frozenset({
    "generate_and_run_tests", "analyze_test_quality",
    "docker_build", "docker_compose", "profile_python",
})

# ADR-040. `lint_code` / `check_format` (`tools/devtools.py`) run in the engineer's workspace through
# the same runner as `run_command`. The file-reading and search tools the engineer lacked
# (`tools/media.py`, `tools/websearch.py`) are each only registered when their own setting is present
# (AURA_TOOLS_FILES_ROOT for the file readers, AURA_WEB_SEARCH_URL for search), so naming them here
# is a grant, not a guarantee.
_ENGINEER_DEVTOOLS = frozenset({"lint_code", "check_format", "scan_security"})
_DOCUMENT_READING_TOOLS = frozenset({"parse_document", "inspect_spreadsheet", "ocr_image"})
_ENGINEER_READING_TOOLS = _DOCUMENT_READING_TOOLS | frozenset({
    "web_search", "fetch_url", "analyze_image_requirements", "transcribe_audio",
})

# ADR-043 (`tools/open_workspace.py`): the tools/open classes that read the user's workspace. Each file
# tool is registered only with AURA_TOOLS_FILES_ROOT, `project_status` only with a run store, so naming
# them here is a grant, not a guarantee. Engineer only: `coding_agent` holds no file-reading tool, and
# these return workspace content (tagged untrusted_content), which that persona must never hold.
_ENGINEER_WORKSPACE_TOOLS = frozenset({
    "index_repository", "search_repository", "check_file_staleness", "load_project_conventions",
    "load_project_skills", "read_notebook", "inspect_binary_file", "verify_backup", "project_status",
})


# ADR-044. `verify_release_health` (`tools/release.py`) is only registered when AURA_TOOLS_HTTP_ALLOWED_HOSTS
# is set, so naming it here is a grant, not a guarantee. It returns other systems' text (untrusted_content),
# so the Engineer holds it and the coding agent does not.
_ENGINEER_RELEASE_TOOLS = frozenset({"verify_release_health"})

# ADR-047. Registered only when the operator configured them (AURA_OPS_*), so naming them is a grant, not a
# guarantee. The read tools return other systems' text (untrusted_content); the two deploy tools are
# side-effecting, need `engineer_agent` and a confirmation on every call.
_ENGINEER_OPS_TOOLS = frozenset({
    "check_platform_health", "query_telemetry", "list_workflow_runs", "deploy_release", "rollback_release"})

# ADR-044. Model drift against a stored history (`tools/drift.py`): `record_model_metric` writes the
# owner's own metric series (side-effecting), `compare_model_metric` reads it. Always registered with the
# analysis group, so unlike the release tool these names are guaranteed to exist.
_ENGINEER_MODEL_MONITOR_TOOLS = frozenset({"record_model_metric", "compare_model_metric"})

# ADR-044. `research_topic` (`tools/research.py`) calls search_knowledge / web_search / fetch_url through the
# registry as the caller. Its output is other people's text (untrusted_content), so only the personas that
# already hold web_search / fetch_url get it, never the coding agent.
_RESEARCH_TOOLS = frozenset({"research_topic"})

# `tools/session_tools.py`: checkpoint the conversation and read it back. Written out rather than
# imported, like the sets above (`tests/unit/test_agents.py` pins the names against the builder).
_SESSION_TOOLS = frozenset({"save_session_checkpoint", "recall_session_checkpoint"})


def build_default_agent_registry(*, engineer_token_budget: int | None = None) -> AgentRegistry:
    """The seven personas :data:`AGENT_PROMPT_IDS` used to define, now with real grants.

    Prompt template ids are read from ``AGENT_PROMPT_IDS`` rather than repeated here, so which
    template a persona renders has one source of truth; only the grant (tools, risk, capability,
    memory scope) is new. Risk ceilings and tool sets are drawn conservatively from what each
    persona's prompt already claims to do -- narrowing later, as more tools exist, is the expected
    direction; broadening a persona's grant is a decision to make deliberately, not a default to
    fall into.

    ``engineer_token_budget`` (``AURA_ENGINEER_TOKEN_BUDGET``, ADR-037) gives the Engineer a run
    token budget; ``None`` or ``0`` leaves it unset, which is what a caller that passes nothing
    (tests, ``evaluation/agent_runs.py``) has always got.
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
                } | _OPEN_PURE_PLANNING_TOOLS,
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
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {"calculator", "fetch_url", "web_search"} | {
                    "synthesize_research", "validate_data_records",
                } | _DOCUMENT_READING_TOOLS | {"transcribe_audio"} | _OPEN_PURE_RESEARCH_TOOLS | _RESEARCH_TOOLS,
                risk_ceiling=ToolSensitivity.CONFIDENTIAL, memory_scope="shared",
            ),
            AgentSpec(
                id="coding_agent", name="Coding Agent",
                prompt_template_id=AGENT_PROMPT_IDS["coding_agent"],
                allowed_tools=(
                    _READ_TOOLS | _MEMORY_WRITE_TOOLS | {"calculator"}
                    | _CODE_ANALYSIS_TOOLS | _DATA_ANALYSIS_TOOLS | _OPEN_PURE_CODE_TOOLS
                ),
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
                # Sandboxed filesystem/command/git tools and GitHub read+write (tools/coding.py),
                # by exact name. MCP-bridged tools are granted separately, by the `mcp_` prefix
                # below: they are registered at container startup as `mcp_{server}_{tool}`, so no
                # exact name can be written here, and the exact-name set alone left the Engineer
                # unable to use any of them (audit T-03).
                allowed_tools=_READ_TOOLS | _MEMORY_WRITE_TOOLS | {
                    "calculator",
                    # `write_files` is D6's atomic multi-file edit; it was registered under the
                    # engineer_agent capability but missing here, so the persona could never use
                    # it (audit FSA-057). tests/unit/test_agents.py pins that every tool the
                    # sandbox and code-host builders register for the capability is granted.
                    "write_file", "write_files", "edit_file", "run_command", "clone_repository",
                    "git_diff", "git_commit", "git_checkout_branch", "git_status", "git_push",
                    # Snapshot and roll back the workspace (ADR-039); restore always confirms.
                    "workspace_snapshot", "list_workspace_snapshots", "workspace_restore",
                    # Persistent workspaces (ADR-013). Only registered when a deployment wires a
                    # workspace manager, so naming them here is a grant, not a guarantee they
                    # exist -- the same rule _READ_TOOLS' own comment states.
                    "open_workspace", "list_workspaces", "destroy_workspace",
                    "github_list_repository_files", "github_get_file_contents",
                    "github_get_issue", "github_get_pull_request",
                    "github_create_branch", "github_commit_files",
                    "github_create_draft_pull_request",
                } | _CODE_ANALYSIS_TOOLS | _DATA_ANALYSIS_TOOLS | _ENGINEER_OPEN_TOOLS | _ENGINEER_DEVTOOLS
                | _ENGINEER_READING_TOOLS | _SESSION_TOOLS | _OPEN_PURE_CODE_TOOLS | _OPEN_PURE_PLANNING_TOOLS
                | _ENGINEER_WORKSPACE_TOOLS | _ENGINEER_RELEASE_TOOLS | _ENGINEER_OPS_TOOLS | _ENGINEER_MODEL_MONITOR_TOOLS | _RESEARCH_TOOLS,
                # Every tool the MCP bridge registers (`build_mcp_bridge_tools`). The prefix only
                # decides *who* may ask: each such tool still needs the `engineer_agent`
                # capability, and an unreviewed one stays CONFIDENTIAL + side-effecting, hence
                # confirmation-gated (`AURA_MCP_SAFE_TOOLS` is the only way out of that). No other
                # persona holds a prefix grant, and delegation intersects it like any other grant
                # (`domain/tools.py::intersect_tool_grants`), so a caller without it cannot obtain
                # it by delegating here.
                allowed_tool_prefixes=frozenset({"mcp_"}),
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
                token_budget=engineer_token_budget or None,
                memory_scope="engineer",
            ),
        ]
    )
