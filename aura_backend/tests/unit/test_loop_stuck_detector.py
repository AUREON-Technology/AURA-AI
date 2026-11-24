"""Stuck detection: a loop that is going in circles stops, and says why (ADR-037; row 43).

Before this the only stops were hard bounds, so an identical failing plan ran nine times with eight
replans. The detector is exercised three ways: as a pure object; inside the agent loop with a
scripted executor; and end to end through ``AIKernel`` with a real engineer turn (real tools, real
planner, real loop), asserting the pass count with the detector on against off.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.memory_stores import InMemoryEngineerRunStore
from aura_core.domain.agent_runs import AgentRunStatus
from aura_core.domain.identifiers import AgentRunId, PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.engine import ExecutionOutcome, StepEvent
from aura_core.execution.loop_events import LoopEvent
from aura_core.execution.run_recorder import AgentRunContext
from aura_core.execution.stuck import (
    CallRecord,
    StuckConfig,
    StuckDetector,
    digest,
    escalation_message,
)
from aura_core.ports.runtime import ToolContext
from tests.unit._loop_harness import (
    collect_stream,
    engineer_turn,
    frames_of,
    make_container,
    ready_engineer,
    script_model,
    tool_step,
)


def _call(tool="t", args="a", ok=False, kind="boom", result="r") -> CallRecord:
    return CallRecord(tool=tool, args_digest=args, ok=ok, error_kind=None if ok else kind,
                      result_digest=result)


class TestDetector:
    def test_identical_failing_calls_trip_at_the_threshold(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=3, same_error=0, no_progress=0))
        assert d.record_pass([_call()]) is None
        assert d.record_pass([_call()]) is None
        verdict = d.record_pass([_call()])
        assert verdict is not None and verdict.rule == "identical_calls" and verdict.count == 3
        assert verdict.tool == "t" and "failed the same way" in verdict.detail

    def test_identical_successful_results_count_too(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=2, same_error=0, no_progress=0))
        d.record_pass([_call(ok=True)])
        verdict = d.record_pass([_call(ok=True)])
        assert verdict is not None and "returned the same result" in verdict.detail

    def test_a_changed_outcome_is_movement_and_resets_the_count(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=3, same_error=0, no_progress=0))
        for result in ("a", "b", "c", "d", "e"):
            assert d.record_pass([_call(result=result)]) is None, "each rerun told us something new"

    def test_different_arguments_are_different_calls(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=2, same_error=0, no_progress=0))
        for args in ("1", "2", "3", "4"):
            assert d.record_pass([_call(args=args)]) is None

    def test_the_same_error_kind_in_consecutive_passes(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=0, same_error=3, no_progress=0))
        assert d.record_pass([_call(args="1", kind="denied")]) is None
        assert d.record_pass([_call(args="2", kind="denied")]) is None
        verdict = d.record_pass([_call(args="3", kind="denied")])
        assert verdict is not None and verdict.rule == "repeated_error" and verdict.error_kind == "denied"

    def test_an_intervening_success_breaks_the_error_streak(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=0, same_error=3, no_progress=0))
        d.record_pass([_call(args="1", kind="x")])
        d.record_pass([_call(args="2", kind="x")])
        d.record_pass([_call(args="3", ok=True)])
        assert d.record_pass([_call(args="4", kind="x")]) is None

    def test_failure_signals_count_as_errors(self) -> None:
        """A failing-test signature repeated: the tool calls all 'succeeded', the work did not."""
        d = StuckDetector(StuckConfig(identical_calls=0, same_error=2, no_progress=0))
        d.record_pass([_call(args="1", ok=True)], failure_signals=["tests_failed:abc"])
        verdict = d.record_pass([_call(args="2", ok=True)], failure_signals=["tests_failed:abc"])
        assert verdict is not None and verdict.error_kind == "tests_failed:abc"

    def test_no_new_success_across_passes(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=0, same_error=0, no_progress=3))
        assert d.record_pass([_call(args="1", kind="a")], new_success=False) is None
        assert d.record_pass([_call(args="2", kind="b")], new_success=False) is None
        verdict = d.record_pass([_call(args="3", kind="c")], new_success=False)
        assert verdict is not None and verdict.rule == "no_progress" and verdict.count == 3

    def test_progress_resets_the_idle_count(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=0, same_error=0, no_progress=2))
        d.record_pass([], new_success=False)
        d.record_pass([], new_success=True)
        assert d.record_pass([], new_success=False) is None

    def test_a_zero_threshold_disables_just_that_rule(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=0, same_error=0, no_progress=0))
        for _ in range(20):
            assert d.record_pass([_call()], new_success=False) is None

    def test_a_disabled_detector_never_trips(self) -> None:
        d = StuckDetector(StuckConfig.disabled())
        for _ in range(20):
            assert d.record_pass([_call()], new_success=False) is None

    def test_the_escalation_names_what_repeated_and_what_to_try(self) -> None:
        d = StuckDetector(StuckConfig(identical_calls=2))
        d.record_pass([_call(tool="edit_file")])
        text = escalation_message(d.record_pass([_call(tool="edit_file")]))
        assert "edit_file" in text and "going in circles" in text and "different arguments" in text

    def test_digests_are_stable_and_order_insensitive(self) -> None:
        assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1}) != digest({"a": 1, "b": 3})


# -- inside the loop -----------------------------------------------------------------------------


class _Planner:
    def __init__(self) -> None:
        self.replan_calls = 0

    def can_replan(self, plan, *, limit=None) -> bool:
        return len(plan.revisions) < (limit if limit is not None else 2)

    async def replan(self, plan, *, reason, available_tools=(), limit=None):
        """Like the real one: the unfinished remainder comes back as fresh PENDING steps."""
        self.replan_calls += 1
        fresh = [
            PlanStep(id=s.id, kind=s.kind, tool=s.tool, description=s.description,
                     arguments=dict(s.arguments))
            if s.status is not StepStatus.SUCCEEDED else s
            for s in plan.steps
        ]
        return plan.revised(steps=fresh, reason=reason)


class _Executor:
    """Dispatches each pending tool step; ``script(call_number)`` -> (ok, text, error_kind)."""

    def __init__(self, script) -> None:
        self.script, self.calls = script, 0

    async def execute(self, plan, *, context, capabilities=(), confirmations=None, on_event=None):
        self.calls += 1
        steps, results = [], {}
        for s in plan.steps:
            if s.status is not StepStatus.PENDING:
                steps.append(s)
                continue
            ok, text, kind = self.script(self.calls)
            started = s.started()
            if ok:
                steps.append(started.succeeded(text, 1.0))
                results[s.id.value] = text
            else:
                steps.append(started.failed(kind, 1.0))
        new = plan.with_steps(steps)
        return ExecutionOutcome(plan=new, results=results, succeeded=new.succeeded,
                                failure_messages={s.id.value: "msg" for s in new.failed_steps})

    async def stream(self, plan, *, context, capabilities=(), confirmations=None):
        yield StepEvent(step_id="s1", kind="tool", status="running", description="x")
        yield await self.execute(plan, context=context)


def _plan() -> Plan:
    step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="edit_file", description="edit it",
                    arguments={"path": "a.py"})
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(step,))


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)


class TestInTheLoop:
    async def test_an_identical_failing_plan_stops_after_three_passes_not_nine(self, ctx) -> None:
        planner, executor = _Planner(), _Executor(lambda n: (False, "", "tool_execution_error"))
        agentic = AgenticExecutor(planner=planner, executor=executor, stuck=StuckConfig())
        outcome = await agentic.run(_plan(), context=ctx, max_iterations=8, deadline_s=60.0)
        assert executor.calls == 3 and planner.replan_calls == 2
        assert outcome.loop.stop_reason == "stuck"
        assert outcome.loop.stuck["rule"] == "identical_calls"
        assert outcome.loop.trace[-1] == "stuck"
        assert "edit_file" in outcome.loop.escalation and "different arguments" in outcome.loop.escalation

    async def test_without_a_detector_the_loop_runs_to_its_limit_as_before(self, ctx) -> None:
        planner, executor = _Planner(), _Executor(lambda n: (False, "", "tool_execution_error"))
        agentic = AgenticExecutor(planner=planner, executor=executor)  # default: no detector
        outcome = await agentic.run(_plan(), context=ctx, max_iterations=8, deadline_s=60.0)
        assert executor.calls == 9 and planner.replan_calls == 8
        assert outcome.loop.stop_reason == "iteration_budget_exhausted"

    async def test_a_loop_that_is_making_progress_is_left_alone(self, ctx) -> None:
        """Different output each pass, then success: nothing here is 'the same'."""
        executor = _Executor(lambda n: (n >= 4, f"result {n}", f"kind{n}"))
        agentic = AgenticExecutor(planner=_Planner(), executor=executor, stuck=StuckConfig())
        outcome = await agentic.run(_plan(), context=ctx, max_iterations=8, deadline_s=60.0)
        assert outcome.succeeded and outcome.loop.stop_reason == "completed" and executor.calls == 4

    async def test_it_never_acts_on_a_pass_that_succeeded(self, ctx) -> None:
        executor = _Executor(lambda n: (True, "same", ""))
        agentic = AgenticExecutor(planner=_Planner(), executor=executor, stuck=StuckConfig(identical_calls=1))
        assert (await agentic.run(_plan(), context=ctx, max_iterations=8, deadline_s=60.0)).succeeded

    async def test_a_single_pass_persona_is_untouched(self, ctx) -> None:
        executor = _Executor(lambda n: (False, "", "x"))
        agentic = AgenticExecutor(planner=_Planner(), executor=executor, stuck=StuckConfig(identical_calls=1))
        outcome = await agentic.run(_plan(), context=ctx, max_iterations=0, deadline_s=60.0)
        assert outcome.loop.stop_reason == "single_pass" and executor.calls == 1

    async def test_the_stream_carries_a_stuck_frame_the_client_can_show(self, ctx) -> None:
        executor = _Executor(lambda n: (False, "", "boom"))
        agentic = AgenticExecutor(planner=_Planner(), executor=executor, stuck=StuckConfig())
        items = [i async for i in agentic.stream(_plan(), context=ctx, max_iterations=8, deadline_s=60.0)]
        frames = [i.as_frame() for i in items if isinstance(i, LoopEvent)]
        stuck = [f for f in frames if f["type"] == "stuck"]
        assert len(stuck) == 1
        assert stuck[0]["rule"] == "identical_calls" and stuck[0]["tool"] == "edit_file"
        assert stuck[0]["escalation"].startswith("I stopped because")
        assert isinstance(items[-1], ExecutionOutcome), "the outcome still comes last"

    async def test_the_run_record_says_stuck(self, ctx) -> None:
        store = InMemoryEngineerRunStore()
        agentic = AgenticExecutor(planner=_Planner(), executor=_Executor(lambda n: (False, "", "boom")),
                                  runs=store, stuck=StuckConfig())
        run_ctx = AgentRunContext(user_id=UserId("u"), agent_id="engineer", turn_id="t",
                                  correlation_id="c", goal="g")
        outcome = await agentic.run(_plan(), context=ctx, max_iterations=8, deadline_s=60.0,
                                    run_context=run_ctx)
        run = await store.get_run(AgentRunId(outcome.run_id), user_id=UserId("u"))
        assert run.status == AgentRunStatus.STUCK and run.stop_reason == "stuck"
        assert run.iterations == 3

    async def test_same_error_rule_alone_catches_a_loop_whose_calls_differ(self, ctx) -> None:
        """Different arguments every pass, same failure every pass."""

        class Varying(_Executor):
            async def execute(self, plan, **kw):
                self.calls += 1
                steps = tuple(
                    s.started().failed("denied_by_remote", 1.0) if s.status is StepStatus.PENDING else s
                    for s in plan.steps
                )
                return ExecutionOutcome(plan=plan.with_steps(steps), results={}, succeeded=False)

        class Replanner(_Planner):
            async def replan(self, plan, *, reason, available_tools=(), limit=None):
                self.replan_calls += 1
                step = PlanStep(id=StepId(f"s{self.replan_calls + 1}"), kind=StepKind.TOOL,
                                tool="edit_file", description="again",
                                arguments={"path": f"{self.replan_calls}.py"})
                return plan.revised(steps=[step], reason=reason)

        executor = Varying(None)
        agentic = AgenticExecutor(
            planner=Replanner(), executor=executor,
            stuck=StuckConfig(identical_calls=0, same_error=3, no_progress=0),
        )
        outcome = await agentic.run(_plan(), context=ctx, max_iterations=8, deadline_s=60.0)
        assert executor.calls == 3 and outcome.loop.stuck["rule"] == "repeated_error"


# -- end to end: the real kernel, tools, planner and loop -----------------------------------------


class TestEndToEnd:
    """An Engineer turn whose plan fails identically every time (``edit_file`` on a file that does
    not exist). Real registry and policy; the model is the only scripted part."""

    FAILING_PLAN = [tool_step("s1", "edit_file", "Fix the bug", {"path": "missing.py", "find": "a", "replace": "b"})]

    async def _turn(self, monkeypatch, **env):
        # The critic is switched off here: it would (rightly) ask for a read before this edit and
        # spend a planning call doing so, which is its own test's business.
        # The recovery policy (on by default) escalates an unrecognised failure after two passes,
        # before this detector's threshold of three. These tests are about the detector, so the
        # policy is off here; test_agent_recovery_defaults.py runs the same turn with it on.
        env.setdefault("LOOP_RECOVERY_POLICY_ENABLED", "false")
        container = make_container(monkeypatch, CRITIC_ENABLED="false", LLM_MAX_PROMPT_TOKENS="8000",
                                   **env)
        echo = await ready_engineer(container)
        script = script_model(echo, [self.FAILING_PLAN], final_text="I could not fix it.")
        frames = await collect_stream(container, engineer_turn("fix missing.py"))
        return container, script, frames

    async def test_the_detector_stops_the_real_loop_after_three_passes(self, monkeypatch) -> None:
        container, script, frames = await self._turn(monkeypatch)
        done = frames_of(frames, "done")[0]
        assert script.planning_calls == 3, "one plan and two replans, not one plan and eight"
        assert done["agent_loop"]["stop_reason"] == "stuck"
        stuck = frames_of(frames, "stuck")
        assert len(stuck) == 1 and stuck[0]["tool"] == "edit_file"
        text = "".join(f["text"] for f in frames_of(frames, "token"))
        assert "I stopped because I was going in circles" in text, "the user is told, in the answer"
        system = container.router.get("echo").last_system_prompt()
        assert "repeating itself" in system and "Do not say the task succeeded" in system, (
            "and the model is told, on the system channel, not to claim success"
        )

    async def test_switched_off_the_same_turn_runs_nine_passes(self, monkeypatch) -> None:
        _, script, frames = await self._turn(monkeypatch, LOOP_STUCK_DETECTION_ENABLED="false")
        done = frames_of(frames, "done")[0]
        assert script.planning_calls == 9
        assert done["agent_loop"]["stop_reason"] == "iteration_budget_exhausted"
        assert not frames_of(frames, "stuck")

    async def test_the_threshold_is_configurable(self, monkeypatch) -> None:
        _, script, _ = await self._turn(monkeypatch, LOOP_STUCK_IDENTICAL_CALLS="2",
                                        LOOP_STUCK_SAME_ERROR="0", LOOP_STUCK_NO_PROGRESS="0")
        assert script.planning_calls == 2

    async def test_the_run_is_recorded_as_stuck_through_the_container(self, monkeypatch) -> None:
        container, _, frames = await self._turn(monkeypatch)
        run_id = frames_of(frames, "done")[0]["agent_run_id"]
        from tests.unit._loop_harness import ENGINEER

        run = await container.agent_runs.get_run(AgentRunId(run_id), user_id=ENGINEER)
        assert run is not None and run.status == AgentRunStatus.STUCK and run.iterations == 3
