"""ADR-040: ``lint_code`` and ``check_format`` through the engineer's own sandbox substrate.

The runner is ``InMemorySandboxRunner``: a real temp directory and real subprocesses, so these tests
run whichever of ruff, mypy, eslint and prettier are installed on the machine and skip (never fake)
the ones that are not. The "tool is missing" behaviour is exercised against a runner that really
cannot start the binary (an empty PATH), not against a canned result.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.domain.errors import ToolExecutionError
from aura_core.ports.coding import SandboxResult
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.devtools import (
    DEVTOOL_NAMES,
    parse_eslint,
    parse_mypy,
    parse_prettier_check,
    parse_pyflakes,
    parse_ruff_check,
    parse_ruff_format_check,
)

needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")
needs_mypy = pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy is not installed")
needs_eslint = pytest.mark.skipif(shutil.which("eslint") is None, reason="eslint is not installed")
needs_prettier = pytest.mark.skipif(shutil.which("prettier") is None, reason="prettier is not installed")


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-devtools", deadline_s=120.0)


@pytest.fixture
def tools():
    runner = InMemorySandboxRunner()
    return {t.spec.name: t for t in build_sandbox_tools(runner)}, runner


async def _write(tools, ctx, path: str, content: str) -> None:
    await tools["write_file"].execute({"path": path, "content": content}, ctx)


UNTIDY = "import os\nimport sys\n\ndef f( a,b ):\n    x=1\n    return undefined_name\n"


class TestSpecs:
    def test_registered_with_the_sandbox_tools_and_engineer_gated(self, tools) -> None:
        by_name, _runner = tools
        assert DEVTOOL_NAMES <= set(by_name)
        for name in DEVTOOL_NAMES:
            spec = by_name[name].spec
            assert spec.required_capability == "engineer_agent"
            assert "untrusted_content" in spec.tags  # linter output quotes the project's own text

    def test_neither_tool_ever_writes(self, tools) -> None:
        """No auto-fix flag may reach the linters: read-only is part of why they are safe to run
        without a confirmation per call."""
        import inspect

        from aura_core.tools import devtools

        source = inspect.getsource(devtools)
        for flag in ('"--fix"', '"--write"', '"-w"', '"--unsafe-fixes"'):
            assert flag not in source


class TestParsers:
    def test_ruff_json_keeps_position_code_and_fixability(self) -> None:
        raw = json.dumps([
            {"code": "F401", "message": "`os` imported but unused", "filename": "/w/a.py",
             "location": {"row": 1, "column": 8}, "end_location": {"row": 1, "column": 10},
             "fix": {"edits": []}, "name": "unused-import"},
            {"code": None, "message": "SyntaxError: x", "filename": "/w/b.py",
             "location": {"row": 3, "column": 1}, "end_location": {"row": 3, "column": 2}, "fix": None},
            {"code": "E501", "message": "Line too long", "filename": "/w/c.py",
             "location": {"row": 9, "column": 100}, "end_location": None, "fix": None},
        ])
        found = parse_ruff_check(raw)
        assert found[0] == {
            "linter": "ruff", "file": "/w/a.py", "line": 1, "column": 8, "end_line": 1, "end_column": 10,
            "code": "F401", "rule_name": "unused-import", "severity": "warning",
            "message": "`os` imported but unused", "fixable": True,
        }
        assert found[1]["severity"] == "error"  # no rule code = the file did not parse
        assert found[2]["severity"] == "warning"
        assert found[2]["end_line"] is None

    def test_ruff_json_that_is_not_a_list_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            parse_ruff_check('{"oops": 1}')
        with pytest.raises(ValueError):
            parse_ruff_check("Traceback (most recent call last)")

    def test_mypy_text(self) -> None:
        out = (
            'src/a.py:3:5: error: Incompatible types in assignment (expression has type "str", '
            'variable has type "int")  [assignment]\n'
            "src/a.py:3:5: note: See the docs\n"
            "src/b.py:10: error: Name 'x' is not defined  [name-defined]\n"
            "C:\\w\\c.py:1:1: error: Invalid syntax  [syntax]\n"
            "Success: no issues\n"
        )
        found = parse_mypy(out)
        assert [(d["file"], d["line"], d["column"], d["severity"], d["code"]) for d in found] == [
            ("src/a.py", 3, 5, "error", "assignment"),
            ("src/a.py", 3, 5, "info", None),
            ("src/b.py", 10, None, "error", "name-defined"),
            ("C:\\w\\c.py", 1, 1, "error", "syntax"),
        ]

    def test_pyflakes_text(self) -> None:
        found = parse_pyflakes("a.py:1:1: 'os' imported but unused\na.py:6:12: undefined name 'zz'\n")
        assert [(d["line"], d["column"], d["severity"]) for d in found] == [(1, 1, "warning"), (6, 12, "error")]

    def test_eslint_json(self) -> None:
        raw = json.dumps([{"filePath": "/w/a.js", "messages": [
            {"ruleId": "no-unused-vars", "severity": 1, "message": "x unused", "line": 2, "column": 7,
             "endLine": 2, "endColumn": 8},
            {"ruleId": None, "severity": 2, "fatal": True, "message": "Parsing error", "line": 1, "column": 3},
        ]}])
        found = parse_eslint(raw)
        assert (found[0]["code"], found[0]["severity"], found[0]["end_column"]) == ("no-unused-vars", "warning", 8)
        assert (found[1]["code"], found[1]["severity"]) == (None, "error")
        with pytest.raises(ValueError):
            parse_eslint("Oops! Something went wrong")

    def test_ruff_format_old_and_new_text(self) -> None:
        assert [f["file"] for f in parse_ruff_format_check("Would reformat: a.py\nWould reformat: b/c.py\n")] == [
            "a.py", "b/c.py"]
        new = (
            "unformatted: File would be reformatted\n --> a.py:4:1\n  |\n3 |\n"
            "invalid-syntax: Expected a parameter\n --> bad.py:1:12\n"
        )
        got = parse_ruff_format_check(new)
        assert [(f["file"], f["status"], f["line"]) for f in got] == [
            ("a.py", "needs_formatting", 4), ("bad.py", "syntax_error", 1)]

    def test_prettier_check_text(self) -> None:
        err = (
            "Checking formatting...\n[error] syn.js: SyntaxError: Unexpected token (1:7)\n[error] > 1 | const = ;\n"
            "[warn] t.js\n[warn] Code style issues found in the above file. Run Prettier with --write to fix.\n"
        )
        got = parse_prettier_check("", err)
        assert [(f["file"], f["status"], f["line"]) for f in got] == [
            ("syn.js", "syntax_error", 1), ("t.js", "needs_formatting", None)]


class TestLintCode:
    @needs_ruff
    async def test_real_ruff_returns_structured_findings(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "pkg/a.py", UNTIDY)
        await _write(by_name, ctx, "ok.py", "x = 1\n")
        report = json.loads(await by_name["lint_code"].execute({"linter": "ruff"}, ctx))
        assert report["clean"] is False
        assert [(e["linter"], e["status"]) for e in report["linters"]] == [("ruff", "findings")]
        codes = {d["code"] for d in report["diagnostics"]}
        assert {"F401", "F821"} <= codes
        undefined = next(d for d in report["diagnostics"] if d["code"] == "F821")
        assert undefined["file"] == "pkg/a.py"  # relative to the workspace, not an absolute temp path
        assert (undefined["line"], undefined["column"], undefined["severity"]) == (6, 12, "error")
        assert "undefined_name" in undefined["message"]
        assert report["summary"]["errors"] >= 1 and report["summary"]["files"] == 1
        assert report["summary"]["truncated"] is False
        assert all(d["file"] != "ok.py" for d in report["diagnostics"])

    @needs_ruff
    async def test_a_clean_tree_is_clean(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "ok.py", "x = 1\n")
        report = json.loads(await by_name["lint_code"].execute({"paths": ["ok.py"]}, ctx))
        assert report["clean"] is True
        assert report["summary"]["total"] == 0
        assert report["linters"][0]["status"] == "ok"

    @needs_ruff
    async def test_truncation_is_counted_not_silent(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "many.py", "".join(f"import mod{i}\n" for i in range(30)))
        report = json.loads(await by_name["lint_code"].execute(
            {"linter": "ruff", "limit": 5}, ctx))
        assert report["summary"]["shown"] == 5 and len(report["diagnostics"]) == 5
        assert report["summary"]["truncated"] is True
        assert report["summary"]["withheld"] == report["summary"]["total"] - 5 > 0
        assert report["clean"] is False

    @needs_ruff
    async def test_a_syntax_error_is_an_error_finding_not_a_crash(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "bad.py", "def broken(:\n  pass\n")
        report = json.loads(await by_name["lint_code"].execute({"linter": "ruff"}, ctx))
        assert report["linters"][0]["status"] == "findings"
        assert report["diagnostics"][0]["severity"] == "error"

    @needs_mypy
    async def test_real_mypy(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "ty.py", 'x: int = "a"\n')
        report = json.loads(await by_name["lint_code"].execute({"linter": "mypy", "paths": ["ty.py"]}, ctx))
        assert report["linters"][0]["status"] == "findings"
        d = report["diagnostics"][0]
        assert (d["file"], d["line"], d["column"], d["code"], d["severity"]) == ("ty.py", 1, 10, "assignment", "error")

    @needs_eslint
    async def test_eslint_with_no_config_is_failed_not_clean(self, tools, ctx) -> None:
        """ESLint 9+ refuses to run without a config; that is a failed run, and must never read as a
        pass."""
        by_name, _ = tools
        await _write(by_name, ctx, "t.js", "var a = 1\n")
        report = json.loads(await by_name["lint_code"].execute({"paths": ["t.js"]}, ctx))
        assert report["clean"] is False
        assert report["linters"][0]["linter"] == "eslint"
        assert report["linters"][0]["status"] == "failed"
        assert "config" in report["linters"][0]["detail"].lower()

    @needs_eslint
    async def test_real_eslint_with_a_config(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "eslint.config.js",
                     "module.exports = [{ rules: { 'no-unused-vars': 'warn', 'no-undef': 'error' } }];\n")
        await _write(by_name, ctx, "t.js", "var a = 1;\nconsole.log(zzz);\n")
        report = json.loads(await by_name["lint_code"].execute({"paths": ["t.js"], "linter": "eslint"}, ctx))
        assert report["linters"][0]["status"] == "findings"
        by_code = {d["code"]: d for d in report["diagnostics"]}
        assert by_code["no-undef"]["severity"] == "error" and by_code["no-undef"]["line"] == 2
        assert by_code["no-unused-vars"]["severity"] == "warning"
        assert by_code["no-undef"]["file"] == "t.js"

    async def test_a_missing_linter_is_not_installed_never_clean(self, ctx, monkeypatch) -> None:
        """A runner that genuinely cannot start any linter (empty PATH): the answer is
        ``not_installed`` with ``clean`` false."""
        runner = InMemorySandboxRunner()
        by_name = {t.spec.name: t for t in build_sandbox_tools(runner)}
        await _write(by_name, ctx, "a.py", UNTIDY)
        monkeypatch.setenv("PATH", "/nonexistent-aura-path")
        report = json.loads(await by_name["lint_code"].execute({"linter": "ruff"}, ctx))
        assert report["clean"] is False
        assert report["linters"][0]["status"] == "not_installed"
        assert "not installed" in report["linters"][0]["detail"]
        assert report["diagnostics"] == []

    async def test_auto_falls_back_to_pyflakes_and_says_ruff_was_missing(self, ctx) -> None:
        """Scripted at the runner boundary (the only seam: which binaries exist on this machine)."""
        calls: list[list[str]] = []

        class Runner(InMemorySandboxRunner):
            async def run(self, handle, command, *, timeout_s):
                calls.append(list(command))
                if command[0] == "pwd":
                    return SandboxResult(stdout="/w\n", stderr="", exit_code=0)
                if command[:3] == ["python3", "-m", "pyflakes"] or command[0] == "pyflakes":
                    return SandboxResult(stdout="a.py:1:1: 'os' imported but unused\n", stderr="", exit_code=1)
                return SandboxResult(stdout="", stderr=f"{command[0]}: command not found", exit_code=127)

        by_name = {t.spec.name: t for t in build_sandbox_tools(Runner())}
        report = json.loads(await by_name["lint_code"].execute({"paths": ["a.py"]}, ctx))
        assert [(e["linter"], e["status"]) for e in report["linters"]] == [
            ("ruff", "not_installed"), ("pyflakes", "findings")]
        assert report["linters"][0]["superseded_by"] == "pyflakes"
        assert report["diagnostics"][0]["linter"] == "pyflakes"
        assert report["clean"] is False
        # Every candidate launcher for ruff was tried before giving up on it.
        assert ["ruff", "check", "--output-format", "json", "--no-cache", "a.py"] in calls

    async def test_auto_with_pyflakes_clean_is_clean(self, ctx) -> None:
        class Runner(InMemorySandboxRunner):
            async def run(self, handle, command, *, timeout_s):
                if command[0] == "pyflakes":
                    return SandboxResult(stdout="", stderr="", exit_code=0)
                return SandboxResult(stdout="", stderr="", exit_code=127)

        by_name = {t.spec.name: t for t in build_sandbox_tools(Runner())}
        report = json.loads(await by_name["lint_code"].execute({"paths": ["a.py"]}, ctx))
        assert report["clean"] is True

    async def test_unparseable_output_and_timeouts_are_failures(self, ctx) -> None:
        class Garbage(InMemorySandboxRunner):
            async def run(self, handle, command, *, timeout_s):
                return SandboxResult(stdout="<html>proxy error</html>", stderr="boom", exit_code=2)

        report = json.loads(await {t.spec.name: t for t in build_sandbox_tools(Garbage())}["lint_code"]
                            .execute({"linter": "ruff"}, ctx))
        assert report["linters"][0]["status"] == "failed" and report["clean"] is False

        class Slow(InMemorySandboxRunner):
            async def run(self, handle, command, *, timeout_s):
                return SandboxResult(stdout="", stderr="command timed out after 5s", exit_code=124)

        report = json.loads(await {t.spec.name: t for t in build_sandbox_tools(Slow())}["lint_code"]
                            .execute({"linter": "ruff", "timeout_s": 5}, ctx))
        assert report["linters"][0]["status"] == "timeout" and report["clean"] is False

    @pytest.mark.parametrize("bad", ["--config=/etc/x", "/etc/passwd", "../x", "a\nb", "C:\\x"])
    async def test_paths_cannot_become_options_or_escape(self, tools, ctx, bad) -> None:
        by_name, _ = tools
        with pytest.raises(ToolExecutionError):
            await by_name["lint_code"].execute({"paths": [bad]}, ctx)
        with pytest.raises(ToolExecutionError):
            await by_name["check_format"].execute({"paths": [bad]}, ctx)

    async def test_an_unknown_linter_is_refused(self, tools, ctx) -> None:
        by_name, _ = tools
        with pytest.raises(ToolExecutionError):
            await by_name["lint_code"].execute({"linter": "nonsense"}, ctx)


class TestCheckFormat:
    @needs_ruff
    async def test_real_ruff_format_check(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "a.py", UNTIDY)
        await _write(by_name, ctx, "ok.py", "x = 1\n")
        report = json.loads(await by_name["check_format"].execute({"formatter": "ruff"}, ctx))
        assert report["formatted"] is False
        assert report["formatters"][0]["status"] == "findings"
        assert [f["file"] for f in report["files"]] == ["a.py"]
        assert report["files"][0]["status"] == "needs_formatting"
        assert report["summary"]["needs_formatting"] == 1 and report["summary"]["syntax_errors"] == 0

    @needs_ruff
    async def test_formatted_tree(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "ok.py", "x = 1\n")
        report = json.loads(await by_name["check_format"].execute({"paths": ["ok.py"]}, ctx))
        assert report["formatted"] is True and report["files"] == []

    @needs_ruff
    async def test_check_never_changes_the_file(self, tools, ctx) -> None:
        by_name, runner = tools
        await _write(by_name, ctx, "a.py", UNTIDY)
        await by_name["check_format"].execute({"formatter": "ruff"}, ctx)
        shown = await by_name["run_command"].execute({"command": "cat a.py"}, ctx)
        assert "def f( a,b ):" in shown

    @needs_ruff
    async def test_syntax_error_is_reported_per_file(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "bad.py", "def broken(:\n  pass\n")
        report = json.loads(await by_name["check_format"].execute({"formatter": "ruff"}, ctx))
        assert report["formatted"] is False
        assert report["files"][0]["status"] == "syntax_error"
        assert report["summary"]["syntax_errors"] == 1

    @needs_prettier
    async def test_real_prettier(self, tools, ctx) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "t.js", "const a = {b:1}\nfunction f( ){return a}\n")
        await _write(by_name, ctx, "good.js", "const a = 1;\n")
        report = json.loads(await by_name["check_format"].execute({"paths": ["t.js", "good.js"]}, ctx))
        assert report["formatters"][0]["formatter"] == "prettier"
        assert [f["file"] for f in report["files"]] == ["t.js"]
        assert report["formatted"] is False

    async def test_a_missing_formatter_is_not_formatted(self, tools, ctx, monkeypatch) -> None:
        by_name, _ = tools
        await _write(by_name, ctx, "a.py", UNTIDY)
        monkeypatch.setenv("PATH", "/nonexistent-aura-path")
        report = json.loads(await by_name["check_format"].execute({"formatter": "ruff"}, ctx))
        assert report["formatted"] is False
        assert report["formatters"][0]["status"] == "not_installed"
        assert os.environ["PATH"] == "/nonexistent-aura-path"
