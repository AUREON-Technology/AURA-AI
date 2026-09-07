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
            d["name"] for d in container.kernel._tool_declarations(
                _request(agent="orchestrator", user_id=user)
            )
        }
        finance_tools = {
            d["name"] for d in container.kernel._tool_declarations(
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
