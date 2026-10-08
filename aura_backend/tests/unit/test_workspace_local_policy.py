"""What `workspace_local` actually changes in `ToolPolicy.evaluate`, in both directions.

The exemption is only defensible if it is narrow, so these test the boundary from both sides:
work inside a disposable workspace proceeds, and anything reaching past it still stops for a
human. The second half is the part that would make this change a mistake if it broke.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import UserId
from aura_core.domain.tools import ToolCategory, ToolSensitivity, ToolSpec
from aura_core.ports.runtime import ToolContext
from aura_core.tools.policy import ToolPolicy


def _spec(name: str, **kwargs) -> ToolSpec:
    defaults = dict(
        description="x",
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
    )
    defaults.update(kwargs)
    return ToolSpec(name=name, **defaults)


def _context(*, tainted: bool = False, **kwargs) -> ToolContext:
    return ToolContext(
        user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0, tainted=tainted, **kwargs
    )


@pytest.fixture
def policy() -> ToolPolicy:
    return ToolPolicy()


class TestInsideTheWorkspace:
    def test_a_tainted_workspace_local_tool_runs(self, policy) -> None:
        """The confirmation storm this exists to prevent: one cloned repository taints the turn,
        and without the exemption every command after it needs approval."""
        decision = policy.evaluate(
            _spec("run_command", workspace_local=True), context=_context(tainted=True)
        )

        assert decision.allowed
        assert decision.reason == "workspace_local_side_effect"

    def test_a_system_sensitivity_workspace_local_tool_needs_no_confirmation(self, policy) -> None:
        """SYSTEM + side_effecting normally forces requires_confirmation at construction time.
        run_command is exactly that combination, and is called at every step of a task."""
        spec = _spec("run_command", sensitivity=ToolSensitivity.SYSTEM, workspace_local=True)

        assert not spec.requires_confirmation
        assert policy.evaluate(spec, context=_context()).allowed

    def test_a_confidential_workspace_local_tool_needs_no_confirmation(self, policy) -> None:
        spec = _spec("x", sensitivity=ToolSensitivity.CONFIDENTIAL, workspace_local=True)

        assert policy.evaluate(spec, context=_context()).allowed

    def test_repeated_calls_stay_allowed(self, policy) -> None:
        """The point is a whole task's worth of steps, not one."""
        spec = _spec("run_command", sensitivity=ToolSensitivity.SYSTEM, workspace_local=True)
        context = _context(tainted=True)

        assert all(policy.evaluate(spec, context=context).allowed for _ in range(20))


class TestCrossingTheBoundary:
    def test_a_tainted_boundary_crossing_tool_still_confirms(self, policy) -> None:
        """SEC-02: a poisoned document must not be able to reach a write. Unchanged."""
        decision = policy.evaluate(_spec("git_push"), context=_context(tainted=True))

        assert decision.requires_confirmation
        assert decision.reason == "side_effect_in_tainted_context"

    def test_a_confidential_write_still_confirms(self, policy) -> None:
        decision = policy.evaluate(
            _spec("github_commit_files", sensitivity=ToolSensitivity.CONFIDENTIAL),
            context=_context(),
        )

        assert decision.requires_confirmation

    def test_a_system_write_still_confirms(self, policy) -> None:
        decision = policy.evaluate(
            _spec("git_push", sensitivity=ToolSensitivity.SYSTEM), context=_context()
        )

        assert decision.requires_confirmation

    def test_an_explicit_requires_confirmation_is_still_honoured(self, policy) -> None:
        decision = policy.evaluate(
            _spec("x", sensitivity=ToolSensitivity.PERSONAL, requires_confirmation=True),
            context=_context(),
        )

        assert decision.requires_confirmation


class TestTheExemptionIsNarrow:
    def test_it_does_not_bypass_the_capability_gate(self, policy) -> None:
        """Relaxing confirmation must not relax authorisation."""
        decision = policy.evaluate(
            _spec("run_command", workspace_local=True, required_capability="engineer_agent"),
            context=_context(),
            capabilities=[],
        )

        assert not decision.allowed
        assert "missing_capability" in decision.reason

    def test_it_does_not_bypass_the_agent_grant(self, policy) -> None:
        context = _context(allowed_tools=frozenset({"something_else"}))

        decision = policy.evaluate(_spec("run_command", workspace_local=True), context=context)

        assert not decision.allowed
        assert decision.reason == "outside_agent_grant"

    def test_it_does_not_bypass_the_denylist(self) -> None:
        policy = ToolPolicy(denylist=["run_command"])

        decision = policy.evaluate(
            _spec("run_command", workspace_local=True), context=_context()
        )

        assert not decision.allowed
        assert decision.reason == "denied_by_configuration"

    def test_it_does_not_bypass_the_sensitivity_ceiling(self, policy) -> None:
        context = _context(max_sensitivity=ToolSensitivity.PERSONAL)

        decision = policy.evaluate(
            _spec("run_command", sensitivity=ToolSensitivity.SYSTEM, workspace_local=True),
            context=context,
        )

        assert not decision.allowed
        assert decision.reason == "sensitivity_exceeds_policy"

    def test_a_read_only_workspace_local_tool_is_unaffected(self, policy) -> None:
        """The clause only fires for side-effecting tools; a read was already allowed."""
        decision = policy.evaluate(
            _spec("git_diff", side_effecting=False, workspace_local=True), context=_context()
        )

        assert decision.allowed
        assert decision.reason == "policy_ok"

    def test_unmarked_tools_behave_exactly_as_before(self, policy) -> None:
        """The regression that would matter most: this change must be invisible to every tool
        that does not set the flag."""
        spec = _spec("ingest_document", sensitivity=ToolSensitivity.CONFIDENTIAL)

        assert policy.evaluate(spec, context=_context()).requires_confirmation
        assert policy.evaluate(spec, context=_context(tainted=True)).requires_confirmation
        assert policy.evaluate(spec, context=_context(), confirmed=True).allowed
