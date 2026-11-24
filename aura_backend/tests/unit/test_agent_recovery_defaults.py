"""The three agent-execution problems from the tool audit, run with NO flag set.

The audit listed three critical problems in how the Engineer agent recovers from failure:

1. a command that exits non-zero still came back ``ok=True``, so the loop never noticed a red test run;
2. no repeated-failure detection: one identical failing plan was executed nine times;
3. a failed read-only step was retried even when the failure was a policy refusal or a validation error.

The fixes (ADR-037, ADR-045) existed behind ``AURA_LOOP_TEST_SIGNALS_ENABLED`` and
``AURA_LOOP_RECOVERY_POLICY_ENABLED``, both off. The "Enable agent recovery defaults" change turned
them on. The other loop tests pin each mechanism on its own, with the others switched off; these
three run the way production now runs, with the environment untouched, so they fail if a default
is ever flipped back or a new default quietly defeats one of the fixes.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.engineering import ENGINEER_CONSENT_NOTICE_VERSION
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import SessionId
from aura_core.domain.tools import ToolSensitivity
from aura_core.kernel import TurnRequest
from aura_core.tools import param, tool

from aura_core.execution.stuck import stable_text
from tests.unit._loop_harness import (
    BUGGY, FIXED, TEST_MODULE, collect_stream, engineer_turn, frames_of, make_container,
    pytest_step, ready_engineer, script_model, tool_step, write_step,
)
from tests.unit.test_recovery_e2e import USER, _Probe, _Script, _plan, _run, _step


def test_the_defaults_are_the_fixed_behaviour() -> None:
    reasoning = load_settings(refresh=True, validate=False).reasoning
    assert reasoning.loop_test_signals_enabled is True
    assert reasoning.loop_recovery_policy_enabled is True
    assert reasoning.loop_stuck_detection_enabled is True


class TestACommandThatExitsNonZeroIsNotSuccess:
    """Problem 1: real pytest, red then green, nothing enabled by hand."""

    PLAN_RED = [write_step("s1", "calc.py", BUGGY), write_step("s2", "test_calc.py", TEST_MODULE),
                pytest_step("s3", ["s1", "s2"])]
    PLAN_FIX = [write_step("s4", "calc.py", FIXED), pytest_step("s5", ["s4"])]

    async def test_a_red_suite_drives_a_replan_and_the_run_is_not_called_done_while_red(self, monkeypatch) -> None:
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000")
        echo = await ready_engineer(container)
        script = script_model(echo, [self.PLAN_RED, self.PLAN_FIX], final_text="All tests pass now.")
        frames = await collect_stream(container, engineer_turn())

        done = frames_of(frames, "done")[0]
        kinds = [o["kind"] for o in done["agent_loop"]["observations"]]
        assert kinds == ["tests_failed", "tests_passed"], "the exit status was read: red, then green"
        assert script.planning_calls == 2, "the red run caused exactly one replan"
        assert "tests_failed" in script.plan_prompts[1] or "1 failed" in script.plan_prompts[1]
        assert done["agent_loop"]["stop_reason"] == "completed"
        assert {p["id"]: p["status"] for p in done["plan_summary"]}["s5"] == "succeeded"

    async def test_a_suite_that_never_goes_green_does_not_end_as_completed(self, monkeypatch) -> None:
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000")
        echo = await ready_engineer(container)
        script_model(echo, [self.PLAN_RED], final_text="All tests pass now.")
        frames = await collect_stream(container, engineer_turn())

        done = frames_of(frames, "done")[0]
        assert done["agent_loop"]["stop_reason"] != "completed", "a run that is still red is not complete"
        text = "".join(f["text"] for f in frames_of(frames, "token"))
        assert "All tests pass now." not in text.replace("Caution", "") or "failed" in text.lower(), (
            "and the answer does not simply repeat the model's claim of a green suite"
        )


class TestAnIdenticalFailingPlanIsNotRepeatedNineTimes:
    """Problem 2: ``edit_file`` on a file that does not exist, every pass, nothing enabled by hand."""

    FAILING_PLAN = [tool_step("s1", "edit_file", "Fix the bug",
                              {"path": "missing.py", "find": "a", "replace": "b"})]

    async def test_it_stops_after_a_few_passes_and_says_so(self, monkeypatch) -> None:
        container = make_container(monkeypatch, CRITIC_ENABLED="false", LLM_MAX_PROMPT_TOKENS="8000")
        echo = await ready_engineer(container)
        script = script_model(echo, [self.FAILING_PLAN], final_text="I could not fix it.")
        frames = await collect_stream(container, engineer_turn("fix missing.py"))

        done = frames_of(frames, "done")[0]
        assert script.planning_calls <= 3, f"one plan and at most two replans, not nine (got {script.planning_calls})"
        reason = done["agent_loop"]["stop_reason"]
        assert reason not in ("completed", "iteration_budget_exhausted"), reason
        assert reason == "stuck" or reason.startswith("escalated"), reason
        escalation = done["agent_loop"]["escalation"] or ""
        text = "".join(f["text"] for f in frames_of(frames, "token"))
        assert escalation and escalation in text, "the user is told, in the answer, that it gave up and why"


class TestAReadFailureIsOnlyRetriedWhenRetryingCanHelp:
    """Problem 3: the default container, no ``policy=`` argument -- whatever the shipped default is."""

    async def _container(self, script, probe):
        container = Container(load_settings(refresh=True, validate=False))
        await container.startup()
        await container.engineer_consent.accept(USER, ENGINEER_CONSENT_NOTICE_VERSION, at=datetime.now(timezone.utc))

        @tool("git_status", "A read that times out once, then works.", sensitivity=ToolSensitivity.PUBLIC)
        async def git_status() -> str:
            if probe.hit("git_status") == 1:
                raise ToolExecutionError("upstream timed out", tool="git_status")
            return "flaky data"

        @tool("calculator", "A read that needs an integer.", param("n", "integer", "a number"),
              sensitivity=ToolSensitivity.PUBLIC)
        async def calculator(n: int) -> str:
            probe.hit("calculator")
            return f"value {n}"

        @tool("list_workspace_snapshots", "A read the caller is not granted.",
              sensitivity=ToolSensitivity.PUBLIC, required_capability="locked_capability")
        async def list_workspace_snapshots() -> str:
            probe.hit("list_workspace_snapshots")
            return "secret"

        for t in (git_status, calculator, list_workspace_snapshots):
            container.tools.register(t, overwrite=True)
        container.planner.plan = script.plan
        return container

    async def test_a_validation_error_is_never_dispatched_and_never_retried(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "calculator", {"n": "not-a-number"})))
        result = await _run(await self._container(script, probe), "compute")
        assert probe.calls.get("calculator", 0) == 0, "an invalid call never reaches the tool, let alone twice"
        assert len(script.goals) <= 3, "the plan and at most the two replans the loop allows, not nine"
        assert "validation_error" in script.goals[1], "and the replan carries the reason it was refused"

    async def test_a_policy_refusal_is_not_retried(self) -> None:
        probe = _Probe()
        script = _Script(_plan(_step("s1", "list_workspace_snapshots")))
        result = await _run(await self._container(script, probe), "list them")
        assert probe.calls.get("list_workspace_snapshots", 0) == 0, "a refusal is not worked around by retrying"
        assert result.agent_loop["stop_reason"] != "completed"

    async def test_a_transient_failure_still_is_retried(self) -> None:
        """The other half: the fix is classification, not 'never retry'."""
        probe = _Probe()
        script = _Script(_plan(_step("s1", "git_status")))
        result = await _run(await self._container(script, probe), "check the data")
        assert probe.calls == {"git_status": 2}
        assert result.agent_loop["stop_reason"] == "completed"


class TestAFailureThatCarriesATimestampIsStillTheSameFailure:
    """Found by running ``npm test`` through the loop: its failure ends with a log path named after
    the minute it ran, so no two runs looked alike, the detector never saw a repeat, and the plan
    ran to the iteration limit (nine times). Same failure, different text; it has to count as one."""

    NOISY = ("sh -c 'echo \"build failed at $(date +%s%N) pid $$\"; "
             "echo \"log: /tmp/run-$$/x-$(date +%Y-%m-%dT%H_%M_%S).log (took 0.$(date +%N | cut -c1-3)s)\"; exit 2'")

    async def test_a_failing_command_with_changing_text_is_stopped_as_stuck(self, monkeypatch) -> None:
        container = make_container(monkeypatch, CRITIC_ENABLED="false", LLM_MAX_PROMPT_TOKENS="8000")
        echo = await ready_engineer(container)
        plan = [tool_step("s1", "run_command", "build", {"command": self.NOISY, "timeout_s": 30})]
        script = script_model(echo, [plan], final_text="Built.")
        frames = await collect_stream(container, engineer_turn("build it"))
        done = frames_of(frames, "done")[0]
        assert script.planning_calls <= 3, f"not nine (got {script.planning_calls})"
        assert done["agent_loop"]["stop_reason"] in ("stuck",) or done["agent_loop"]["stop_reason"].startswith("escalated")

    async def test_the_same_holds_for_a_test_runner_whose_output_carries_a_timestamp(self, monkeypatch) -> None:
        container = make_container(monkeypatch, CRITIC_ENABLED="false", LLM_MAX_PROMPT_TOKENS="8000")
        echo = await ready_engineer(container)
        plan = [tool_step("s1", "run_command", "test", {
            "command": "sh -c 'echo \"npm error A complete log of this run can be found in: "
                       "/root/.npm/_logs/$(date +%Y-%m-%dT%H_%M_%S_%NZ)-debug-0.log\" >&2; "
                       "echo \"FAIL tests\"; exit 1' # npm test",
            "timeout_s": 30})]
        script = script_model(echo, [plan], final_text="Green.")
        frames = await collect_stream(container, engineer_turn("run npm test"))
        assert script.planning_calls <= 3


class TestStableText:
    def test_run_to_run_noise_is_removed(self) -> None:
        a = "A complete log of this run can be found in: /root/.npm/_logs/2026-11-24T21_31_05_123Z-debug-0.log"
        b = "A complete log of this run can be found in: /root/.npm/_logs/2026-11-24T21_32_47_981Z-debug-0.log"
        assert stable_text(a) == stable_text(b)
        assert stable_text("1 failed in 0.12s at 10:31:55") == stable_text("1 failed in 3.4s at 10:48:02")
        assert stable_text("wrote /tmp/aura-ab12/out.txt pid 4411") == stable_text("wrote /tmp/aura-cd34/out.txt pid 9")

    def test_a_different_failure_is_still_different(self) -> None:
        assert stable_text("3 failed, 2 passed") != stable_text("5 failed, 2 passed")
        assert stable_text("ModuleNotFoundError: No module named 'a'") != stable_text("ModuleNotFoundError: No module named 'b'")
        assert stable_text("exit 2: permission denied") != stable_text("exit 2: no such file")
