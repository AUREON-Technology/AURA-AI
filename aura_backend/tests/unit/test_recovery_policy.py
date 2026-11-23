"""``RecoveryPolicy``: failures are handled by kind, not all retried alike (ADR-037; row 44).

Three layers, because the claim is about behaviour: the pure table; the *real* execution engine
(real registry, real policy, real tools that fail in each way) deciding whether a failed step is
re-dispatched; and the agent loop deciding whether a failed pass is replanned, surfaced or stopped.
"""

from __future__ import annotations

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.domain.tools import ToolSensitivity
from aura_core.execution import ExecutionEngine
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.engine import ExecutionOutcome
from aura_core.execution.recovery import (
    FailureClass,
    RecoveryAction,
    RecoveryPolicy,
    classify_failure,
)
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import param, tool
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry


class TestClassification:
    @pytest.mark.parametrize("kind,expected", [
        ("timeout", FailureClass.TRANSIENT), ("tool_timeout", FailureClass.TRANSIENT),
        ("rate_limited", FailureClass.TRANSIENT), ("dependency_unavailable", FailureClass.TRANSIENT),
        ("ConnectionError", FailureClass.TRANSIENT),
        ("validation_error", FailureClass.VALIDATION),
        ("unresolved_argument", FailureClass.VALIDATION),
        ("unresolved_reference", FailureClass.VALIDATION),
        ("tool_not_found", FailureClass.VALIDATION),
        ("policy_denied", FailureClass.DENIED), ("confirmation_required", FailureClass.DENIED),
        ("consent_required", FailureClass.DENIED),
        ("output_withheld", FailureClass.SECURITY), ("safety_violation", FailureClass.SECURITY),
        ("tool_execution_error", FailureClass.UNKNOWN), ("", FailureClass.UNKNOWN),
        (None, FailureClass.UNKNOWN), ("weird_new_kind", FailureClass.UNKNOWN),
    ])
    def test_kinds(self, kind, expected) -> None:
        assert classify_failure(kind) is expected

    def test_a_generic_kind_is_upgraded_by_what_the_message_says(self) -> None:
        """`tool_execution_error` wraps whatever a tool raised; the transport error inside it is
        still transient."""
        assert classify_failure("tool_execution_error", "upstream timed out after 30s") is FailureClass.TRANSIENT
        assert classify_failure("tool_execution_error", "HTTP 429 too many requests") is FailureClass.TRANSIENT
        assert classify_failure("tool_execution_error", "Invalid arguments for 'x'") is FailureClass.VALIDATION
        assert classify_failure("tool_execution_error", "disk exploded") is FailureClass.UNKNOWN

    def test_free_text_error_kinds_are_handled(self) -> None:
        """When a tool reports no kind the engine records the first characters of its message."""
        assert classify_failure("The 'x' tool timed out after 15s.") is FailureClass.TRANSIENT


class TestDecisions:
    def setup_method(self) -> None:
        self.policy = RecoveryPolicy(backoff_base_s=0.5, backoff_cap_s=4.0)

    def test_transient_read_only_retries_with_growing_backoff_up_to_the_ceiling(self) -> None:
        first = self.policy.decide("timeout", attempt=1, max_attempts=3)
        second = self.policy.decide("timeout", attempt=2, max_attempts=3)
        assert first.action is RecoveryAction.RETRY and first.backoff_s == 0.5
        assert second.action is RecoveryAction.RETRY and second.backoff_s == 1.0
        # ceiling reached: the engine's max_attempts still binds
        last = self.policy.decide("timeout", attempt=3, max_attempts=3)
        assert last.action is RecoveryAction.REPLAN

    def test_backoff_is_capped(self) -> None:
        assert self.policy.backoff_for(10) == 4.0

    def test_transient_side_effecting_is_replanned_never_repeated(self) -> None:
        decision = self.policy.decide("timeout", attempt=1, max_attempts=3, side_effecting=True)
        assert decision.action is RecoveryAction.REPLAN and not decision.retry

    def test_validation_is_replanned_not_retried(self) -> None:
        decision = self.policy.decide("validation_error", attempt=1, max_attempts=3)
        assert decision.action is RecoveryAction.REPLAN and not decision.retry

    def test_denial_is_surfaced(self) -> None:
        for kind in ("policy_denied", "confirmation_required"):
            assert self.policy.decide(kind, attempt=1, max_attempts=3).action is RecoveryAction.SURFACE

    def test_security_stops(self) -> None:
        assert self.policy.decide("output_withheld", attempt=1, max_attempts=3).action is RecoveryAction.STOP

    def test_unknown_replans_once_then_escalates(self) -> None:
        assert self.policy.decide("odd", attempt=1, max_attempts=3).action is RecoveryAction.REPLAN
        again = self.policy.decide("odd", attempt=1, max_attempts=3, prior_replans_for_kind=1)
        assert again.action is RecoveryAction.ESCALATE

    def test_every_decision_explains_itself(self) -> None:
        for kind in ("timeout", "validation_error", "policy_denied", "output_withheld", "odd"):
            assert self.policy.decide(kind, attempt=1, max_attempts=2).reason


# -- the real engine -----------------------------------------------------------------------------


class _Probe:
    """Counts how often a tool body ran, and fails on a schedule."""

    def __init__(self, *fail_with: BaseException | None) -> None:
        self.schedule = list(fail_with)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        outcome = self.schedule.pop(0) if self.schedule else None
        if outcome is not None:
            raise outcome
        return "ok"


def _registry(probe: _Probe, *, side_effecting: bool = False, capability: str | None = None) -> ToolRegistry:
    @tool("probe", "A tool that fails on a schedule.", category=None, sensitivity=ToolSensitivity.PUBLIC,
          side_effecting=side_effecting, required_capability=capability)
    async def probe_tool() -> str:
        return await probe()

    registry = ToolRegistry(policy=ToolPolicy())
    registry.register(probe_tool)
    return registry


@tool("needs_int", "Takes an integer.", param("n", "integer", "a number"), sensitivity=ToolSensitivity.PUBLIC)
async def _needs_int(n: int) -> str:
    return str(n)


def _plan(tool_name: str = "probe", arguments=None, max_attempts: int = 2) -> Plan:
    step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool=tool_name, description="probe it",
                    arguments=arguments or {}, max_attempts=max_attempts)
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(step,))


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)


class _Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


async def _run(engine: ExecutionEngine, plan: Plan, ctx: ToolContext, caps=()) -> ExecutionOutcome:
    return await engine.execute(plan, context=ctx, capabilities=caps)


class TestEngineRetries:
    async def test_without_a_policy_every_failed_read_is_retried_as_before(self, ctx) -> None:
        probe = _Probe(ToolExecutionError("disk exploded", tool="probe"), ToolExecutionError("disk exploded", tool="probe"))
        engine = ExecutionEngine(tools=_registry(probe))
        outcome = await _run(engine, _plan(), ctx)
        assert probe.calls == 2 and not outcome.succeeded  # the original rule: retry once, then fail

    async def test_a_transient_failure_is_retried_after_a_backoff_and_recovers(self, ctx) -> None:
        probe = _Probe(ToolExecutionError("upstream timed out", tool="probe"))
        sleeps = _Sleeps()
        engine = ExecutionEngine(tools=_registry(probe), recovery=RecoveryPolicy(backoff_base_s=0.5),
                                 sleeper=sleeps)
        outcome = await _run(engine, _plan(), ctx)
        assert outcome.succeeded and probe.calls == 2
        assert sleeps.waits == [0.5], "the retry waited exactly one backoff"

    async def test_an_unrecognised_failure_is_not_blindly_retried(self, ctx) -> None:
        probe = _Probe(ToolExecutionError("disk exploded", tool="probe"), ToolExecutionError("disk exploded", tool="probe"))
        sleeps = _Sleeps()
        engine = ExecutionEngine(tools=_registry(probe), recovery=RecoveryPolicy(), sleeper=sleeps)
        outcome = await _run(engine, _plan(), ctx)
        assert probe.calls == 1, "unknown -> replan, not a second identical dispatch"
        assert outcome.plan.failed_steps and sleeps.waits == []

    async def test_an_invalid_call_is_not_re_dispatched(self, ctx) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(_needs_int)
        bad = _plan("needs_int", {"n": "not-a-number"})
        old = await _run(ExecutionEngine(tools=registry), bad, ctx)
        new = await _run(ExecutionEngine(tools=registry, recovery=RecoveryPolicy()), bad, ctx)
        assert old.plan.failed_steps[0].attempts == 2, "the original rule dispatched it twice"
        failed = new.plan.failed_steps[0]
        assert failed.attempts == 1 and failed.error_kind == "validation_error"
        assert "Invalid arguments" in new.failure_messages["s1"], "the validator's message is kept for the replan"

    async def test_a_policy_denial_is_not_re_dispatched(self, ctx) -> None:
        registry = _registry(_Probe(), capability="needs_this_grant")
        old = await _run(ExecutionEngine(tools=registry), _plan(), ctx, caps=())
        new = await _run(ExecutionEngine(tools=registry, recovery=RecoveryPolicy()), _plan(), ctx, caps=())
        assert old.plan.failed_steps[0].attempts == 2
        assert new.plan.failed_steps[0].attempts == 1
        assert new.plan.failed_steps[0].error_kind == "policy_denied"

    async def test_a_side_effecting_step_is_never_retried_in_place(self, ctx) -> None:
        probe = _Probe(ToolExecutionError("upstream timed out", tool="probe"))
        engine = ExecutionEngine(tools=_registry(probe, side_effecting=True),
                                 recovery=RecoveryPolicy(), sleeper=_Sleeps())
        outcome = await _run(engine, _plan(), ctx)
        assert probe.calls == 1 and outcome.plan.failed_steps, "it may have acted before it failed"

    async def test_max_attempts_stays_the_ceiling(self, ctx) -> None:
        probe = _Probe(*[ToolExecutionError("upstream timed out", tool="probe")] * 5)
        sleeps = _Sleeps()
        engine = ExecutionEngine(tools=_registry(probe), recovery=RecoveryPolicy(), sleeper=sleeps)
        outcome = await _run(engine, _plan(max_attempts=3), ctx)
        assert probe.calls == 3 and not outcome.succeeded
        assert sleeps.waits == [0.5, 1.0], "backoff doubles between the attempts that remain"


# -- the loop ------------------------------------------------------------------------------------


class _Planner:
    def __init__(self, max_replans: int = 8) -> None:
        self.max_replans = max_replans
        self.replan_calls = 0
        self.feedback: list[list[str]] = []

    def can_replan(self, plan: Plan, *, limit: int | None = None) -> bool:
        return len(plan.revisions) < (self.max_replans if limit is None else limit)

    async def replan(self, plan, *, reason, available_tools=(), limit=None, feedback=()):
        self.replan_calls += 1
        self.feedback.append(list(feedback))
        return plan.revised(steps=list(plan.steps), reason=reason)


class _FailingExecutor:
    """Fails the plan's tool step with a given kind and message on every pass."""

    def __init__(self, error_kind: str, message: str = "") -> None:
        self.error_kind, self.message, self.calls = error_kind, message, 0

    async def execute(self, plan, *, context, capabilities=(), confirmations=None, on_event=None):
        self.calls += 1
        steps = tuple(
            s.started().failed(self.error_kind, 1.0) if s.status is StepStatus.PENDING else s
            for s in plan.steps
        )
        return ExecutionOutcome(
            plan=plan.with_steps(steps), results={}, succeeded=False,
            failure_messages={s.id.value: self.message for s in steps},
        )


def _tool_plan() -> Plan:
    step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="probe", description="probe it")
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(step,))


async def _drive(executor, planner, *, max_iterations: int = 8, **loop_kwargs):
    agentic = AgenticExecutor(planner=planner, executor=executor, **loop_kwargs)
    ctx = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)
    return await agentic.run(_tool_plan(), context=ctx, max_iterations=max_iterations, deadline_s=60.0)


class TestLoopRecovery:
    async def test_without_a_policy_the_loop_replans_to_its_limit_as_before(self) -> None:
        planner = _Planner()
        outcome = await _drive(_FailingExecutor("policy_denied"), planner, max_iterations=4)
        assert planner.replan_calls == 4 and outcome.loop.stop_reason == "iteration_budget_exhausted"

    async def test_a_policy_denial_is_surfaced_not_replanned_around(self) -> None:
        planner, executor = _Planner(), _FailingExecutor("policy_denied")
        outcome = await _drive(executor, planner, recovery=RecoveryPolicy())
        assert planner.replan_calls == 0 and executor.calls == 1
        assert outcome.loop.stop_reason == "not_retried:policy_denied"
        assert "refused by policy" in outcome.loop.escalation

    async def test_a_withheld_result_stops_the_run(self) -> None:
        planner, executor = _Planner(), _FailingExecutor("output_withheld")
        outcome = await _drive(executor, planner, recovery=RecoveryPolicy())
        assert planner.replan_calls == 0 and executor.calls == 1
        assert outcome.loop.stop_reason == "unrecoverable:output_withheld"
        assert "safety guard" in outcome.loop.escalation

    async def test_validation_failures_replan_with_the_validators_message(self) -> None:
        planner = _Planner()
        message = "Invalid arguments for 'probe': n must be an integer"
        await _drive(_FailingExecutor("validation_error", message), planner,
                     max_iterations=1, recovery=RecoveryPolicy())
        assert planner.replan_calls == 1
        assert any(message in line for line in planner.feedback[0]), planner.feedback

    async def test_an_unknown_failure_replans_once_then_escalates(self) -> None:
        planner, executor = _Planner(), _FailingExecutor("mystery_kind")
        outcome = await _drive(executor, planner, recovery=RecoveryPolicy())
        assert planner.replan_calls == 1 and executor.calls == 2
        assert outcome.loop.stop_reason == "escalated:mystery_kind"
        assert "same unexpected failure" in outcome.loop.escalation

    async def test_a_transient_failure_that_outlasts_its_retries_replans(self) -> None:
        planner = _Planner()
        outcome = await _drive(_FailingExecutor("timeout"), planner, max_iterations=2,
                               recovery=RecoveryPolicy())
        assert planner.replan_calls == 2 and outcome.loop.stop_reason == "iteration_budget_exhausted"

    async def test_a_refusal_mixed_with_a_repairable_failure_still_replans(self) -> None:
        """Only an *all refusals* pass is surfaced; a pass that also has a fixable failure replans,
        with the refusal named so the planner does not repeat it."""

        class Mixed(_FailingExecutor):
            async def execute(self, plan, *, context, capabilities=(), confirmations=None, on_event=None):
                self.calls += 1
                kinds = iter(["policy_denied", "validation_error"])
                steps = tuple(
                    s.started().failed(next(kinds), 1.0) if s.status is StepStatus.PENDING else s
                    for s in plan.steps
                )
                return ExecutionOutcome(plan=plan.with_steps(steps), results={}, succeeded=False,
                                        failure_messages={s.id.value: "m" for s in steps})

        a = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="probe", description="a")
        b = PlanStep(id=StepId("s2"), kind=StepKind.TOOL, tool="probe", description="b")
        plan = Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(a, b))
        planner = _Planner()
        agentic = AgenticExecutor(planner=planner, executor=Mixed("x"), recovery=RecoveryPolicy())
        ctx = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)
        await agentic.run(plan, context=ctx, max_iterations=1, deadline_s=60.0)
        assert planner.replan_calls == 1
        assert any("refused" in line for line in planner.feedback[0])
