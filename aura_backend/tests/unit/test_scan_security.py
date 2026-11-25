"""ADR-050: ``scan_security`` runs Semgrep, Gitleaks and Bandit in the engineer's sandbox.

The parsers are tested on the real output of those tools (``tests/fixtures/scanners``: semgrep 1.187, bandit
1.9.4, gitleaks 8.28 over a small vulnerable project). The tool is tested through ``InMemorySandboxRunner``
-- a real temp directory and real subprocesses -- so each real-run test uses whichever scanner is installed
on the machine and **skips, never fakes**, the ones that are not. "Not installed" is exercised against a
runner that genuinely cannot start the binary (an empty PATH).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.domain.errors import ToolExecutionError
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.devtools_security import (
    SEMGREP_RULES_DIR, normalise_semgrep_id, parse_bandit, parse_gitleaks, parse_semgrep, scanner_args)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "scanners"
needs_semgrep = pytest.mark.skipif(shutil.which("semgrep") is None or not Path(SEMGREP_RULES_DIR).is_dir(),
                                   reason="semgrep or its bundled rules are not installed")
needs_gitleaks = pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks is not installed")
needs_bandit = pytest.mark.skipif(shutil.which("bandit") is None, reason="bandit is not installed")
TOKEN = "ghp_" + ("aB3dE5gH7jK9mN1pQ3sT5vW7yZ0aB3dE5gH7")[:36]


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-scan", deadline_s=900.0)


@pytest.fixture
def tools():
    runner = InMemorySandboxRunner()
    return {t.spec.name: t for t in build_sandbox_tools(runner)}


async def _write(tools, ctx, path: str, content: str) -> None:
    await tools["write_file"].execute({"path": path, "content": content}, ctx)


async def scan(tools, ctx, **args) -> dict:
    return json.loads(await tools["scan_security"].execute(args, ctx))


VULN_PY = ("import subprocess, hashlib\n\ndef run(cmd):\n    subprocess.call(cmd, shell=True)\n"
           "    return hashlib.md5(b'x').hexdigest()\n")
VULN_JS = "const out = eval(userInput);\ndocument.getElementById('a').innerHTML = userHtml;\n"


class TestParsersOnRealOutput:
    def test_semgrep(self) -> None:
        findings, errors = parse_semgrep((FIXTURES / "semgrep.json").read_text())
        assert errors == [] and len(findings) == 6
        assert {f["file"] for f in findings} == {"app.py", "web.js"}
        sample = next(f for f in findings if f["file"] == "app.py" and f["line"] == 3)
        assert sample["severity"] == "error" and sample["scanner"] == "semgrep"
        assert sample["code"].startswith("python.lang.security") and "scratchpad" not in sample["code"]
        assert any(f["cwe"] for f in findings) and all(f["message"] for f in findings)

    def test_bandit(self) -> None:
        findings, errors = parse_bandit((FIXTURES / "bandit.json").read_text())
        assert findings and errors == []
        shell = next(f for f in findings if f["code"] == "B602")
        assert shell["severity"] == "error" and shell["file"] == "app.py" and shell["cwe"] == ["CWE-78"]
        assert {f["severity"] for f in findings} <= {"error", "warning", "info"}

    def test_gitleaks_reads_only_the_safe_fields(self) -> None:
        raw = (FIXTURES / "gitleaks.json").read_text()
        findings = parse_gitleaks(raw)
        assert len(findings) == 1 and findings[0]["code"] == "github-pat" and findings[0]["file"] == "cfg.py"
        assert findings[0]["fingerprint"] and set(findings[0]) == {
            "scanner", "file", "line", "end_line", "code", "severity", "message", "fingerprint", "commit", "snippet"}
        hostile = json.dumps([{"RuleID": "x", "File": "f", "StartLine": 1, "Secret": TOKEN, "Match": TOKEN,
                               "Author": "someone", "Email": "a@b.c", "Message": TOKEN}])
        assert TOKEN not in json.dumps(parse_gitleaks(hostile)), "a report that leaks is still not passed on"

    def test_a_snippet_never_carries_a_credential(self) -> None:
        doc = {"results": [{"check_id": "r", "path": "a.py", "start": {"line": 1}, "end": {"line": 1},
                            "extra": {"message": "m", "severity": "ERROR", "lines": f'k = "{TOKEN}"'}}], "errors": []}
        findings, _ = parse_semgrep(json.dumps(doc))
        assert TOKEN not in json.dumps(findings) and "REDACTED" in findings[0]["snippet"]

    @pytest.mark.parametrize("parser,text", [(parse_semgrep, "not json"), (parse_semgrep, "[]"), (parse_bandit, "{}"),
                                             (parse_gitleaks, "{}"), (parse_gitleaks, '["x"]')])
    def test_unreadable_output_is_a_value_error_not_a_clean_result(self, parser, text) -> None:
        with pytest.raises(ValueError):
            parser(text)

    def test_semgrep_rule_ids_lose_the_directory_the_rules_sit_in(self) -> None:
        assert normalise_semgrep_id("opt.aura.semgrep-rules.python.lang.security.audit.x") == "python.lang.security.audit.x"
        assert normalise_semgrep_id("a.b.c.d.e.f") == "c.d.e.f"

    def test_the_commands_are_fixed_argv_with_the_bundled_rules_only(self) -> None:
        (semgrep,) = scanner_args("semgrep", ["src", "-x"])
        assert "--metrics=off" in semgrep and "--disable-version-check" in semgrep
        assert "auto" not in semgrep and all(c.startswith(SEMGREP_RULES_DIR) for c in semgrep if c.startswith("/opt"))
        assert semgrep[-2:] == ["src", "-x"], "paths are the last arguments, never interpreted by a shell"
        runs = scanner_args("gitleaks", ["a", "b"])
        assert len(runs) == 2 and all("--redact" in r and "--no-git" in r for r in runs)
        assert "--no-git" not in scanner_args("gitleaks", ["a"], history=True)[0]
        with pytest.raises(ValueError):
            scanner_args("trivy", ["a"])


class TestTheTool:
    def test_it_is_registered_engineer_gated_and_untrusted(self, tools) -> None:
        spec = tools["scan_security"].spec
        assert spec.required_capability == "engineer_agent" and "untrusted_content" in spec.tags
        assert len(spec.description) < 400, "the tool catalogue is part of every planning prompt"

    def test_no_fix_or_upload_flag_exists(self) -> None:
        import inspect

        from aura_core.tools import devtools_security

        source = inspect.getsource(devtools_security)
        for flag in ('"--autofix"', '"--fix"', '"--metrics=on"', '"--config", "auto"', '"p/'):
            assert flag not in source

    async def test_a_missing_scanner_is_never_clean(self, tools, ctx, monkeypatch) -> None:
        await _write(tools, ctx, "a.py", "x = 1\n")
        monkeypatch.setenv("PATH", "/nonexistent-aura-path")
        report = await scan(tools, ctx)
        assert report["clean"] is False
        assert {s["scanner"]: s["status"] for s in report["scanners"]} == {
            "semgrep": "not_installed", "gitleaks": "not_installed", "bandit": "not_installed"}

    async def test_bad_arguments_are_refused(self, tools, ctx) -> None:
        for args in ({"scanners": ["trivy"]}, {"scanners": []}, {"paths": ["../etc"]}, {"paths": ["/etc"]},
                     {"paths": [str(i) for i in range(6)], "scanners": ["gitleaks"]}):
            with pytest.raises(ToolExecutionError):
                await tools["scan_security"].execute(args, ctx)

    @needs_bandit
    async def test_bandit_finds_the_shell_call_in_a_real_run(self, tools, ctx) -> None:
        await _write(tools, ctx, "app.py", VULN_PY)
        report = await scan(tools, ctx, scanners=["bandit"])
        assert report["clean"] is False and report["scanners"][0]["status"] == "findings"
        assert any(f["code"] == "B602" and f["file"] == "app.py" for f in report["findings"])
        await _write(tools, ctx, "ok.py", "def add(a, b):\n    return a + b\n")
        clean = await scan(tools, ctx, scanners=["bandit"], paths=["ok.py"])
        assert clean["clean"] is True and clean["scanners"][0]["status"] == "ok"

    @needs_gitleaks
    async def test_gitleaks_finds_a_secret_and_never_returns_it(self, tools, ctx) -> None:
        await _write(tools, ctx, "cfg.py", f'GITHUB_TOKEN = "{TOKEN}"\n')
        report = await scan(tools, ctx, scanners=["gitleaks"])
        assert report["scanners"][0]["status"] == "findings"
        assert report["findings"][0]["code"] == "github-pat" and report["findings"][0]["file"] == "cfg.py"
        assert TOKEN not in json.dumps(report) and TOKEN[4:] not in json.dumps(report)
        await _write(tools, ctx, "cfg.py", "GITHUB_TOKEN = None\n")
        assert (await scan(tools, ctx, scanners=["gitleaks"]))["clean"] is True

    @needs_gitleaks
    async def test_gitleaks_history_finds_a_secret_that_was_deleted(self, tools, ctx) -> None:
        runner = tools["run_command"]
        await _write(tools, ctx, "cfg.py", f'GITHUB_TOKEN = "{TOKEN}"\n')
        for cmd in ("git init -q .", "git add -A", "git -c user.email=a@b -c user.name=n commit -qm add",
                    "git rm -q cfg.py", "git -c user.email=a@b -c user.name=n commit -qm remove"):
            await runner.execute({"command": cmd}, ctx)
        assert (await scan(tools, ctx, scanners=["gitleaks"]))["clean"] is True, "gone from the working tree"
        deep = await scan(tools, ctx, scanners=["gitleaks"], history=True)
        assert deep["clean"] is False and deep["findings"][0]["commit"]

    @needs_semgrep
    async def test_semgrep_finds_issues_in_python_and_javascript(self, tools, ctx) -> None:
        await _write(tools, ctx, "app.py", VULN_PY)
        await _write(tools, ctx, "web.js", VULN_JS)
        report = await scan(tools, ctx, scanners=["semgrep"])
        assert report["scanners"][0]["status"] == "findings"
        assert {f["file"] for f in report["findings"]} >= {"app.py", "web.js"}
        assert report["summary"]["error"] >= 1 and report["findings"][0]["severity"] == "error"

    @needs_semgrep
    async def test_a_rule_file_in_the_repository_is_not_used(self, tools, ctx) -> None:
        """The scanned repository must not choose its own rules (or silence the bundled ones)."""
        await _write(tools, ctx, ".semgrep.yml", "rules: []\n")
        await _write(tools, ctx, ".semgrepignore", "*\n")
        await _write(tools, ctx, "app.py", VULN_PY)
        report = await scan(tools, ctx, scanners=["semgrep"])
        assert report["findings"], "a .semgrepignore in the workspace does not hide the code"

    @needs_semgrep
    async def test_a_missing_rule_set_is_a_failure_not_a_clean_scan(self, tools, ctx, monkeypatch) -> None:
        import aura_core.tools.devtools_security as sec

        monkeypatch.setattr(sec, "SEMGREP_RULES_DIR", "/nonexistent/rules")
        await _write(tools, ctx, "a.py", "x = 1\n")
        report = await scan(tools, ctx, scanners=["semgrep"])
        assert report["clean"] is False and report["scanners"][0]["status"] in ("failed", "incomplete")

    @needs_bandit
    async def test_a_file_the_scanner_cannot_parse_makes_the_run_incomplete(self, tools, ctx) -> None:
        await _write(tools, ctx, "broken.py", "def f(:\n")
        report = await scan(tools, ctx, scanners=["bandit"])
        assert report["clean"] is False and report["scanners"][0]["status"] == "incomplete"

    async def test_the_default_scan_reports_each_scanner_separately(self, tools, ctx) -> None:
        await _write(tools, ctx, "a.py", "x = 1\n")
        report = await scan(tools, ctx)
        assert [s["scanner"] for s in report["scanners"]] == ["semgrep", "gitleaks", "bandit"]
        assert report["summary"]["total"] == len(report["findings"]) or report["summary"]["truncated"]
