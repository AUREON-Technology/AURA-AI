"""Turn a tool result into an observation the agent loop can act on (state graph, ADR-039).

``run_command`` returns ``ok=True`` whenever the *tool* worked -- the command running and exiting
non-zero is still a successful dispatch, with the exit status only in the result text
(``exit_code=1\\n--- stdout ---...``). The loop therefore saw a plan whose every step succeeded and
stopped with ``completed`` while the test suite it had just run was red. This module reads the
structure the tool *already* returns -- the ``exit_code=`` header and the test runner's own
summary -- so the loop can treat a failing run as what it is, without changing any tool's result
shape.

Everything here is pure, stdlib-only and tolerant: a result that is not what it expects (garbage,
a truncated stream, a runner we do not know) yields ``None`` rather than an exception, because an
observation that cannot be parsed must degrade to "no signal", never to a crashed turn.

Runners understood: pytest, unittest, jest, vitest and ``go test``, plus the JSON that
``generate_and_run_tests`` returns.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..domain.planning import Plan, StepKind, StepStatus
from .stuck import stable_text

__all__ = [
    "observe_plan",
    "unresolved_failures",
    "TestRun",
    "ToolObservation",
    "parse_test_output",
    "parse_command_result",
    "is_test_command",
    "observe_tool_result",
    "OBS_TESTS_FAILED",
    "OBS_TESTS_PASSED",
    "OBS_COMMAND_FAILED",
    "OBS_COMMAND_OK",
]

OBS_TESTS_FAILED = "tests_failed"
OBS_TESTS_PASSED = "tests_passed"
OBS_COMMAND_FAILED = "command_failed"
OBS_COMMAND_OK = "command_ok"

#: Parsing is bounded: ``run_command`` already caps a result at ~20k characters, but this is a
#: pure function that may be handed anything.
_MAX_INPUT = 200_000
_MAX_NAMES = 8

_EXIT_HEADER = re.compile(r"^\s*exit_code=(-?\d+)", re.M)
_STDOUT_MARK = "--- stdout ---"
_STDERR_MARK = "--- stderr ---"

#: A command that is, by its own words, running tests or a build whose failure is the agent's
#: problem to fix. A non-zero exit from one of these is "tests failed" even when the runner's
#: summary line is not recognised (a crash before collection, for instance).
_TEST_COMMAND = re.compile(
    r"(?<![\w-])(?:pytest|py\.test|tox|nox|jest|vitest|mocha|rspec|phpunit|"
    r"python3?\s+-m\s+(?:pytest|unittest)|unittest|go\s+test|cargo\s+test|"
    r"(?:npm|yarn|pnpm)\s+(?:run\s+)?test|make\s+(?:test|check)|mvn\s+(?:-\S+\s+)*test|"
    r"gradlew?\s+test|dotnet\s+test|flutter\s+test|dart\s+test)(?![\w-])",
    re.I,
)


@dataclass(frozen=True, slots=True)
class TestRun:
    """What one test-runner invocation reported."""

    __test__ = False  # not a pytest class, whatever the name says

    runner: str
    failed: int = 0
    passed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: tuple[str, ...] = ()
    summary: str = ""

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.errors == 0

    def describe(self) -> str:
        bits = [f"{n} {label}" for n, label in (
            (self.failed, "failed"), (self.errors, "errors"), (self.passed, "passed"),
            (self.skipped, "skipped"),
        ) if n]
        text = f"{self.runner}: {', '.join(bits) or 'no counts'}"
        if self.failures:
            text += f"; failing: {', '.join(self.failures[:_MAX_NAMES])}"
        return text


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """One tool result, read for what it says about the work (not about the tool)."""

    kind: str
    tool: str
    summary: str
    #: Stable across reruns that fail the same way; differs when the failure changes. What the
    #: stuck detector compares to tell "still broken, differently" from "still broken, identically".
    signature: str = ""
    exit_code: int | None = None

    @property
    def failing(self) -> bool:
        return self.kind in (OBS_TESTS_FAILED, OBS_COMMAND_FAILED)

    @property
    def family(self) -> str:
        return "tests" if self.kind in (OBS_TESTS_FAILED, OBS_TESTS_PASSED) else "command"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "tool": self.tool, "summary": self.summary,
                "exit_code": self.exit_code}


# -- command results ---------------------------------------------------------------------------


def parse_command_result(text: str) -> tuple[int | None, str, str]:
    """``(exit_code, stdout, stderr)`` from a ``run_command`` result; ``(None, text, "")`` if the
    text does not have the tool's shape."""
    text = (text or "")[-_MAX_INPUT:]
    match = _EXIT_HEADER.search(text)
    if match is None:
        return None, text, ""
    exit_code = int(match.group(1))
    out_at = text.find(_STDOUT_MARK, match.end())
    err_at = text.find(_STDERR_MARK, match.end())
    if out_at < 0 and err_at < 0:
        return exit_code, "", ""
    if err_at >= 0 and (out_at < 0 or err_at > out_at):
        stdout = text[out_at + len(_STDOUT_MARK):err_at] if out_at >= 0 else ""
        stderr = text[err_at + len(_STDERR_MARK):]
    else:  # stderr printed first: not the tool's layout, but be tolerant
        stdout = text[out_at + len(_STDOUT_MARK):]
        stderr = text[err_at + len(_STDERR_MARK):out_at] if err_at >= 0 else ""
    return exit_code, stdout.strip("\n"), stderr.strip("\n")


def is_test_command(command: Any) -> bool:
    return isinstance(command, str) and bool(_TEST_COMMAND.search(command))


# -- runner parsers ----------------------------------------------------------------------------

_PYTEST_SUMMARY = re.compile(
    r"^\W*((?:\d+\s+(?:failed|passed|skipped|errors?|xfailed|xpassed|warnings?|deselected|"
    r"rerun)(?:,\s*|\s+and\s+)?)+)\s+in\s+[\d.]+\s*s",
    re.M | re.I,
)
_COUNT = re.compile(r"(\d+)\s+(failed|passed|skipped|errors?|xfailed|xpassed)", re.I)
_PYTEST_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s+-\s|\s*$)", re.M)
_PYTEST_NO_TESTS = re.compile(r"no tests ran|ERROR collecting|errors? during collection", re.I)

_UNITTEST_RAN = re.compile(r"^Ran\s+(\d+)\s+tests?\s+in\s+[\d.]+s", re.M)
_UNITTEST_RESULT = re.compile(r"^(OK|FAILED)\b(?:\s*\(([^)]*)\))?", re.M)
_UNITTEST_FAIL_NAME = re.compile(r"^(?:FAIL|ERROR):\s+(\S+)\s+\(([^)]+)\)", re.M)

_JEST_TESTS = re.compile(r"^Tests:\s+(.*?)\s*$", re.M)
_JEST_BULLET = re.compile(r"^\s*●\s+(.+?)\s*$", re.M)
_VITEST_TESTS = re.compile(r"^\s*Tests\s+(.*?)\s*$", re.M)
_VITEST_FAIL = re.compile(r"^\s*(?:FAIL|×)\s+(.+?)\s*$", re.M)

_GO_FAIL_NAME = re.compile(r"^\s*--- FAIL:\s+(\S+)", re.M)
_GO_PKG_RESULT = re.compile(r"^(ok|FAIL)\s+\S+\s+[\d.]+s", re.M)
_GO_PKG_FAIL = re.compile(r"^FAIL\s+\S+", re.M)


def _counts(text: str) -> dict[str, int]:
    out = {"failed": 0, "passed": 0, "skipped": 0, "errors": 0}
    for number, label in _COUNT.findall(text):
        label = label.lower()
        if label.startswith("error"):
            out["errors"] += int(number)
        elif label in out:
            out[label] += int(number)
    return out


def _names(found: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return tuple(seen[:_MAX_NAMES])


def _parse_pytest(text: str) -> TestRun | None:
    summaries = list(_PYTEST_SUMMARY.finditer(text))
    names = _names(_PYTEST_FAILED_LINE.findall(text))
    if summaries:
        counts = _counts(summaries[-1].group(1))
        return TestRun("pytest", counts["failed"], counts["passed"], counts["errors"],
                       counts["skipped"], names, summaries[-1].group(0).strip(" =-"))
    if _PYTEST_NO_TESTS.search(text) and ("pytest" in text.lower() or "collect" in text.lower()):
        return TestRun("pytest", 0, 0, 1, 0, names, "pytest: no tests ran / collection error")
    return None


def _parse_unittest(text: str) -> TestRun | None:
    ran = _UNITTEST_RAN.search(text)
    if ran is None:
        return None
    result = None
    for result in _UNITTEST_RESULT.finditer(text, ran.end()):
        pass
    total = int(ran.group(1))
    failed = errors = 0
    if result is not None and result.group(1) == "FAILED":
        for key, value in re.findall(r"(failures|errors)=(\d+)", result.group(2) or ""):
            if key == "failures":
                failed = int(value)
            else:
                errors = int(value)
        if not (failed or errors):
            failed = 1
    elif result is None:
        return None
    names = _names([f"{m.group(2)}.{m.group(1)}" for m in _UNITTEST_FAIL_NAME.finditer(text)])
    return TestRun("unittest", failed, max(0, total - failed - errors), errors, 0, names,
                   f"Ran {total} tests; {result.group(0).strip()}")


def _parse_jest(text: str) -> TestRun | None:
    match = _JEST_TESTS.search(text)
    if match is None or "total" not in match.group(1):
        return None
    counts = _counts(match.group(1))
    names = _names([m.group(1) for m in _JEST_BULLET.finditer(text)
                    if "›" in m.group(1) or "Test suite failed" not in m.group(1)])
    return TestRun("jest", counts["failed"], counts["passed"], 0, counts["skipped"], names,
                   match.group(0).strip())


def _parse_vitest(text: str) -> TestRun | None:
    match = None
    for match in _VITEST_TESTS.finditer(text):
        if re.search(r"\d+\s+(?:failed|passed)", match.group(1)):
            break
    else:
        match = None
    if match is None:
        return None
    counts = _counts(match.group(1))
    names = _names([m.group(1) for m in _VITEST_FAIL.finditer(text)])
    return TestRun("vitest", counts["failed"], counts["passed"], 0, counts["skipped"], names,
                   match.group(0).strip())


def _parse_go(text: str) -> TestRun | None:
    results = _GO_PKG_RESULT.findall(text)
    failures = _names(_GO_FAIL_NAME.findall(text))
    if not results and not failures:
        return None
    failed_pkgs = len(_GO_PKG_FAIL.findall(text))
    failed = max(len(failures), failed_pkgs if not failures else len(failures))
    passed_pkgs = sum(1 for r in results if r == "ok")
    return TestRun("go", failed, passed_pkgs, 0, 0, failures,
                   f"go test: {failed_pkgs} package(s) failed, {passed_pkgs} ok")


def _parse_json_result(text: str) -> TestRun | None:
    """The JSON ``generate_and_run_tests`` returns: ``{"passed": bool, "counts": {...}, ...}``."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, Mapping) or not isinstance(data.get("passed"), bool):
        return None
    counts = data.get("counts") if isinstance(data.get("counts"), Mapping) else {}

    def num(key: str) -> int:
        value = counts.get(key, 0)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    failed, errors = num("failed"), num("errors")
    if not data["passed"] and not (failed or errors):
        failed = 1  # it says it did not pass; a count of zero must not read as green
    error_text = data.get("error")
    return TestRun("pytest", failed, num("passed"), errors, num("skipped"), (),
                   str(error_text)[:160] if error_text else "generated tests "
                   + ("passed" if data["passed"] else "failed"))


def parse_test_output(text: str) -> TestRun | None:
    """The test run a block of output describes, or ``None`` if it does not look like one.

    Order matters only where formats overlap (a vitest and a jest summary both start ``Tests``);
    the first parser that finds its own summary line wins. ``None`` is a normal answer.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    text = text[-_MAX_INPUT:]
    try:
        for parser in (_parse_json_result, _parse_pytest, _parse_unittest, _parse_jest,
                       _parse_vitest, _parse_go):
            run = parser(text)
            if run is not None:
                return run
    except Exception:  # pragma: no cover - a parser bug must not become a crashed turn
        return None
    return None


# -- the observation ---------------------------------------------------------------------------


def _signature(*parts: Any) -> str:
    # Volatile text (timestamps, durations, temp and log paths) is stripped first: two runs of the
    # same failing command must have the same signature or the stuck detector never sees a repeat.
    raw = "|".join(stable_text(str(p)) for p in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]


def observe_tool_result(
    tool: str, arguments: Mapping[str, Any] | None, text: str | None,
) -> ToolObservation | None:
    """What a *successfully dispatched* tool call says about the work, or ``None`` for no signal.

    Only ``run_command`` and ``generate_and_run_tests`` are read: they are the tools whose "ok"
    does not mean the work went well. A failed dispatch (``ok=False``) is already a failed step and
    needs no second reading.
    """
    if not isinstance(text, str):
        return None
    if tool == "generate_and_run_tests":
        run = parse_test_output(text)
        if run is None:
            return None
        if run.ok:
            return ToolObservation(OBS_TESTS_PASSED, tool, run.describe(),
                                   _signature("passed", run.runner))
        return ToolObservation(OBS_TESTS_FAILED, tool, run.describe(),
                               _signature(run.runner, run.failures, run.failed, run.errors))
    if tool != "run_command":
        return None
    arguments = arguments or {}
    command = arguments.get("command")
    exit_code, stdout, stderr = parse_command_result(text)
    run = parse_test_output(f"{stdout}\n{stderr}") if (stdout or stderr) else None
    if run is not None and not run.ok:
        return ToolObservation(OBS_TESTS_FAILED, tool, run.describe(),
                               _signature(run.runner, run.failures, run.failed, run.errors),
                               exit_code)
    if exit_code is None:
        return None  # not the tool's shape: no claim either way
    if exit_code == 0:
        if run is not None:
            return ToolObservation(OBS_TESTS_PASSED, tool, run.describe(),
                                   _signature("passed", run.runner), 0)
        if is_test_command(command):
            return ToolObservation(OBS_TESTS_PASSED, tool, "test command exited 0",
                                   _signature("passed", "cmd"), 0)
        return ToolObservation(OBS_COMMAND_OK, tool, "command exited 0", _signature("ok"), 0)
    tail = (stderr or stdout).strip().splitlines()
    last = tail[-1].strip()[:160] if tail else ""
    if is_test_command(command) or run is not None:
        summary = f"test command exited {exit_code}" + (f": {last}" if last else "")
        return ToolObservation(OBS_TESTS_FAILED, tool, summary,
                               _signature("exit", exit_code, last), exit_code)
    if exit_code == 1 and not stdout.strip() and not stderr.strip():
        # `grep` with no match, `diff` with no difference, `test -f` -- a status with no output is
        # an answer, not a failure worth a replan.
        return ToolObservation(OBS_COMMAND_OK, tool, "command exited 1 with no output",
                               _signature("ok", 1), 1)
    summary = f"command exited {exit_code}" + (f": {last}" if last else "")
    return ToolObservation(OBS_COMMAND_FAILED, tool, summary,
                           _signature("exit", exit_code, last), exit_code)


def observe_plan(plan: Plan) -> list[tuple[str, ToolObservation]]:
    """``(step_id, observation)`` for every succeeded tool step that says something about the
    work, in plan order. A failed *dispatch* is a failed step already and is not read twice."""
    out: list[tuple[str, ToolObservation]] = []
    for step in plan.steps:
        if step.kind is StepKind.TOOL and step.status is StepStatus.SUCCEEDED and step.tool:
            try:
                observation = observe_tool_result(step.tool, step.arguments, step.result)
            except Exception:  # pragma: no cover - an observation is an aid, never a failure
                observation = None
            if observation is not None:
                out.append((step.id.value, observation))
    return out


def unresolved_failures(
    observations: list[tuple[str, ToolObservation]],
) -> list[tuple[str, ToolObservation]]:
    """The failing observations nothing later fixed: the last one of each family (test runs;
    other commands) if it is a failure. A red test run followed by a green one is resolved."""
    out: list[tuple[str, ToolObservation]] = []
    for family in ("tests", "command"):
        mine = [pair for pair in observations if pair[1].family == family]
        if mine and mine[-1][1].failing:
            out.append(mine[-1])
    return out
