"""The token budget monitor: budget status each pass, a pressure signal, and compression, once
(ADR-039; row 28).

The Engineer had no ``token_budget`` at all. It now has a generous default, reports how much of it a
run has used on every pass, and when use crosses a threshold says so and shortens older tool output --
once. The arithmetic is tested exactly on a scripted loop; the wiring (default, setting, frames,
compression reaching the final prompt) through a real Engineer turn.
"""

from __future__ import annotations

import shlex
import sys

import pytest

from aura_core.agents.registry import build_default_agent_registry
from aura_core.config import load_settings
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.identifiers import PlanId, StepId, UserId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind, StepStatus
from aura_core.domain.agent_runs import AgentBudget
from aura_core.execution.agentic_loop import AgenticExecutor
from aura_core.execution.engine import ExecutionOutcome
from aura_core.execution.token_monitor import (
    RECOMMENDATIONS,
    BudgetStatus,
    TokenMonitor,
    compress_results,
    estimate_tokens,
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

BIG = "L" * 6_000


def _step(sid: str, result: str | None = None, *, failed: bool = False) -> PlanStep:
    s = PlanStep(id=StepId(sid), kind=StepKind.TOOL, tool="run_command", description=sid,
                 arguments={"command": sid})
    if result is None:
        return s
    started = s.started()
    return started.failed("boom", 1.0) if failed else started.succeeded(result, 1.0)


def _plan(*steps: PlanStep, tokens: int = 0) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps, generation_tokens=tokens)


class TestArithmetic:
    def test_estimate(self) -> None:
        assert estimate_tokens("") == 0 and estimate_tokens(None) == 0
        assert estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2
        assert estimate_tokens("x" * 4000) == 1000

    def test_status_is_generation_plus_held_output(self) -> None:
        monitor = TokenMonitor(10_000)
        plan = _plan(_step("s1", "x" * 4000), _step("s2", "y" * 400), _step("s3", "bad", failed=True))
        status = monitor.status(plan, generation_tokens=3_000, iteration=2)
        assert (status.used, status.generation_tokens, status.held_output_tokens) == (4_100, 3_000, 1_100)
        assert status.remaining == 5_900 and status.percent == 41.0 and status.iteration == 2
        assert status.as_dict()["budget"] == 10_000

    def test_a_failed_steps_text_is_not_held_output(self) -> None:
        status = TokenMonitor(1000).status(_plan(_step("s1", "x" * 4000, failed=True)),
                                           generation_tokens=0, iteration=1)
        assert status.held_output_tokens == 0

    def test_remaining_never_goes_negative(self) -> None:
        assert BudgetStatus(100, 150, 150, 0, 1).remaining == 0

    @pytest.mark.parametrize("budget", [None, 0])
    def test_no_budget_means_no_monitoring(self, budget) -> None:
        monitor = TokenMonitor(budget)
        assert not monitor.active
        assert monitor.status(_plan(_step("s1", BIG)), generation_tokens=10**9, iteration=1) is None
        assert monitor.pressure_due(None) is False

    def test_pressure_fires_once_at_the_threshold(self) -> None:
        monitor = TokenMonitor(1000, threshold=0.8)
        below = BudgetStatus(1000, 799, 799, 0, 1)
        at = BudgetStatus(1000, 800, 800, 0, 2)
        above = BudgetStatus(1000, 950, 950, 0, 3)
        assert monitor.pressure_due(below) is False
        assert monitor.pressure_due(at) is True
        assert monitor.pressure_due(above) is False, "once, not on every later pass"


class TestCompression:
    def test_older_long_outputs_are_shortened_and_the_newest_pass_is_kept(self) -> None:
        plan = _plan(_step("s1", BIG), _step("s2", BIG), _step("s3", "short"))
        results = {"s1": BIG, "s2": BIG, "s3": "short"}
        new_plan, new_results, saved = compress_results(plan, results, keep_step_ids=frozenset({"s2"}))
        assert saved > 3_000
        assert len(new_results["s1"]) < 2_300 and "omitted to save context" in new_results["s1"]
        assert new_results["s2"] == BIG, "the newest pass is untouched"
        assert new_results["s3"] == "short", "short output is left alone"
        s1 = next(s for s in new_plan.steps if s.id.value == "s1")
        assert s1.result == new_results["s1"] and s1.status is StepStatus.SUCCEEDED and s1.tool == "run_command"
        assert s1.result.startswith("LLL") and s1.result.endswith("LLL"), "head and tail survive"

    def test_nothing_to_compress_returns_the_same_plan(self) -> None:
        plan = _plan(_step("s1", "short"))
        new_plan, new_results, saved = compress_results(plan, {"s1": "short"})
        assert saved == 0 and new_plan is plan and new_results == {"s1": "short"}

    def test_it_is_idempotent(self) -> None:
        plan = _plan(_step("s1", BIG))
        once_plan, once_results, _ = compress_results(plan, {"s1": BIG})
        _, _, saved_again = compress_results(once_plan, once_results)
        assert saved_again == 0

    def test_failed_and_unsucceeded_steps_are_not_touched(self) -> None:
        plan = _plan(_step("s1", BIG, failed=True), _step("s2"))
        new_plan, _, saved = compress_results(plan, {})
        assert saved == 0 and new_plan is plan


# -- the loop ------------------------------------------------------------------------------------


class _Planner:
    def __init__(self, generation_tokens: int) -> None:
        self.generation_tokens = generation_tokens

    def can_replan(self, plan, *, limit=None) -> bool:
        return len(plan.revisions) < (limit if limit is not None else 2)

    async def replan(self, plan, *, reason, available_tools=(), limit=None):
        kept = [s for s in plan.steps if s.status is StepStatus.SUCCEEDED]
        fresh = [_step(f"s{len(kept) + 1}")]
        return plan.revised(steps=[*kept, *fresh], reason=reason,
                            generation_tokens=self.generation_tokens)


class _Engine:
    """Pass 1..n-1 fail after producing big output; pass n succeeds."""

    def __init__(self, succeed_on: int) -> None:
        self.succeed_on, self.calls = succeed_on, 0

    async def execute(self, plan, *, context, capabilities=(), confirmations=None, on_event=None):
        self.calls += 1
        steps, results = [], {}
        for s in plan.steps:
            if s.status is StepStatus.PENDING:
                s = s.started().succeeded(BIG, 1.0)
                results[s.id.value] = BIG
            steps.append(s)
        new = plan.with_steps(steps)
        ok = self.calls >= self.succeed_on
        if not ok:  # a fresh failing step each pass, so there is something to replan
            extra = PlanStep(id=StepId(f"f{self.calls}"), kind=StepKind.TOOL, tool="x", description="f")
            new = Plan(id=new.id, goal=new.goal, steps=(*new.steps, extra.started().failed("boom", 1.0)),
                       revisions=new.revisions, generation_tokens=new.generation_tokens)
        return ExecutionOutcome(plan=new, results=results, succeeded=ok)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0)


async def stream_loop(loop, plan, ctx, budget):
    items = [i async for i in loop.stream(plan, context=ctx, max_iterations=6, deadline_s=60.0, budget=budget)]
    frames = [i.as_frame() for i in items if hasattr(i, "as_frame")]
    return frames, items[-1]


class TestInTheLoop:
    async def test_every_pass_reports_used_remaining_and_percent(self, ctx) -> None:
        loop = AgenticExecutor(planner=_Planner(500), executor=_Engine(succeed_on=3))
        frames, _ = await stream_loop(loop, _plan(_step("s1"), tokens=1000), ctx,
                                      AgentBudget(max_iterations=6, token_budget=100_000))
        budgets = [f for f in frames if f["type"] == "budget"]
        assert [b["iteration"] for b in budgets] == [1, 2, 3]
        for b in budgets:
            assert b["used"] == b["generation_tokens"] + b["held_output_tokens"]
            assert b["remaining"] == 100_000 - b["used"]
            assert b["percent"] == round(100 * b["used"] / 100_000, 1)
        assert [b["generation_tokens"] for b in budgets] == [1000, 1500, 2000]
        assert budgets[0]["held_output_tokens"] == 1500 and budgets[2]["held_output_tokens"] == 4500

    async def test_no_budget_no_frames_and_nothing_changes(self, ctx) -> None:
        loop = AgenticExecutor(planner=_Planner(500), executor=_Engine(succeed_on=2))
        frames, outcome = await stream_loop(loop, _plan(_step("s1")), ctx, AgentBudget(max_iterations=6))
        assert [f for f in frames if f["type"] in ("budget", "context_pressure")] == []
        assert outcome.succeeded and outcome.loop.budget is None

    async def test_pressure_is_signalled_once_and_older_output_is_compressed(self, ctx) -> None:
        # 20 000 budget: pass 1 uses 1 500 (7.5 %), pass 2 ~3 500 (17.5 %), pass 3 ~6 000 (30 %)
        loop = AgenticExecutor(planner=_Planner(500), executor=_Engine(succeed_on=4),
                               pressure_threshold=0.25)
        frames, outcome = await stream_loop(loop, _plan(_step("s1"), tokens=0), ctx,
                                            AgentBudget(max_iterations=6, token_budget=20_000))
        pressure = [f for f in frames if f["type"] == "context_pressure"]
        assert len(pressure) == 1, "once, however many passes stay above the threshold"
        p = pressure[0]
        assert p["percent"] >= 25.0 and p["iteration"] == 3
        assert p["recommendations"] == list(RECOMMENDATIONS)
        assert p["action"] == "compressed_tool_output" and p["characters_saved"] > 3_000
        # s1 and s2's output (older passes) shortened in the plan the run holds; the newest
        # pass's output, which is what the final answer is built from, intact
        by_id = {s.id.value: s.result for s in outcome.plan.steps if s.result}
        assert "omitted to save context" in by_id["s1"] and "omitted to save context" in by_id["s2"]
        assert by_id["s4"] == BIG and outcome.results == {"s4": BIG}, "the last pass is never compressed"

    async def test_with_nothing_older_to_shorten_the_signal_still_fires_and_says_so(self, ctx) -> None:
        loop = AgenticExecutor(planner=_Planner(0), executor=_Engine(succeed_on=1), pressure_threshold=0.1)
        frames, _ = await stream_loop(loop, _plan(_step("s1"), tokens=900), ctx,
                                      AgentBudget(max_iterations=6, token_budget=2_000))
        (p,) = [f for f in frames if f["type"] == "context_pressure"]
        assert p["action"] == "nothing_to_compress" and p["characters_saved"] == 0

    async def test_the_hard_stop_is_still_generation_tokens_alone(self, ctx) -> None:
        """Held output informs pressure; it does not end a run. Generation tokens still do."""
        loop = AgenticExecutor(planner=_Planner(600), executor=_Engine(succeed_on=99))
        outcome = await loop.run(_plan(_step("s1"), tokens=600), context=ctx, max_iterations=6,
                                 deadline_s=60.0, budget=AgentBudget(max_iterations=6, token_budget=1_000))
        assert outcome.loop.stop_reason == "token_budget_exhausted"

    async def test_held_output_alone_never_exhausts_the_budget(self, ctx) -> None:
        loop = AgenticExecutor(planner=_Planner(1), executor=_Engine(succeed_on=3))
        outcome = await loop.run(_plan(_step("s1"), tokens=1), context=ctx, max_iterations=6,
                                 deadline_s=60.0, budget=AgentBudget(max_iterations=6, token_budget=100))
        assert outcome.succeeded, "100 tokens of budget, 4 500 held: still not a reason to stop"

    async def test_the_last_status_travels_on_the_outcome(self, ctx) -> None:
        loop = AgenticExecutor(planner=_Planner(500), executor=_Engine(succeed_on=2))
        outcome = await loop.run(_plan(_step("s1")), context=ctx, max_iterations=6, deadline_s=60.0,
                                 budget=AgentBudget(max_iterations=6, token_budget=50_000))
        assert outcome.loop.budget["budget"] == 50_000 and outcome.loop.budget["iteration"] == 2


# -- configuration -------------------------------------------------------------------------------


class TestTheEngineerBudget:
    def test_the_bare_registry_is_unchanged(self) -> None:
        assert build_default_agent_registry().get("engineer").token_budget is None

    def test_the_registry_takes_the_setting(self) -> None:
        reg = build_default_agent_registry(engineer_token_budget=123_456)
        assert reg.get("engineer").budget.token_budget == 123_456
        assert reg.get("orchestrator").token_budget is None, "only the Engineer gets one"
        assert build_default_agent_registry(engineer_token_budget=0).get("engineer").token_budget is None

    def test_the_default_is_generous_and_the_threshold_is_80_percent(self) -> None:
        reasoning = load_settings(refresh=True, validate=False).reasoning
        assert reasoning.engineer_token_budget == 400_000
        assert reasoning.context_pressure_threshold == 0.8

    async def test_the_container_gives_the_engineer_its_budget(self, monkeypatch) -> None:
        engineer = make_container(monkeypatch).agents.get("engineer")
        assert engineer.budget.token_budget == 400_000

    async def test_the_env_var_overrides_it_and_zero_removes_it(self, monkeypatch) -> None:
        assert make_container(monkeypatch, ENGINEER_TOKEN_BUDGET="50000").agents.get("engineer").token_budget == 50_000
        assert make_container(monkeypatch, ENGINEER_TOKEN_BUDGET="0").agents.get("engineer").token_budget is None

    def test_bad_values_are_refused_at_load(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_CONTEXT_PRESSURE_THRESHOLD", "1.5")
        with pytest.raises(ConfigurationError):
            load_settings(refresh=True, validate=False)

    async def test_the_nine_pass_default_budget_does_not_get_close_to_the_ceiling(self) -> None:
        """Nine 1 200-token plans plus nine passes of 20 000 characters of tool output."""
        worst = 9 * 1_200 + 9 * estimate_tokens("x" * 20_000)
        assert worst < 0.2 * 400_000, "the default is generous, as promised"


# -- through a real turn -------------------------------------------------------------------------

PRINT_BIG = f"{shlex.quote(sys.executable)} -c \"print('A' * 6000)\""
PLAN_1 = [tool_step("s1", "run_command", "Print a lot", {"command": PRINT_BIG}),
          tool_step("s2", "edit_file", "Edit a missing file", {"path": "nope.py", "find": "a", "replace": "b"})]
PLAN_2 = [tool_step("s3", "run_command", "Say ok", {"command": "echo ok"})]


class TestEndToEnd:
    async def _turn(self, monkeypatch, **env):
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000", CRITIC_ENABLED="false", **env)
        echo = await ready_engineer(container)
        script_model(echo, [PLAN_1, PLAN_2], final_text="Done.")
        frames = await collect_stream(container, engineer_turn("print and edit"))
        return echo, frames

    async def test_budget_frames_then_one_compression_that_reaches_the_prompt(self, monkeypatch) -> None:
        # Calibrate on an identical run, then pick a budget/threshold that puts the crossing on pass 2.
        _, calibration = await self._turn(monkeypatch)
        used = [b["used"] for b in frames_of(calibration, "budget")]
        assert len(used) == 2 and used[0] < used[1]
        budget = used[1] * 2
        threshold = round(((used[0] + used[1]) / 2) / budget, 4)

        echo, frames = await self._turn(
            monkeypatch, ENGINEER_TOKEN_BUDGET=str(budget), CONTEXT_PRESSURE_THRESHOLD=str(threshold),
        )
        budgets = frames_of(frames, "budget")
        assert [b["iteration"] for b in budgets] == [1, 2]
        assert all(b["budget"] == budget and b["remaining"] == budget - b["used"] for b in budgets)
        pressure = frames_of(frames, "context_pressure")
        assert len(pressure) == 1 and pressure[0]["iteration"] == 2
        assert pressure[0]["action"] == "compressed_tool_output" and pressure[0]["characters_saved"] > 3_000
        # What the final answer is built from is the last pass's output (ExecutionOutcome.results
        # carries one pass), which compression never touches; the 6 000 characters it shortened
        # were held in the plan from pass 1.
        system = echo.last_system_prompt()
        assert "A" * 6_000 not in system and "stdout ---\nok" in system

    async def test_the_default_budget_emits_status_but_no_pressure(self, monkeypatch) -> None:
        _, frames = await self._turn(monkeypatch)
        budgets = frames_of(frames, "budget")
        assert budgets and budgets[0]["budget"] == 400_000 and budgets[-1]["percent"] < 10  # was 5 before the planner prompt carried the merged tool catalog (~10k tokens a pass)
        assert frames_of(frames, "context_pressure") == []

    async def test_unbounded_means_no_budget_frames(self, monkeypatch) -> None:
        _, frames = await self._turn(monkeypatch, ENGINEER_TOKEN_BUDGET="0")
        assert frames_of(frames, "budget") == []

    async def test_other_personas_emit_no_budget_frames(self, monkeypatch) -> None:
        container = make_container(monkeypatch)
        await ready_engineer(container)
        from aura_core.kernel import TurnRequest
        from tests.unit._loop_harness import ENGINEER

        frames = await collect_stream(container, TurnRequest(message="hello", user_id=ENGINEER, use_tools=False))
        assert frames_of(frames, "budget") == []
