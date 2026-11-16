"""delegate_to_agent (aura_core/tools/delegation.py): one persona handing a self-contained
sub-task to another through a recursive AIKernel.run() call.

Unit tests here exercise the tool handler directly against a fake kernel (a plain object with an
async `.run()`), the same style test_agentic_loop.py uses for its fake planner/scripted executor
-- deterministic and independent of what a real LLM would plan. The one end-to-end test at the
bottom goes through the real, fully-wired `container` fixture to prove the whole path (real
AgentRegistry, real ToolRegistry/ToolPolicy, real recursive AIKernel.run()) actually composes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_core.agents.registry import build_default_agent_registry
from aura_core.domain import ToolCall, ToolCallId
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.domain.tools import ToolSensitivity
from aura_core.ports.runtime import ToolContext
from aura_core.tools.delegation import build_delegation_tools


def _context(**overrides) -> ToolContext:
    base = dict(
        user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0,
        agent_id="orchestrator", capabilities=(), delegation_depth=0,
    )
    base.update(overrides)
    return ToolContext(**base)


class _FakeKernel:
    """A stand-in for AIKernel: records the child TurnRequest it was called with and returns a
    canned result. delegate_to_agent only reads `.text`/`.needs_confirmation`/
    `.pending_tool_calls` off the result, so a SimpleNamespace is enough -- no real TurnResult
    (and its required TurnTelemetry) needed."""

    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result
        self.received_request = None

    async def run(self, request):
        self.received_request = request
        return self._result


def _ok_result(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(text=text, needs_confirmation=(), pending_tool_calls=())


def _delegate_tool(fake_kernel: _FakeKernel, agents=None, **kwargs):
    agents = agents or build_default_agent_registry()
    tools = build_delegation_tools(agents=agents, kernel=lambda: fake_kernel, **kwargs)
    by_name = {t.spec.name: t for t in tools}
    return by_name["delegate_to_agent"], agents


class TestDepthCeiling:
    async def test_refuses_once_the_depth_ceiling_is_reached(self) -> None:
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel, max_depth=1)
        with pytest.raises(ToolExecutionError):
            await delegate.execute(
                {"target_agent_id": "finance_advisor", "subtask": "x"},
                _context(delegation_depth=1),  # already at the default ceiling
            )
        assert fake_kernel.received_request is None, "must refuse before recursing"

    async def test_allows_the_first_hop_and_stamps_the_childs_depth(self) -> None:
        fake_kernel = _FakeKernel(_ok_result("the answer"))
        delegate, _ = _delegate_tool(fake_kernel, max_depth=1)
        result = await delegate.execute(
            {"target_agent_id": "finance_advisor", "subtask": "x"}, _context(delegation_depth=0),
        )
        assert result == "the answer"
        assert fake_kernel.received_request.delegation_depth == 1
        assert fake_kernel.received_request.agent == "finance_advisor"


class TestGrantIntersection:
    """The child TurnRequest's grant must never be broader than either the caller's or the
    target persona's own declared grant -- verified against the real default registry so this
    tracks whatever finance_advisor's grant actually is, rather than a hand-picked fixture."""

    async def test_narrower_caller_grant_wins_over_the_targets_own(self) -> None:
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel)
        await delegate.execute(
            {"target_agent_id": "finance_advisor", "subtask": "x"},
            # propose_health_log is health_companion's, not finance_advisor's -- naming it in the
            # caller's own grant must not smuggle it into the child's.
            _context(allowed_tools=frozenset({"calculator", "propose_health_log"})),
        )
        # finance_advisor's own grant includes calculator but not propose_health_log -- the
        # intersection keeps only what both sides allow.
        assert fake_kernel.received_request.delegated_tool_grant == frozenset({"calculator"})

    async def test_unrestricted_caller_gets_exactly_the_targets_own_grant(self) -> None:
        fake_kernel = _FakeKernel(_ok_result())
        delegate, agents = _delegate_tool(fake_kernel)
        await delegate.execute(
            {"target_agent_id": "finance_advisor", "subtask": "x"},
            _context(allowed_tools=None),  # orchestrator's own grant
        )
        assert (
            fake_kernel.received_request.delegated_tool_grant
            == agents.get("finance_advisor").allowed_tools
        )

    async def test_risk_ceiling_is_the_minimum_of_the_two(self) -> None:
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel)
        # finance_advisor's own ceiling is PERSONAL; a narrower caller ceiling must win.
        await delegate.execute(
            {"target_agent_id": "finance_advisor", "subtask": "x"},
            _context(max_sensitivity=ToolSensitivity.PUBLIC),
        )
        assert fake_kernel.received_request.delegated_risk_ceiling == ToolSensitivity.PUBLIC


class TestPrefixGrantCannotWiden:
    """Audit T-03: the engineer's `mcp_` prefix grant is intersected like any other grant -- a
    caller that lacks it cannot obtain MCP tools by delegating to the engineer."""

    _ENGINEER = dict(capabilities=("engineer_agent",))

    async def _child(self, **context):
        fake_kernel = _FakeKernel(_ok_result())
        delegate, agents = _delegate_tool(fake_kernel)
        await delegate.execute(
            {"target_agent_id": "engineer", "subtask": "x"}, _context(**self._ENGINEER, **context),
        )
        return fake_kernel.received_request, agents

    async def test_a_caller_without_the_prefix_does_not_hand_it_to_the_engineer(self) -> None:
        planner = build_default_agent_registry().get("task_planner")
        child, _ = await self._child(
            agent_id="task_planner", allowed_tools=planner.allowed_tools,
            allowed_tool_prefixes=planner.allowed_tool_prefixes,
        )
        assert child.delegated_tool_grant is not None
        assert child.delegated_tool_prefixes == frozenset()
        assert not any(n.startswith("mcp_") for n in child.delegated_tool_grant)

    async def test_and_the_engineer_sub_turn_cannot_reach_mcp(self, container, user) -> None:
        """The child request, as the real kernel resolves it: no `mcp_*` declared, none permitted."""
        from aura_core.adapters.coding_fakes import InMemoryMcpClient
        from aura_core.ports.coding import McpToolDescriptor
        from aura_core.tools.coding import build_mcp_bridge_tools

        await container.startup()
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(server="fs", name="list_dir", description="List.")],
            handlers={("fs", "list_dir"): lambda args: "a.txt"},
        )
        for t in await build_mcp_bridge_tools(client, safe_tools=["mcp_fs_list_dir"]):
            container.tools.register(t, overwrite=True)

        planner = container.agents.get("task_planner")
        child, _ = await self._child(
            user_id=user, agent_id="task_planner", allowed_tools=planner.allowed_tools,
            allowed_tool_prefixes=planner.allowed_tool_prefixes,
        )
        declared = {d["name"] for d in await container.kernel._tool_declarations(child)}
        assert "mcp_fs_list_dir" not in declared
        context = container.kernel._tool_context(child, tainted=False)
        assert context.agent_id == "engineer"
        assert not context.permits_tool("mcp_fs_list_dir")
        result, report = await container.tools.execute(
            ToolCall(id=ToolCallId("1"), name="mcp_fs_list_dir", arguments={}),
            context, capabilities=("engineer_agent",),
        )
        assert not result.ok and report.policy_reason == "outside_agent_grant"

    async def test_a_caller_naming_one_mcp_tool_passes_only_that_one(self) -> None:
        child, _ = await self._child(allowed_tools=frozenset({"mcp_fs_read", "calculator"}))
        assert child.delegated_tool_grant == frozenset({"mcp_fs_read", "calculator"})
        assert child.delegated_tool_prefixes == frozenset()

    async def test_an_unrestricted_caller_gets_exactly_the_engineers_own_grant(self) -> None:
        child, agents = await self._child(allowed_tools=None)
        engineer = agents.get("engineer")
        assert child.delegated_tool_grant == engineer.allowed_tools
        assert child.delegated_tool_prefixes == frozenset({"mcp_"})

    async def test_an_engineer_caller_keeps_its_prefix(self) -> None:
        engineer = build_default_agent_registry().get("engineer")
        child, _ = await self._child(
            agent_id="engineer", allowed_tools=engineer.allowed_tools,
            allowed_tool_prefixes=engineer.allowed_tool_prefixes,
        )
        assert child.delegated_tool_prefixes == frozenset({"mcp_"})


class TestGrantIntersectionHelper:
    """`intersect_tool_grants` is the one function both delegation and the kernel use."""

    def test_names_and_prefixes_from_both_sides(self) -> None:
        from aura_core.domain.tools import intersect_tool_grants

        tools, prefixes = intersect_tool_grants(
            frozenset({"a", "mcp_x_y"}), frozenset(), frozenset({"a", "b"}), frozenset({"mcp_"}),
        )
        assert tools == frozenset({"a", "mcp_x_y"}) and prefixes == frozenset()

    def test_a_narrower_prefix_survives_a_broader_one(self) -> None:
        from aura_core.domain.tools import intersect_tool_grants

        _, prefixes = intersect_tool_grants(
            frozenset(), frozenset({"mcp_fs_"}), frozenset(), frozenset({"mcp_"}),
        )
        assert prefixes == frozenset({"mcp_fs_"})

    def test_none_is_unrestricted_on_either_side(self) -> None:
        from aura_core.domain.tools import intersect_tool_grants

        assert intersect_tool_grants(None, frozenset(), None, frozenset()) == (None, frozenset())
        assert intersect_tool_grants(None, frozenset(), frozenset({"a"}), frozenset({"m_"})) == (
            frozenset({"a"}), frozenset({"m_"}),
        )
        assert intersect_tool_grants(frozenset({"a"}), frozenset({"m_"}), None, frozenset({"z_"})) == (
            frozenset({"a"}), frozenset({"m_"}),
        )


class TestCapabilityGatedTarget:
    async def test_missing_capability_refuses_rather_than_silently_redirecting(self) -> None:
        """AgentRegistry.resolve() falls back to orchestrator when the caller lacks a target's
        required_capabilities. Delegation must not inherit that silently -- the caller asked for
        `engineer` specifically and should see a clear refusal, not an unexplained substitution.
        """
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel)
        with pytest.raises(ToolExecutionError):
            await delegate.execute(
                {"target_agent_id": "engineer", "subtask": "x"},
                _context(capabilities=()),  # lacks "engineer_agent"
            )
        assert fake_kernel.received_request is None

    async def test_the_capability_actually_reaches_the_target(self) -> None:
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel)
        result = await delegate.execute(
            {"target_agent_id": "engineer", "subtask": "x"},
            _context(capabilities=("engineer_agent",)),
        )
        assert result == "ok"
        assert fake_kernel.received_request.agent == "engineer"


class TestNestedConfirmation:
    async def test_a_sub_turn_needing_confirmation_fails_cleanly_instead_of_dropping_it(self) -> None:
        fake_kernel = _FakeKernel(
            SimpleNamespace(text="", needs_confirmation=("propose_expense",), pending_tool_calls=())
        )
        delegate, _ = _delegate_tool(fake_kernel)
        with pytest.raises(ToolExecutionError):
            await delegate.execute(
                {"target_agent_id": "finance_advisor", "subtask": "log $10 for lunch"}, _context(),
            )


class TestEndToEndDelegation:
    """Through the real, fully-wired container: real AgentRegistry, real ToolRegistry/ToolPolicy,
    a real recursive AIKernel.run() call -- proving the whole path composes, not just the tool's
    own logic in isolation."""

    async def test_orchestrator_can_delegate_to_finance_advisor_through_the_real_kernel(
        self, container, user
    ) -> None:
        await container.startup()
        context = ToolContext(
            user_id=user, correlation_id="c-e2e", deadline_s=30.0,
            agent_id="orchestrator", allowed_tools=None, capabilities=(), delegation_depth=0,
        )
        result, report = await container.tools.execute(
            ToolCall(
                id=ToolCallId("1"), name="delegate_to_agent",
                arguments={"target_agent_id": "finance_advisor", "subtask": "What is 2+2?"},
            ),
            context,
        )
        assert result.ok, result.content
        assert report.allowed
        assert result.content


class TestTaintInheritance:
    """ADR-006 across the hop (audit FSA-001): a parent turn whose context was quarantined
    must not be able to launder that by delegating to a persona whose own inputs look clean."""

    async def test_a_tainted_caller_taints_the_child_turn(self) -> None:
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel)
        await delegate.execute(
            {"target_agent_id": "finance_advisor", "subtask": "summarise my spending"},
            _context(tainted=True),
        )
        assert fake_kernel.received_request.inherited_taint is True

    async def test_a_clean_caller_leaves_the_child_untainted(self) -> None:
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel)
        await delegate.execute(
            {"target_agent_id": "finance_advisor", "subtask": "summarise my spending"},
            _context(tainted=False),
        )
        assert fake_kernel.received_request.inherited_taint is False
