"""The failure-recovery policy through a real Engineer kernel turn (ADR-039 / ADR-047, row 44).

``AURA_LOOP_RECOVERY_POLICY_ENABLED`` stays default OFF. These tests turn it on in a real
container and run real turns through the kernel -- real registry, tool policy, execution engine,
agentic loop and ``PlanningEngine.replan`` -- with only what "the model" plans scripted, and
assert what the user-visible run does: a transient failure is retried in place and recovers, an
invalid call is replanned with the validator's message and never dispatched twice, a refusal is
surfaced instead of worked around, and a permanent failure stops with an escalation instead of
burning the whole iteration budget. The same scenarios with the flag off show what changed.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import PlanId, SessionId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.domain.tools import ToolSensitivity
from aura_core.kernel import TurnRequest
from aura_core.tools import param, tool

USER = UserId("u-recovery")


class _Probe:
    """Counts dispatches of each tool and fails on a schedule."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.schedule: dict[str, list[BaseException | None]] = {}

    def hit(self, name: str) -> int:
        self.calls[name] = self.calls.get(name, 0) + 1
        return self.calls[name]


class _Script:
    """What the planner model writes: one plan per ``plan()`` call; the last one repeats."""

    def __init__(self, *plans: Plan) -> None:
        self.plans = list(plans)
        self.goals: list[str] = []

    async def plan(self, goal, available_tools=(), max_steps=None):  # noqa: ANN001
        self.goals.append(goal.description)
        return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]


def _step(step_id: str, name: str, arguments=None, *, depends_on: tuple[str, ...] = ()) -> PlanStep:
    return PlanStep(id=StepId(step_id), kind=StepKind.TOOL, tool=name, description=f"call {name}",
                    arguments=arguments or {}, depends_on=tuple(StepId(d) for d in depends_on))


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=tuple(steps))


async def _ready(script: _Script, probe: _Probe, *, policy: bool):
    base = load_settings(refresh=True, validate=False)
    settings = dataclasses.replace(base, reasoning=dataclasses.replace(
        base.reasoning, loop_recovery_policy_enabled=policy, loop_recovery_backoff_s=0.01,
    ))
    container = Container(settings)
    await container.startup()
    # The default container has no sandbox (no Docker, no paired desktop), and the kernel now hides the
    # sandbox-backed tools from a turn in that state (audit T-13). These turns exercise the Engineer loop
    # over tools that stand in for a deployment that has one, so give it the in-memory runner.
    from aura_core.adapters.coding_fakes import InMemorySandboxRunner

    container.sandbox_runner._fallback = InMemorySandboxRunner()
    await container.engineer_consent.accept(USER, ENGINEER_CONSENT_NOTICE_VERSION, at=datetime.now(timezone.utc))

    @tool("git_status", "A read that times out once, then works.", sensitivity=ToolSensitivity.PUBLIC)
    async def git_status() -> str:
        if probe.hit("git_status") == 1:
            raise ToolExecutionError("upstream timed out", tool="git_status")
        return "flaky data"

    @tool("git_diff", "A read that always fails in a way nobody recognises.",
          sensitivity=ToolSensitivity.PUBLIC)
    async def git_diff() -> str:
        probe.hit("git_diff")
        raise ToolExecutionError("the database exploded", tool="git_diff")

    @tool("calculator", "A read that needs an integer.", param("n", "integer", "a number"),
          sensitivity=ToolSensitivity.PUBLIC)
    async def calculator(n: int) -> str:
        probe.hit("calculator")
        return f"value {n}"

    @tool("list_workspace_snapshots", "A read the caller is not granted.", sensitivity=ToolSensitivity.PUBLIC,
          required_capability="locked_capability")
    async def list_workspace_snapshots() -> str:
        probe.hit("list_workspace_snapshots")
        return "secret"

    @tool("git_commit", "A write that times out once.", param("target", "string", "t"),
          side_effecting=True, sensitivity=ToolSensitivity.PUBLIC)
    async def git_commit(target: str) -> str:
        if probe.hit("git_commit") == 1:
            raise ToolExecutionError("upstream timed out", tool="git_commit")
        return f"wrote {target}"

    for t in (git_status, git_diff, calculator, list_workspace_snapshots, git_commit):
        container.tools.register(t, overwrite=True)
    # The real planner (heuristics, goal_from_message, replan with its allowance and renumbering);
    # only the model's plan is scripted.
    container.planner.plan = script.plan
    return container


async def _run(container, message: str = "check the upstream data"):
    return await container.kernel.run(TurnRequest(
        message=message, user_id=USER, session_id=SessionId("conv-recovery"), agent="engineer",
        capabilities=("engineer_agent",), plan=True,
    ))


class TestDefault:
    def test_the_policy_is_on_by_default(self) -> None:
        """Enabled by default since the "Enable agent recovery defaults" change: a failed read is
        classified before it is retried (see ``test_agent_recovery_defaults.py`` for the three
        behaviours that depends on, run with no flag set at all)."""
        assert load_settings(refresh=True, validate=False).reasoning.loop_recovery_policy_enabled is True


class TestTransient:
    async def test_a_transient_read_failure_is_retried_in_place_and_the_run_completes(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "git_status")))
        result = await _run(await _ready(script, probe, policy=True))

        assert probe.calls == {"git_status": 2}, "retried once after the timeout"
        assert result.telemetry.outcome.value == "success"
        assert result.agent_loop["stop_reason"] == "completed"
        assert len(script.goals) == 1, "no replan was needed: the retry recovered it"
        assert result.plan_summary[0]["status"] == "succeeded"
        assert result.agent_loop["escalation"] == ""

    async def test_a_transient_failure_of_a_side_effecting_step_is_replanned_never_repeated_in_place(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "git_commit", {"target": "x"})))
        result = await _run(await _ready(script, probe, policy=True), "write x")

        # A step that may already have acted is not run again in place: the loop re-plans (the
        # replan prompt carries the failure) and the *new* plan's call is what succeeds.
        assert probe.calls == {"git_commit": 2} and len(script.goals) == 2
        assert "Failed: call git_commit" in script.goals[1] and "upstream timed out" in script.goals[1]
        assert result.agent_loop["stop_reason"] == "completed"
        assert result.agent_loop["trace"][:5] == ["plan", "execute", "observe", "replan", "execute"]


class TestStream:
    async def test_the_streaming_path_recovers_and_escalates_the_same_way(self) -> None:
        def stream(container, message):
            return container.kernel.stream(TurnRequest(
                message=message, user_id=USER, session_id=SessionId("conv-recovery-stream"),
                agent="engineer", capabilities=("engineer_agent",), plan=True))

        probe = _Probe()
        container = await _ready(_Script(_plan(_step("s1", "git_status"))), probe, policy=True)
        done = [f async for f in stream(container, "check the data")][-1]
        assert probe.calls == {"git_status": 2} and done["agent_loop"]["stop_reason"] == "completed"

        probe2 = _Probe()
        container2 = await _ready(_Script(_plan(_step("s1", "git_diff"))), probe2, policy=True)
        frames = [f async for f in stream(container2, "read the broken thing")]
        assert frames[-1]["agent_loop"]["stop_reason"].startswith("escalated")
        assert probe2.calls == {"git_diff": 2}
        text = "".join(f.get("text", "") for f in frames if f["type"] == "token")
        assert frames[-1]["agent_loop"]["escalation"] in text


class TestValidation:
    async def test_an_invalid_call_is_replanned_with_the_validators_message_not_redispatched(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "calculator", {"n": "not-a-number"})),
                         _plan(_step("s1", "calculator", {"n": 7})))
        result = await _run(await _ready(script, probe, policy=True), "read the value")

        assert probe.calls == {"calculator": 1}, "the bad call was never dispatched; only the repaired one ran"
        assert result.telemetry.outcome.value == "success"
        assert result.agent_loop["stop_reason"] == "completed"
        # The revised plan was asked to act on what the validator said.
        assert len(script.goals) == 2
        assert "Failed: call calculator (validation_error)" in script.goals[1]
        assert "parameter 'n' must be integer, got 'not-a-number'" in script.goals[1]


class TestDenied:
    async def test_a_refusal_is_surfaced_not_retried_and_not_worked_around(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "list_workspace_snapshots")), _plan(_step("s1", "list_workspace_snapshots")))
        result = await _run(await _ready(script, probe, policy=True), "read the locked thing")

        assert probe.calls == {}, "policy refused the call; the tool body never ran"
        # The only replan is the loop's *pre-flight* one: the plan critic saw the tool is not
        # available to this caller. The refusal at execution is then surfaced, not planned around.
        assert len(script.goals) == 2 and "unknown_tool" in script.goals[1]
        assert result.agent_loop["stop_reason"] == "not_retried:policy_denied"
        assert "That is your call to make, not something to work around" in result.text


class TestPermanentFailure:
    async def test_an_unrecognised_failure_is_replanned_once_then_escalated(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "git_diff")))
        result = await _run(await _ready(script, probe, policy=True), "read the broken thing")

        assert str(result.agent_loop["stop_reason"]).startswith("escalated"), result.agent_loop["stop_reason"]
        assert probe.calls == {"git_diff": 2}, "the original pass and one replanned pass; no blind retries"
        assert len(script.goals) == 2
        assert result.agent_loop["escalation"], "the user is told what was tried"
        assert result.agent_loop["escalation"] in result.text, "...and the answer says so"

    async def test_the_same_run_without_the_policy_spends_its_budget_retrying(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "git_diff")))
        result = await _run(await _ready(script, probe, policy=False), "read the broken thing")

        assert probe.calls["git_diff"] > 2, "the old rule: retry every failed read, replan to the limit"
        assert not str(result.agent_loop["stop_reason"]).startswith(("escalated", "not_retried"))
