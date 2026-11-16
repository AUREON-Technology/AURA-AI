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


class TestTargetsFollowTheLiveRegistry:
    """Audit 2026-11-16 F6: the target enum used to be computed once, at container build, so a
    blueprint agent deployed later (ActiveBlueprint.swap) sat in the registry but could never be
    named as a target -- and ADR-021's "delegate_to_agent sub-turns carry the same person's
    tokens" had no generated agent to carry them to."""

    @staticmethod
    def _blueprint_registry():
        from aura_core.adaptation.runtime import ActiveBlueprint, BLUEPRINT_AGENT_PROMPT
        from aura_core.agents.registry import AgentSpec

        live = ActiveBlueprint(build_default_agent_registry())
        generated = AgentSpec(
            id="bp_orders", name="Orders", prompt_template_id=BLUEPRINT_AGENT_PROMPT,
            allowed_tools=frozenset({"orders_lookup"}),
            required_capabilities=frozenset({"project_data"}),
            risk_ceiling=ToolSensitivity.PERSONAL, memory_scope="bp_orders",
        )
        return live, generated

    @staticmethod
    def _targets(delegate) -> tuple[str, ...]:
        [target] = [p for p in delegate.spec.parameters if p.name == "target_agent_id"]
        return target.enum

    async def test_a_blueprint_deployed_after_boot_becomes_a_target(self) -> None:
        live, generated = self._blueprint_registry()
        fake_kernel = _FakeKernel(_ok_result("from orders"))
        delegate, _ = _delegate_tool(fake_kernel, agents=live)
        assert "bp_orders" not in self._targets(delegate)

        live.swap([generated], version=1, digest="d1")

        assert "bp_orders" in self._targets(delegate)
        result = await delegate.execute(
            {"target_agent_id": "bp_orders", "subtask": "x"},
            _context(capabilities=("project_data",)),
        )
        assert result == "from orders"
        assert fake_kernel.received_request.agent == "bp_orders"
        assert fake_kernel.received_request.delegated_tool_grant == frozenset({"orders_lookup"})

    async def test_a_reset_blueprint_stops_being_a_target(self) -> None:
        live, generated = self._blueprint_registry()
        delegate, _ = _delegate_tool(_FakeKernel(_ok_result()), agents=live)
        live.swap([generated], version=1, digest="d1")
        live.reset()
        assert "bp_orders" not in self._targets(delegate)

    async def test_the_registry_validates_against_the_live_targets(self, container) -> None:
        """Through the real ToolRegistry: argument validation reads the spec at call time, so a
        blueprint agent swapped in after boot passes the schema check rather than being refused
        as 'must be one of' the boot-time list."""
        from aura_core.adaptation.runtime import ActiveBlueprint

        live, generated = self._blueprint_registry()
        fake_kernel = _FakeKernel(_ok_result("ok"))
        delegate, _ = _delegate_tool(fake_kernel, agents=live)
        live.swap([generated], version=1, digest="d1")
        assert isinstance(live, ActiveBlueprint)
        container.tools.register(delegate, overwrite=True)
        result, _ = await container.tools.execute(
            ToolCall(id=ToolCallId("t1"), name="delegate_to_agent",
                     arguments={"target_agent_id": "bp_orders", "subtask": "x"}),
            _context(capabilities=("project_data",)),
            capabilities=("project_data",),
        )
        assert result.ok, result.content
        assert fake_kernel.received_request.agent == "bp_orders"

    async def test_an_unknown_target_is_refused_by_name(self) -> None:
        """Called directly (no schema check in front), an unknown id must not reach
        AgentRegistry.resolve(), whose fallback would turn it into an orchestrator sub-turn."""
        fake_kernel = _FakeKernel(_ok_result())
        delegate, _ = _delegate_tool(fake_kernel)
        with pytest.raises(ToolExecutionError, match="unknown agent"):
            await delegate.execute(
                {"target_agent_id": "no_such_agent", "subtask": "x"}, _context(),
            )
        assert fake_kernel.received_request is None


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
