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
from aura_core.tools.coding import build_github_tools, build_sandbox_tools
from aura_core.tools.open_exec import build_open_exec_tools


class _PairedDesktop:
    """Enough of a host-agent registry for `build_sandbox_tools(desktop_ops=True)` to register the
    ADR-036 group C tools, so the grant pins below cover them too."""

    async def resolve(self, user_id):
        return object()


def _engineer_tool_builders():
    """Every builder whose tools carry `required_capability == "engineer_agent"` (ADR-036 adds
    two groups: code-running tools, and desktop-only docker/profiling tools)."""
    from aura_core.adapters.coding_fakes import InMemorySandboxRunner

    return (
        build_sandbox_tools(InMemorySandboxRunner(), desktop_ops=True, host_agent_registry=_PairedDesktop())
        + build_open_exec_tools()
    )


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

    async def test_every_engineer_capability_tool_is_in_the_engineer_grant(self) -> None:
        """Audit T-03 extends this to the MCP bridge: `mcp_{server}_{tool}` names exist only once
        a server is configured, so an exact-name grant could never cover them and the Engineer
        could use no MCP tool at all, whatever its comment said."""
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        registry = build_default_agent_registry()
        engineer = registry.get("engineer")
        assert engineer is not None and engineer.allowed_tools is not None

        tools = (
            _engineer_tool_builders() + build_github_tools(lambda *_: None)
            + await _mcp_tools()
        )
        engineer_tools = {
            t.spec.name for t in tools if t.spec.required_capability == "engineer_agent"
        }
        assert "write_files" in engineer_tools, "the regression this pins would be vacuous"
        assert {"docker_build", "generate_and_run_tests"} <= engineer_tools, "ADR-036 pin would be vacuous"
        assert {"mcp_fs_list_dir", "mcp_fs_write"} <= engineer_tools, "MCP pin would be vacuous"
        missing = sorted(n for n in engineer_tools if not engineer.permits_tool(n))
        assert missing == [], f"registered for engineer_agent but not granted to the persona: {missing}"

    async def test_every_engineer_capability_tool_passes_the_policy_grant_check(
        self, container, user
    ) -> None:
        """The same pin, through the context the kernel actually builds and the policy that
        actually enforces it -- a grant the spec declares but the turn never carries is no grant."""
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        await container.startup()
        context = container.kernel._tool_context(
            _request(agent="engineer", user_id=user, capabilities=("engineer_agent",)), tainted=False
        )
        assert context.agent_id == "engineer"
        tools = (
            _engineer_tool_builders() + build_github_tools(lambda *_: None)
            + await _mcp_tools()
        )
        policy = ToolPolicy()
        refused = sorted(
            t.spec.name for t in tools
            if t.spec.required_capability == "engineer_agent"
            and policy.evaluate(t.spec, context=context, capabilities=("engineer_agent",)).reason
            == "outside_agent_grant"
        )
        assert refused == [], f"engineer turn refused its own tools by grant: {refused}"


async def _mcp_tools():
    """Two MCP-bridged tools, one reviewed-safe and one default-deny, from the same fake client
    `test_engineer_tools.py::TestMcpBridge` uses."""
    from aura_core.adapters.coding_fakes import InMemoryMcpClient
    from aura_core.ports.coding import McpToolDescriptor
    from aura_core.tools.coding import build_mcp_bridge_tools

    client = InMemoryMcpClient(
        tools=[
            McpToolDescriptor(server="fs", name="list_dir", description="List a directory."),
            McpToolDescriptor(server="fs", name="write", description="Write a file."),
        ],
        handlers={("fs", "list_dir"): lambda args: "a.txt\nb.txt"},
    )
    return await build_mcp_bridge_tools(client, safe_tools=["mcp_fs_list_dir"])


class TestMcpToolsReachOnlyTheEngineer:
    """Audit T-03, end to end through the real container: the kernel's declarations and the
    policy both honour the engineer's `mcp_` prefix grant, and no other persona gets it."""

    async def _started(self, container):
        await container.startup()
        for t in await _mcp_tools():
            container.tools.register(t, overwrite=True)
        return container

    async def _declared(self, container, user, agent: str, **extra) -> set[str]:
        return {
            d["name"] for d in await container.kernel._tool_declarations(
                _request(agent=agent, user_id=user, capabilities=("engineer_agent",), **extra)
            )
        }

    async def test_an_engineer_turn_is_offered_mcp_tools(self, container, user) -> None:
        await self._started(container)
        declared = await self._declared(container, user, "engineer")
        assert {"mcp_fs_list_dir", "mcp_fs_write"} <= declared

    async def test_a_task_planner_turn_is_not(self, container, user) -> None:
        await self._started(container)
        declared = await self._declared(container, user, "task_planner")
        assert declared, "the task planner should still be offered its own tools"
        assert not any(n.startswith("mcp_") for n in declared)

    @pytest.mark.parametrize(
        "agent_id",
        ["task_planner", "finance_advisor", "health_companion", "research_agent",
         "coding_agent", "decision_agent"],
    )
    def test_no_other_explicit_persona_holds_a_prefix_grant(self, agent_id: str) -> None:
        spec = build_default_agent_registry().get(agent_id)
        assert spec.allowed_tool_prefixes == frozenset()
        assert not spec.permits_tool("mcp_fs_list_dir")

    async def test_policy_refuses_an_mcp_call_from_a_non_engineer_context(
        self, container, user
    ) -> None:
        await self._started(container)
        context = container.kernel._tool_context(
            _request(agent="task_planner", user_id=user, capabilities=("engineer_agent",)),
            tainted=False,
        )
        result, report = await container.tools.execute(
            ToolCall(id=ToolCallId("1"), name="mcp_fs_list_dir", arguments={}),
            context, capabilities=("engineer_agent",),
        )
        assert not result.ok and report.policy_reason == "outside_agent_grant"

    async def test_the_engineer_can_actually_run_a_safe_mcp_tool(self, container, user) -> None:
        await self._started(container)
        context = container.kernel._tool_context(
            _request(agent="engineer", user_id=user, capabilities=("engineer_agent",)),
            tainted=False,
        )
        result, report = await container.tools.execute(
            ToolCall(id=ToolCallId("1"), name="mcp_fs_list_dir", arguments={}),
            context, capabilities=("engineer_agent",),
        )
        assert report.allowed and result.ok, report.policy_reason

    async def test_an_unreviewed_mcp_tool_still_needs_confirmation(self, container, user) -> None:
        """The grant changes who may ask, not what asking costs: default-deny MCP tools stay
        CONFIDENTIAL + side-effecting + confirmation-gated for the engineer too."""
        await self._started(container)
        context = container.kernel._tool_context(
            _request(agent="engineer", user_id=user, capabilities=("engineer_agent",)),
            tainted=False,
        )
        spec = container.tools.get("mcp_fs_write").spec
        decision = container.tools._policy.evaluate(
            spec, context=context, capabilities=("engineer_agent",)
        )
        assert decision.allowed and decision.requires_confirmation

    async def test_a_delegated_grant_without_the_prefix_strips_mcp_from_an_engineer_turn(
        self, container, user
    ) -> None:
        """Defense in depth: even if `delegate_to_agent` handed an engineer sub-turn a names-only
        grant, the kernel's re-intersection must not restore the persona's own prefix."""
        await self._started(container)
        override = frozenset({"calculator", "write_file"})
        declared = await self._declared(container, user, "engineer", delegated_tool_grant=override)
        assert not any(n.startswith("mcp_") for n in declared)
        context = container.kernel._tool_context(
            _request(agent="engineer", user_id=user, capabilities=("engineer_agent",),
                     delegated_tool_grant=override),
            tainted=False,
        )
        assert not context.permits_tool("mcp_fs_list_dir")
        assert context.permits_tool("write_file")

    async def test_a_service_caller_gets_no_mcp_tools(self, container, user) -> None:
        """v3's service-key turns carry `delegated_tool_grant=frozenset()`: no prefix rides along."""
        await self._started(container)
        declared = await self._declared(container, user, "engineer", delegated_tool_grant=frozenset())
        assert declared == set()

    async def test_sandbox_and_desktop_tools_are_hidden_when_their_runner_is_absent(
        self, container, user
    ) -> None:
        """Audit T-13: with no paired desktop and no server-side Docker sandbox (the default test
        container, and Railway), `_tool_declarations` hides sandbox-backed and desktop-only tools
        instead of advertising tools that always fail at `create_task`."""
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner
        from aura_core.adapters.host_agent import HostAgentSession

        await self._started(container)
        # 1. Neither desktop nor server sandbox: sandbox + desktop tools are hidden, while pure
        # analysis and MCP tools remain advertised.
        no_runner = await self._declared(container, user, "engineer")
        assert not ({"run_command", "write_file", "git_status", "git_push", "lint_code", "docker_build"} & no_runner)
        assert {"analyze_python_ast", "mcp_fs_list_dir"} <= no_runner

        # 2. Server-side sandbox available (no desktop paired): sandbox tools appear, but
        # desktop-only tools (`docker_build`, `docker_compose`, `profile_python`) stay hidden.
        container.sandbox_runner._fallback = InMemorySandboxRunner()
        with_server_sandbox = await self._declared(container, user, "engineer")
        assert {"run_command", "write_file", "git_status", "git_push", "lint_code"} <= with_server_sandbox
        assert not ({"docker_build", "docker_compose", "profile_python"} & with_server_sandbox)

        # 3. Paired desktop session active: both sandbox and desktop-only tools appear.
        async def _noop_send(_frame):
            return None

        await container.host_agent_registry.register(HostAgentSession(user, _noop_send))
        with_desktop = await self._declared(container, user, "engineer")
        assert {"run_command", "write_file", "git_push", "docker_build", "docker_compose", "profile_python"} <= with_desktop


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
