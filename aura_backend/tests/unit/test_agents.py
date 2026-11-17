"""Agent registry (Phase C1): a persona's tool set, capability, and risk ceiling are data, and
a tool call outside its grant fails closed through the existing policy/audit machinery."""

from __future__ import annotations

import pytest

from aura_core.agents import AgentRegistry, AgentSpec, build_default_agent_registry
from aura_core.domain import ToolCall, ToolCallId
from aura_core.domain.identifiers import UserId
from aura_core.domain.tools import ToolSensitivity
from aura_core.kernel import TurnRequest
from aura_core.ports.runtime import ToolContext
from aura_core.tools import ToolPolicy, ToolRegistry, param, tool


def _request(message: str = "hello", **overrides):
    base = dict(message=message, user_id=UserId("test-user"))
    base.update(overrides)
    return TurnRequest(**base)


class TestAgentRegistry:
    def test_unknown_agent_id_resolves_to_orchestrator(self) -> None:
        registry = build_default_agent_registry()
        assert registry.resolve("not-a-real-agent").id == "orchestrator"

    def test_named_agent_resolves_to_itself(self) -> None:
        registry = build_default_agent_registry()
        assert registry.resolve("finance_advisor").id == "finance_advisor"

    def test_unmet_required_capability_falls_back_to_orchestrator(self) -> None:
        registry = AgentRegistry([
            AgentSpec(id="orchestrator", name="Orchestrator", prompt_template_id="p.orch"),
            AgentSpec(
                id="admin_agent", name="Admin", prompt_template_id="p.admin",
                required_capabilities=frozenset({"admin.act"}),
            ),
        ])
        assert registry.resolve("admin_agent", capabilities=()).id == "orchestrator"
        assert registry.resolve("admin_agent", capabilities=("admin.act",)).id == "admin_agent"

    def test_orchestrator_is_required(self) -> None:
        with pytest.raises(ValueError):
            AgentRegistry([AgentSpec(id="only_one", name="X", prompt_template_id="p")])

    def test_default_registry_covers_every_legacy_persona(self) -> None:
        """The seven ids AGENT_PROMPT_IDS used to define are still resolvable -- this is a
        migration, not a removal."""
        registry = build_default_agent_registry()
        for agent_id in (
            "orchestrator", "task_planner", "finance_advisor", "health_companion",
            "research_agent", "coding_agent", "decision_agent",
        ):
            assert agent_id in registry

    def test_orchestrator_has_no_tool_restriction(self) -> None:
        assert build_default_agent_registry().get("orchestrator").allowed_tools is None

    def test_every_non_orchestrator_persona_names_an_explicit_grant(self) -> None:
        registry = build_default_agent_registry()
        for spec in registry:
            if spec.id == "orchestrator":
                continue
            assert spec.allowed_tools is not None and len(spec.allowed_tools) > 0


class TestAgenticPersonaPlanning:
    """An agentic persona's tools are only reachable through planning (ADR-006), so the
    conversational heuristics in `PlanningEngine.needs_plan` must not be what decides whether
    it gets to use them.

    From a real production turn: the Engineer answered "I don't have the capability to clone
    repositories" with `clone_repository` in its grant, because the message matched no
    multi-step marker and planning was therefore skipped entirely.
    """

    def _tools(self):
        return [
            {"name": "clone_repository", "description": "Copy a repository into the sandbox."},
            {"name": "run_command", "description": "Run a shell command in the sandbox."},
        ]

    async def test_the_engineer_persona_plans_even_when_the_heuristic_would_not(
        self, container
    ) -> None:
        await container.startup()
        request = _request(
            "fix it",  # short, no markers: needs_plan() alone says no
            agent="engineer",
            capabilities=("engineer_agent",),
        )
        assert container.kernel._should_plan(request, self._tools()) is True

    async def test_a_conversational_persona_still_pays_only_when_the_heuristic_says_so(
        self, container
    ) -> None:
        """The forcing is scoped to `max_plan_iterations > 1`, so every other persona keeps the
        narrow gate that stops ordinary Q&A paying for a planning generation."""
        await container.startup()
        request = _request("fix it", agent="orchestrator")
        assert container.kernel._should_plan(request, self._tools()) is False

    async def test_an_explicit_plan_false_still_wins(self, container) -> None:
        """`plan=False` is the caller saying "answer directly"; a persona default must not
        override an explicit instruction."""
        await container.startup()
        request = _request(
            "fix it", agent="engineer", capabilities=("engineer_agent",), plan=False,
        )
        assert container.kernel._should_plan(request, self._tools()) is False

    async def test_without_the_capability_the_fallback_persona_decides(self, container) -> None:
        """A caller lacking `engineer_agent` resolves to the orchestrator, whose
        `max_plan_iterations` is 1 -- so no forcing, same as any other conversational turn."""
        await container.startup()
        request = _request("fix it", agent="engineer", capabilities=())
        assert container.kernel._should_plan(request, self._tools()) is False

    async def test_the_engineer_persona_plans_a_bare_local_project_reference(self, container) -> None:
        """`matching_repository_tool` (tools/coding.py) only recognises a GitHub/GitLab/Bitbucket
        URL or `owner/repo.git` shape -- a short message naming a local project on a paired
        desktop workspace (ADR-011), like "Analyze the VELVET project.", matches neither that
        nor any `_MULTI_STEP_MARKERS` pattern, and is under the 40-character floor
        (`test_a_bare_local_project_reference_is_not_recognised_by_the_heuristic_alone` in
        test_planning_execution.py confirms `needs_plan` alone says no to it). The engineer
        persona's tools here are host-workspace ones (`run_command`/`write_file`/`edit_file`),
        not the repository tools that heuristic covers, so this exercises the general
        `max_plan_iterations > 1` force in `_should_plan` rather than the repository-specific
        carve-out -- the fix that actually makes this class of short, file/project-directed
        Engineer turn reach a tool instead of a prose-only answer."""
        await container.startup()
        request = _request(
            "Analyze the VELVET project.", agent="engineer", capabilities=("engineer_agent",),
        )
        tools = [
            {"name": "run_command", "description": "Run a shell command on the paired desktop."},
            {"name": "write_file", "description": "Write a file on the paired desktop."},
            {"name": "edit_file", "description": "Edit a file on the paired desktop."},
        ]
        assert container.kernel._should_plan(request, tools) is True


class TestAgentGrantEnforcement:
    """The exit criterion: a tool call outside an agent's grant fails closed and is audited."""

    async def test_a_tool_outside_the_grant_is_denied_and_audited(self, tools, audit, user) -> None:
        context = ToolContext(
            user_id=user, correlation_id="c1", deadline_s=5,
            agent_id="health_companion", allowed_tools=frozenset({"propose_health_log"}),
        )
        result, report = await tools.execute(
            ToolCall(id=ToolCallId("1"), name="calculator", arguments={"expression": "1+1"}),
            context,
        )
        assert not result.ok and not report.allowed
        assert report.policy_reason == "outside_agent_grant"

        audited = await audit.query(action="tool.calculator", limit=10)
        assert audited and audited[0].outcome == "denied"
        assert audited[0].detail["reason"] == "outside_agent_grant"

    async def test_a_tool_inside_the_grant_still_runs(self, tools, user) -> None:
        context = ToolContext(
            user_id=user, correlation_id="c2", deadline_s=5,
            agent_id="finance_advisor", allowed_tools=frozenset({"calculator"}),
        )
        result, report = await tools.execute(
            ToolCall(id=ToolCallId("1"), name="calculator", arguments={"expression": "2+2"}),
            context,
        )
        assert result.ok and report.allowed

    async def test_no_grant_is_unrestricted_beyond_capabilities(self, tools, user) -> None:
        """``allowed_tools=None`` is the orchestrator's grant and every pre-Phase-C1 caller:
        unaffected by this feature existing at all."""
        context = ToolContext(user_id=user, correlation_id="c3", deadline_s=5)
        result, _ = await tools.execute(
            ToolCall(id=ToolCallId("1"), name="calculator", arguments={"expression": "3+3"}),
            context,
        )
        assert result.ok

    async def test_risk_ceiling_denies_a_tool_above_it(self, user) -> None:
        @tool(
            "read_drive", "Read a Drive file.",
            param("file_id", "string", "The file id"),
            sensitivity=ToolSensitivity.CONFIDENTIAL,
        )
        async def read_drive(file_id: str) -> str:
            return "contents"

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(read_drive)
        context = ToolContext(
            user_id=user, correlation_id="c4", deadline_s=5,
            max_sensitivity=ToolSensitivity.PERSONAL,
        )
        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="read_drive", arguments={"file_id": "x"}), context,
        )
        assert not result.ok and report.policy_reason == "sensitivity_exceeds_policy"

    def test_risk_ceiling_narrows_what_is_advertised(self) -> None:
        @tool("public_thing", "Do a public thing.")
        async def public_thing() -> str:
            return "ok"

        @tool("secret_thing", "Do a confidential thing.", sensitivity=ToolSensitivity.CONFIDENTIAL)
        async def secret_thing() -> str:
            return "ok"

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(public_thing)
        registry.register(secret_thing)
        names = {s.name for s in registry.available(max_sensitivity=ToolSensitivity.PERSONAL)}
        assert "public_thing" in names and "secret_thing" not in names


class TestKernelAgentWiring:
    """The registry actually reaches the kernel: different agents see different tools and
    different prompts for the same turn."""

    async def test_declared_tools_are_scoped_to_the_resolved_agent(self, container, user) -> None:
        await container.startup()
        orchestrator_tools = {
            d["name"] for d in await container.kernel._tool_declarations(
                _request(agent="orchestrator", user_id=user)
            )
        }
        finance_tools = {
            d["name"] for d in await container.kernel._tool_declarations(
                _request(agent="finance_advisor", user_id=user)
            )
        }
        assert "propose_health_log" in orchestrator_tools
        assert "propose_health_log" not in finance_tools
        assert finance_tools <= orchestrator_tools

    async def test_tool_context_carries_the_resolved_agents_grant(self, container, user) -> None:
        await container.startup()
        context = container.kernel._tool_context(
            _request(agent="health_companion", user_id=user), tainted=False
        )
        assert context.agent_id == "health_companion"
        assert context.allowed_tools is not None
        assert "propose_expense" not in context.allowed_tools

    async def test_a_delegated_grant_can_only_narrow_the_resolved_agents_own(
        self, container, user
    ) -> None:
        """`delegate_to_agent` (aura_core/tools/delegation.py) already intersects grants before
        building the child TurnRequest; `_tool_context`/`_tool_declarations` re-intersect against
        the resolved AgentSpec here too, as defense in depth -- a broader override must never
        widen what the resolved persona itself is allowed to reach."""
        await container.startup()
        finance_grant = container.agents.get("finance_advisor").allowed_tools

        broad_override = frozenset({"calculator", "write_file", "run_command"})
        context = container.kernel._tool_context(
            _request(agent="finance_advisor", user_id=user, delegated_tool_grant=broad_override),
            tainted=False,
        )
        assert context.allowed_tools == finance_grant & broad_override
        assert "write_file" not in context.allowed_tools

        narrow_override = frozenset({"calculator"})
        context = container.kernel._tool_context(
            _request(agent="finance_advisor", user_id=user, delegated_tool_grant=narrow_override),
            tainted=False,
        )
        assert context.allowed_tools == frozenset({"calculator"})

        declared = {
            d["name"] for d in await container.kernel._tool_declarations(
                _request(agent="finance_advisor", user_id=user, delegated_tool_grant=narrow_override)
            )
        }
        assert declared == {"calculator"}


class TestMemoryWriteGrant:
    """Audit FSA-017: `remember_fact` writes a durable record and is not a read tool."""

    def test_the_read_only_decision_agent_cannot_remember(self) -> None:
        spec = build_default_agent_registry().get("decision_agent")
        assert "remember_fact" not in spec.allowed_tools
        assert "recall_memory" in spec.allowed_tools

    @pytest.mark.parametrize(
        "agent_id",
        ["task_planner", "finance_advisor", "health_companion", "research_agent",
         "coding_agent", "engineer"],
    )
    def test_acting_personas_keep_the_grant(self, agent_id: str) -> None:
        spec = build_default_agent_registry().get(agent_id)
        assert "remember_fact" in spec.allowed_tools


class TestTheEngineerGrantCoversItsOwnTools:
    """Audit FSA-057 (Phase 1 regression): `write_files` -- the D6 atomic multi-file edit -- was
    registered under the `engineer_agent` capability but missing from the Engineer persona's grant,
    so the persona could never call it, while its tests bypassed the grant with hand-built
    contexts. The grant must name every tool the sandbox and code-host builders register for that
    capability; a new Engineer tool that is not granted is a tool nobody can use."""

    def test_every_engineer_capability_tool_is_in_the_engineer_grant(self) -> None:
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner
        from aura_core.tools.coding import build_github_tools, build_sandbox_tools

        registry = build_default_agent_registry()
        engineer = registry.get("engineer")
        assert engineer is not None and engineer.allowed_tools is not None

        tools = build_sandbox_tools(InMemorySandboxRunner()) + build_github_tools(lambda *_: None)
        engineer_tools = {
            t.spec.name for t in tools if t.spec.required_capability == "engineer_agent"
        }
        assert "write_files" in engineer_tools, "the regression this pins would be vacuous"
        missing = sorted(engineer_tools - engineer.allowed_tools)
        assert missing == [], f"registered for engineer_agent but not granted to the persona: {missing}"


class TestEveryPersonaIsToldItsTools:
    """Audit 2026-11-16 F11: `system.decision_agent` declared no `capabilities` variable, so the
    kernel's tool list never reached its prompt -- the only persona with tools that was never
    told what they were. Every persona with a non-empty grant must be able to render the list."""

    @pytest.mark.parametrize(
        "spec", list(build_default_agent_registry()), ids=lambda s: s.id,
    )
    def test_a_persona_with_tools_declares_the_capabilities_variable(self, spec) -> None:
        from aura_core.prompts.library import BUILTIN_PROMPTS

        if spec.allowed_tools is not None and not spec.allowed_tools:
            pytest.skip("no tools to list")
        template = next(t for t in BUILTIN_PROMPTS if t.id == spec.prompt_template_id)
        names = {v.name for v in template.variables}
        assert "capabilities" in names, f"{template.id} never renders the persona's tool list"
        assert "{{#capabilities}}" in template.body
