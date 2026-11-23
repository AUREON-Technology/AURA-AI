"""The agent loop's control flow as an explicit graph, with test results as an input (ADR-037;
row 14).

``run_command`` returns ``ok=True`` with the exit status only in its text, so the old loop saw "every
step succeeded" and stopped while the test suite it had just run was red. Here that is shown against
the real thing: a real ``pytest`` run, in a real directory, through the real kernel and loop.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.domain.tools import ToolSensitivity
from aura_core.execution import ExecutionEngine
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.state_graph import (
    TERMINAL_STATES,
    TRANSITIONS,
    IllegalTransition,
    LoopState,
    Signals,
    classify,
    transition,
)
from aura_core.planning import PlanningEngine
from aura_core.ports.runtime import ToolContext
from aura_core.prompts import build_default_registry
from aura_core.tools.base import param, tool
from aura_core.tools.coding import _command_result
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry
from tests.unit._loop_harness import (
    BUGGY,
    FIXED,
    TEST_MODULE,
    collect_stream,
    engineer_turn,
    frames_of,
    make_container,
    pytest_step,
    ready_engineer,
    script_model,
    write_step,
)


class TestTheGraph:
    def test_every_state_is_reachable_from_plan(self) -> None:
        seen, frontier = {LoopState.PLAN}, [LoopState.PLAN]
        while frontier:
            here = frontier.pop()
            for (src, _event), dst in TRANSITIONS.items():
                if src is here and dst not in seen:
                    seen.add(dst)
                    frontier.append(dst)
        assert seen == set(LoopState)

    def test_terminal_states_have_no_way_out(self) -> None:
        for (src, _event) in TRANSITIONS:
            assert src not in TERMINAL_STATES
        assert TERMINAL_STATES == {LoopState.DONE, LoopState.STUCK, LoopState.ESCALATE}

    def test_observe_can_reach_every_outcome_the_issue_names(self) -> None:
        outcomes = {dst for (src, _), dst in TRANSITIONS.items() if src is LoopState.OBSERVE}
        assert outcomes == {LoopState.VERIFY, LoopState.REPLAN, LoopState.STUCK, LoopState.ESCALATE}

    def test_an_edge_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(IllegalTransition):
            transition(LoopState.DONE, "replan")
        with pytest.raises(IllegalTransition):
            transition(LoopState.EXECUTE, "success")

    def test_the_documented_happy_path(self) -> None:
        state = LoopState.PLAN
        for event in ("planned", "executed", "success", "verified"):
            state = transition(state, event)
        assert state is LoopState.DONE


class TestClassify:
    """OBSERVE's decision, in the order the loop has always applied it."""

    BASE = dict(succeeded=False, repairable=True, max_iterations=8)

    def verdict(self, **overrides):
        return classify(Signals(**{**self.BASE, **overrides}))

    def test_a_clean_success_is_a_success(self) -> None:
        v = self.verdict(succeeded=True)
        assert (v.event, v.stop_reason) == ("success", "completed")

    def test_success_with_unresolved_tests_is_not_success(self) -> None:
        v = self.verdict(succeeded=True, unresolved_tests=True)
        assert v.event == "replan"

    def test_unresolved_tests_are_repairable_even_with_no_failed_step(self) -> None:
        v = self.verdict(succeeded=True, unresolved_tests=True, repairable=False)
        assert v.event == "replan"

    def test_precedence(self) -> None:
        terminal = frozenset({"needs_confirmation", "tool_budget_exceeded"})
        budgets = {"tool_budget_exceeded": "tools"}
        assert self.verdict(past_deadline=True).stop_reason == "time_budget_exhausted"
        v = self.verdict(blocked_reason="tool_budget_exceeded", terminal_blocks=terminal, block_budgets=budgets)
        assert (v.stop_reason, v.exhausted_budget) == ("tool_budget_exceeded", "tools")
        assert self.verdict(blocked_reason="needs_confirmation", terminal_blocks=terminal).event == "escalate"
        assert self.verdict(repairable=False).stop_reason == "nothing_to_repair"
        assert self.verdict(max_iterations=0).stop_reason == "single_pass"
        assert self.verdict(stuck=True).event == "stuck"
        assert self.verdict(tokens_exhausted=True).stop_reason == "token_budget_exhausted"
        assert self.verdict(can_replan=False).stop_reason == "iteration_budget_exhausted"
        assert self.verdict().event == "replan"

    def test_stuck_outranks_the_budgets_because_it_explains_more(self) -> None:
        v = self.verdict(stuck=True, tokens_exhausted=True, can_replan=False)
        assert v.event == "stuck"

    def test_recovery_stops_outrank_a_replan(self) -> None:
        assert self.verdict(recovery_stop="output_withheld").stop_reason == "unrecoverable:output_withheld"
        assert self.verdict(recovery_surface="policy_denied").stop_reason == "not_retried:policy_denied"
        assert self.verdict(recovery_escalate="x").stop_reason == "escalated:x"


# -- through the loop, with tools whose exit status is scripted -------------------------------------


@tool("run_command", "Run a command.", param("command", "string", "c"),
      sensitivity=ToolSensitivity.PUBLIC, side_effecting=False)
async def _scripted_run_command(command: str) -> str:
    # exit status and summary arrive exactly as the real tool formats them
    if "red" in command:
        return _command_result(1, "FAILED t.py::test_x - assert 1 == 2\n1 failed, 2 passed in 0.10s", "")
    return _command_result(0, "3 passed in 0.10s", "")


class _Planner:
    """Records what it was asked, and revises the remainder as fresh steps."""

    def __init__(self, replacement_command: str) -> None:
        self.replacement = replacement_command
        self.calls: list[dict] = []

    def can_replan(self, plan, *, limit=None) -> bool:
        return len(plan.revisions) < (limit if limit is not None else 2)

    async def replan(self, plan, *, reason, available_tools=(), limit=None, feedback=()):
        self.calls.append({"reason": reason, "feedback": list(feedback)})
        kept = [s for s in plan.steps if s.status is StepStatus.SUCCEEDED]
        new = PlanStep(id=StepId(f"s{len(kept) + 1}"), kind=StepKind.TOOL, tool="run_command",
                       description="rerun the tests", arguments={"command": self.replacement})
        return plan.revised(steps=[*kept, new], reason=reason)


def _plan(command: str) -> Plan:
    step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="run_command",
                    description="run the tests", arguments={"command": command})
    return Plan(id=PlanId.new(), goal=Goal(description="make the tests pass"), steps=(step,))


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)


def _loop(planner, *, test_signals: bool, **kw) -> AgenticExecutor:
    registry = ToolRegistry(policy=ToolPolicy())
    registry.register(_scripted_run_command)
    return AgenticExecutor(planner=planner, executor=ExecutionEngine(tools=registry),
                           test_signals=test_signals, **kw)


class TestTestResultsAsAnInput:
    async def test_a_red_run_is_not_done_and_replans_with_that_reason(self, ctx) -> None:
        planner = _Planner("pytest green")
        outcome = await _loop(planner, test_signals=True).run(
            _plan("pytest red"), context=ctx, max_iterations=4, deadline_s=60.0
        )
        assert outcome.loop.stop_reason == "completed" and outcome.succeeded
        assert len(planner.calls) == 1
        assert planner.calls[0]["reason"].startswith("tests_failed (pytest: 1 failed, 2 passed")
        assert any("test_x" in line for line in planner.calls[0]["feedback"]), planner.calls
        assert outcome.loop.trace == [
            "plan", "execute", "observe", "replan", "execute", "observe", "verify", "done",
        ]

    async def test_with_the_input_off_a_red_run_still_ends_as_completed_the_old_behaviour(self, ctx) -> None:
        planner = _Planner("pytest green")
        outcome = await _loop(planner, test_signals=False).run(
            _plan("pytest red"), context=ctx, max_iterations=4, deadline_s=60.0
        )
        assert outcome.succeeded and outcome.loop.stop_reason == "completed" and planner.calls == []

    async def test_a_run_that_stays_red_ends_unfinished_and_says_so(self, ctx) -> None:
        planner = _Planner("pytest red again")  # still red after the revision
        outcome = await _loop(planner, test_signals=True).run(
            _plan("pytest red"), context=ctx, max_iterations=1, deadline_s=60.0
        )
        assert not outcome.loop.stop_reason == "completed"
        assert outcome.loop.stop_reason == "iteration_budget_exhausted"
        assert "tests is still failing" in outcome.loop.escalation or "still failing" in outcome.loop.escalation
        assert outcome.loop.observations[-1]["kind"] == "tests_failed"

    async def test_a_command_that_exits_non_zero_without_a_runner_summary_also_counts(self, ctx) -> None:
        @tool("run_command", "Run a command.", param("command", "string", "c"),
              sensitivity=ToolSensitivity.PUBLIC)
        async def failing(command: str) -> str:
            return _command_result(2, "", "ls: cannot access 'x': No such file or directory")

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(failing)
        planner = _Planner("ls .")
        agentic = AgenticExecutor(planner=planner, executor=ExecutionEngine(tools=registry), test_signals=True)
        await agentic.run(_plan("ls x"), context=ctx, max_iterations=1, deadline_s=60.0)
        assert planner.calls and planner.calls[0]["reason"].startswith("tests_failed (command exited 2")

    async def test_the_stream_reports_the_observation(self, ctx) -> None:
        planner = _Planner("pytest green")
        items = [i async for i in _loop(planner, test_signals=True).stream(
            _plan("pytest red"), context=ctx, max_iterations=4, deadline_s=60.0)]
        outcome = items[-1]
        assert [o["kind"] for o in outcome.loop.observations] == ["tests_failed", "tests_passed"]


class TestTheReplanSeesIt:
    """The real ``PlanningEngine.replan``: the observation must reach the model's prompt."""

    class _Router:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def complete(self, request):
            self.prompts.append(request.conversation.last_user_message.content)

            class R:
                text = '{"steps": [{"id": "s1", "kind": "tool", "tool": "run_command", ' \
                       '"description": "rerun", "arguments": {"command": "pytest"}}]}'
            return R()

    async def test_feedback_lines_land_in_the_revision_prompt(self) -> None:
        router = self._Router()
        planner = PlanningEngine(router=router, prompts=build_default_registry())
        step = _plan("pytest red").steps[0].started().succeeded("x", 1.0)
        plan = Plan(id=PlanId.new(), goal=Goal(description="make the tests pass"), steps=(step,))
        await planner.replan(plan, reason="tests_failed", available_tools=[{"name": "run_command"}],
                             feedback=["Step s1 ran `run_command` and pytest: 1 failed; failing: test_x"])
        prompt = router.prompts[-1]
        assert "Observations to act on" in prompt and "failing: test_x" in prompt
        assert "re-runs the tests" in prompt

    async def test_without_feedback_the_prompt_is_what_it_always_was(self) -> None:
        router = self._Router()
        planner = PlanningEngine(router=router, prompts=build_default_registry())
        step = _plan("pytest red").steps[0].started().succeeded("x", 1.0)
        plan = Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(step,))
        await planner.replan(plan, reason="step_failed", available_tools=[{"name": "run_command"}])
        assert "Plan the remaining work only. Do not repeat completed steps." in router.prompts[-1]
        assert "Observations" not in router.prompts[-1]


# -- end to end: real pytest through the real kernel ----------------------------------------------


class TestEndToEndWithRealPytest:
    PLAN_RED = [write_step("s1", "calc.py", BUGGY), write_step("s2", "test_calc.py", TEST_MODULE),
                pytest_step("s3", ["s1", "s2"])]
    PLAN_FIX = [write_step("s4", "calc.py", FIXED), pytest_step("s5", ["s4"])]

    async def _turn(self, monkeypatch, plans, *, final="All tests pass now.", **env):
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000", **env)
        echo = await ready_engineer(container)
        script = script_model(echo, plans, final_text=final)
        frames = await collect_stream(container, engineer_turn())
        return container, echo, script, frames

    async def test_a_failing_suite_drives_a_replan_that_fixes_it(self, monkeypatch) -> None:
        _, _, script, frames = await self._turn(
            monkeypatch, [self.PLAN_RED, self.PLAN_FIX], LOOP_TEST_SIGNALS_ENABLED="true",
        )
        done = frames_of(frames, "done")[0]
        assert done["agent_loop"]["stop_reason"] == "completed"
        assert script.planning_calls == 2, "plan, then exactly one replan"
        assert "tests_failed" in script.plan_prompts[1] or "1 failed" in script.plan_prompts[1]
        kinds = [o["kind"] for o in done["agent_loop"]["observations"]]
        assert kinds == ["tests_failed", "tests_passed"], "real pytest was red, then green"
        statuses = {p["id"]: p["status"] for p in done["plan_summary"]}
        assert statuses["s5"] == "succeeded"

    async def test_with_the_input_off_the_old_loop_stops_at_red(self, monkeypatch) -> None:
        _, _, script, frames = await self._turn(monkeypatch, [self.PLAN_RED, self.PLAN_FIX])
        done = frames_of(frames, "done")[0]
        assert script.planning_calls == 1
        assert done["agent_loop"]["stop_reason"] == "completed", "every step 'succeeded'; nobody read the exit status"
        # ...but the critic still refuses to let the answer claim a green suite
        text = "".join(f["text"] for f in frames_of(frames, "token"))
        assert "Caution (checked against the tool results)" in text and "last test run failed" in text

    async def test_the_loop_trace_is_the_graph_it_walked(self, monkeypatch) -> None:
        _, _, _, frames = await self._turn(
            monkeypatch, [self.PLAN_RED, self.PLAN_FIX], LOOP_TEST_SIGNALS_ENABLED="true",
        )
        trace = frames_of(frames, "done")[0]["agent_loop"]["trace"]
        assert trace == ["plan", "execute", "observe", "replan", "execute", "observe", "verify", "done"]
