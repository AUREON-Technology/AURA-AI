"""Test-runner output as a loop input (state graph, ADR-037; inventory row 14).

``run_command`` returns ``ok=True`` whenever the command *ran* -- the exit status and the runner's
summary live only in the result text. These tests pin the parser that reads them, on real runner
output shapes and on garbage, and the observation rules built on it.
"""

from __future__ import annotations

import json

import pytest

from aura_core.domain.identifiers import PlanId, StepId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.execution.runner_output import (
    OBS_COMMAND_FAILED,
    OBS_COMMAND_OK,
    OBS_TESTS_FAILED,
    OBS_TESTS_PASSED,
    is_test_command,
    observe_plan,
    observe_tool_result,
    parse_command_result,
    parse_test_output,
    unresolved_failures,
)
from aura_core.tools.coding import _command_result

PYTEST_RED = """\
============================= test session starts ==============================
collected 7 items

tests/test_a.py ..F                                                      [ 42%]

=================================== FAILURES ===================================
____________________________________ test_x ____________________________________
E       assert 1 == 2
=========================== short test summary info ============================
FAILED tests/test_a.py::test_x - assert 1 == 2
FAILED tests/test_b.py::test_y
========================= 2 failed, 5 passed, 1 skipped in 0.42s ===============
"""


class TestParsers:
    def test_pytest_failure_summary_gives_counts_and_names(self) -> None:
        run = parse_test_output(PYTEST_RED)
        assert run is not None and not run.ok
        assert (run.runner, run.failed, run.passed, run.skipped) == ("pytest", 2, 5, 1)
        assert run.failures == ("tests/test_a.py::test_x", "tests/test_b.py::test_y")
        assert "2 failed" in run.describe() and "test_x" in run.describe()

    @pytest.mark.parametrize("text", ["3 passed in 0.01s", "===== 12 passed, 2 skipped in 1.5s ====="])
    def test_pytest_green(self, text: str) -> None:
        run = parse_test_output(text)
        assert run is not None and run.ok and run.runner == "pytest"

    def test_pytest_quiet_mode_summary(self) -> None:
        run = parse_test_output("..F\n1 failed, 2 passed in 0.10s")
        assert run is not None and run.failed == 1 and run.passed == 2

    def test_pytest_collection_error_is_a_failure_not_silence(self) -> None:
        run = parse_test_output("ERROR collecting tests/test_a.py\nImportError: no module x\npytest")
        assert run is not None and not run.ok

    def test_pytest_errors_count_as_failing(self) -> None:
        run = parse_test_output("1 passed, 2 errors in 0.2s")
        assert run is not None and run.errors == 2 and not run.ok

    def test_unittest_failure_and_ok(self) -> None:
        red = parse_test_output(
            "FAIL: test_a (tests.test_mod.T)\n----\nRan 3 tests in 0.001s\n\nFAILED (failures=1, errors=1)"
        )
        assert red is not None and red.runner == "unittest"
        assert (red.failed, red.errors, red.passed) == (1, 1, 1)
        assert red.failures == ("tests.test_mod.T.test_a",)
        green = parse_test_output("Ran 3 tests in 0.001s\n\nOK")
        assert green is not None and green.ok and green.passed == 3

    def test_jest(self) -> None:
        run = parse_test_output("Tests:       2 failed, 5 passed, 7 total\n  ● Suite › does thing")
        assert run is not None and run.runner == "jest" and run.failed == 2 and run.passed == 5
        assert run.failures == ("Suite › does thing",)

    def test_vitest(self) -> None:
        run = parse_test_output(" Tests  1 failed | 4 passed (5)\n FAIL  src/a.test.ts > adds")
        assert run is not None and run.runner == "vitest" and run.failed == 1 and run.passed == 4
        assert run.failures == ("src/a.test.ts > adds",)

    def test_go_test(self) -> None:
        red = parse_test_output("--- FAIL: TestAdd (0.00s)\nFAIL\nFAIL\texample.com/pkg\t0.01s")
        assert red is not None and red.runner == "go" and not red.ok
        assert red.failures == ("TestAdd",)
        green = parse_test_output("ok  \texample.com/pkg\t0.01s")
        assert green is not None and green.ok

    def test_generate_and_run_tests_json(self) -> None:
        red = parse_test_output(json.dumps({"passed": False, "counts": {"passed": 1, "failed": 2}}))
        assert red is not None and not red.ok and red.failed == 2
        # "did not pass" with no counts must not read as green
        bare = parse_test_output(json.dumps({"passed": False}))
        assert bare is not None and not bare.ok
        assert parse_test_output(json.dumps({"passed": True, "counts": {"passed": 4}})).ok

    @pytest.mark.parametrize("garbage", [
        "", "   ", "\x00\x01\x02 lorem ipsum", "{not json", '{"passed": "yes"}', "[1,2,3]",
        "Ran tests in the lab", "X" * 50_000, None, 42, b"bytes",
    ])
    def test_garbage_yields_none_never_an_exception(self, garbage) -> None:
        assert parse_test_output(garbage) is None

    def test_a_huge_output_is_bounded(self) -> None:
        run = parse_test_output("noise\n" * 100_000 + "1 failed, 2 passed in 0.1s")
        assert run is not None and run.failed == 1


class TestCommandResultShape:
    def test_it_reads_the_exact_text_run_command_produces(self) -> None:
        text = _command_result(1, "1 failed, 2 passed in 0.10s", "warning: x")
        exit_code, stdout, stderr = parse_command_result(text)
        assert exit_code == 1 and "1 failed" in stdout and stderr == "warning: x"

    def test_a_text_without_the_header_is_not_claimed(self) -> None:
        assert parse_command_result("hello")[0] is None

    @pytest.mark.parametrize("command,expected", [
        ("pytest -q", True), ("python -m pytest tests/", True), ("python3 -m unittest", True),
        ("npm test", True), ("npm run test", True), ("go test ./...", True), ("cargo test", True),
        ("make test", True), ("dart test", True), ("flutter test", True),
        ("ls -la", False), ("cat README.md", False), ("grep -r pytest_plugins .", False),
        (None, False), (123, False),
    ])
    def test_which_commands_are_test_commands(self, command, expected) -> None:
        assert is_test_command(command) is expected


class TestObservations:
    def _run(self, command: str, exit_code: int, stdout: str = "", stderr: str = ""):
        return observe_tool_result("run_command", {"command": command},
                                   _command_result(exit_code, stdout, stderr))

    def test_a_non_zero_test_run_is_tests_failed_even_though_the_tool_succeeded(self) -> None:
        obs = self._run("pytest -q", 1, PYTEST_RED)
        assert obs.kind == OBS_TESTS_FAILED and obs.failing and obs.exit_code == 1
        assert "2 failed" in obs.summary

    def test_a_red_summary_counts_even_when_the_exit_status_is_masked(self) -> None:
        """`pytest || true` exits 0 with failures on screen: the summary is the truth."""
        assert self._run("pytest || true", 0, PYTEST_RED).kind == OBS_TESTS_FAILED

    def test_a_test_command_that_crashed_before_a_summary_is_still_failed(self) -> None:
        obs = self._run("python -m pytest", 2, "", "ModuleNotFoundError: No module named 'calc'")
        assert obs.kind == OBS_TESTS_FAILED and "ModuleNotFoundError" in obs.summary

    def test_green_runs(self) -> None:
        assert self._run("pytest", 0, "3 passed in 0.01s").kind == OBS_TESTS_PASSED
        assert self._run("make test", 0, "").kind == OBS_TESTS_PASSED

    def test_any_other_non_zero_exit_is_command_failed(self) -> None:
        obs = self._run("ls missing", 2, "", "ls: cannot access 'missing'")
        assert obs.kind == OBS_COMMAND_FAILED and obs.exit_code == 2

    def test_a_silent_exit_1_is_an_answer_not_a_failure(self) -> None:
        """`grep` with no match, `diff` with no difference: replanning over it would be noise."""
        assert self._run("grep nothing file", 1).kind == OBS_COMMAND_OK

    def test_exit_zero_is_command_ok(self) -> None:
        assert self._run("ls", 0, "a.py").kind == OBS_COMMAND_OK

    def test_failure_signature_distinguishes_different_failures(self) -> None:
        a = self._run("pytest", 1, "FAILED t.py::a\n1 failed in 0.1s")
        b = self._run("pytest", 1, "FAILED t.py::b\n1 failed in 0.1s")
        a2 = self._run("pytest", 1, "FAILED t.py::a\n1 failed in 0.9s")
        assert a.signature != b.signature and a.signature == a2.signature

    def test_generate_and_run_tests_failure(self) -> None:
        obs = observe_tool_result("generate_and_run_tests", {}, json.dumps({"passed": False}))
        assert obs is not None and obs.kind == OBS_TESTS_FAILED

    @pytest.mark.parametrize("tool,text", [
        ("calculator", "42"), ("run_command", "no header at all"), ("run_command", None),
        ("generate_and_run_tests", "not json"),
    ])
    def test_no_signal_is_none(self, tool, text) -> None:
        assert observe_tool_result(tool, {}, text) is None


def _plan(*steps: PlanStep) -> Plan:
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=steps)


def _ran(step_id: str, command: str, exit_code: int, stdout: str) -> PlanStep:
    step = PlanStep(id=StepId(step_id), kind=StepKind.TOOL, tool="run_command",
                    description="run", arguments={"command": command})
    return step.started().succeeded(_command_result(exit_code, stdout, ""), 1.0)


class TestUnresolved:
    def test_a_red_run_followed_by_a_green_one_is_resolved(self) -> None:
        plan = _plan(_ran("s1", "pytest", 1, "1 failed in 0.1s"), _ran("s2", "pytest", 0, "1 passed in 0.1s"))
        assert unresolved_failures(observe_plan(plan)) == []

    def test_a_green_run_followed_by_a_red_one_is_unresolved(self) -> None:
        plan = _plan(_ran("s1", "pytest", 0, "1 passed in 0.1s"), _ran("s2", "pytest", 1, "1 failed in 0.1s"))
        failing = unresolved_failures(observe_plan(plan))
        assert [sid for sid, _ in failing] == ["s2"]

    def test_failed_dispatches_are_not_read_twice(self) -> None:
        step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="run_command", description="x",
                        arguments={"command": "pytest"}).started().failed("timeout", 1.0)
        assert observe_plan(_plan(step)) == []
