"""Engineer developer tools: ``lint_code`` and ``check_format`` (ADR-040).

The engineer could already run ``ruff`` through ``run_command``, but what came back was a screen of
text the model had to read, count and re-parse, with a "clean" run indistinguishable from a tool that
was never installed (``sh: 1: ruff: not found`` is easy to misread as "no findings"). These two tools
run the same linters through the same ``SandboxRunner`` -- so they land on the user's paired desktop
or in the server sandbox exactly where ``run_command`` does -- and return **structured** diagnostics:

* one record per finding (``file``, ``line``, ``column``, ``code``, ``severity``, ``message``), parsed
  from each tool's own machine-readable output (``ruff check --output-format json``, ``eslint -f
  json``, mypy's ``--show-column-numbers`` text, pyflakes' ``file:line:col: message``);
* per-linter **status**: ``ok``, ``findings``, ``not_installed``, ``timeout`` or ``failed``. A linter
  that is not installed, crashed, timed out or printed something unparseable is *never* reported as
  clean: the top-level ``clean`` flag is true only when every requested linter actually ran and found
  nothing;
* counts (``errors``, ``warnings``, ``info``, ``files``) and a ``truncated`` flag, with the number of
  findings withheld, so a 4,000-finding run never silently becomes 200.

Nothing here builds a shell string: argv is assembled from validated parts and handed to the runner
directly (the same discipline as ``tools/desktop_ops.py``), so a path argument cannot become an
option or a second command.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool
from .desktop_ops import _relative_path
from .devtools_security import SCANNERS, parse_bandit, parse_gitleaks, parse_semgrep, scanner_args

__all__ = ["build_devtools", "DEVTOOL_NAMES", "parse_mypy", "parse_pyflakes", "parse_ruff_check", "parse_eslint",
           "parse_prettier_check", "parse_ruff_format_check"]

_ENGINEER_CAPABILITY = "engineer_agent"

#: Names, for persona grants (``agents/registry.py``) and the tests that pin them.
DEVTOOL_NAMES: frozenset[str] = frozenset({"lint_code", "check_format", "scan_security"})

#: Findings returned in one result. The rest are counted, not listed: a result the model cannot fit
#: into its context is worse than a short one that says how much it left out.
_DEFAULT_MAX_DIAGNOSTICS = 200
_HARD_MAX_DIAGNOSTICS = 1_000
#: Most of one tool's output that is parsed. A linter that prints more than this on a single run is
#: reported as failed (its JSON would be cut mid-record) rather than parsed from a fragment.
_MAX_OUTPUT_CHARS = 8_000_000
_EXCERPT_CHARS = 600

_JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
#: Ruff's own list of rules that mean "this will fail at runtime or does not parse" (E9, F63, F7, F82).
_RUFF_ERROR_PREFIXES = ("E9", "F63", "F7", "F82")

#: How to start each tool, in the order tried. A bare name first, then the interpreter/npx forms, so a
#: tool installed only into a project's virtualenv or ``node_modules`` is still found.
_LAUNCHERS: dict[str, tuple[tuple[str, ...], ...]] = {
    "ruff": (("ruff",), ("python3", "-m", "ruff"), ("python", "-m", "ruff")),
    "pyflakes": (("pyflakes",), ("python3", "-m", "pyflakes"), ("python", "-m", "pyflakes")),
    "mypy": (("mypy",), ("python3", "-m", "mypy"), ("python", "-m", "mypy")),
    "eslint": (("eslint",), ("npx", "--no-install", "eslint")),
    "prettier": (("prettier",), ("npx", "--no-install", "prettier")),
    "semgrep": (("semgrep",), ("python3", "-m", "semgrep")),
    "gitleaks": (("gitleaks",),),
    "bandit": (("bandit",), ("python3", "-m", "bandit")),
}

#: Phrases on a launcher's *stderr* that mean the program (not the project) is absent. stdout is never
#: consulted: a finding's message may legitimately say "not found".
_MISSING_STDERR = (
    "command not found", ": not found", "is not recognized", "was not found", "no module named",
    "could not determine executable to run", "missing packages and no yes option", "npx canceled",
    "no such file or directory: '",
)


@dataclass(frozen=True)
class _Outcome:
    """What running one tool came to, before it is turned into a report entry."""

    status: str  # ok | findings | not_installed | timeout | failed
    diagnostics: list[dict[str, Any]]
    exit_code: int | None = None
    launcher: str | None = None
    detail: str | None = None


# --------------------------------------------------------------------------------------
# Parsers: pure functions of a tool's output, tested on their own
# --------------------------------------------------------------------------------------


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _short(text: Any, limit: int = 500) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_ruff_check(stdout: str) -> list[dict[str, Any]]:
    """``ruff check --output-format json`` -> diagnostics. Raises ``ValueError`` on anything else."""
    data = json.loads(stdout or "[]")
    if not isinstance(data, list):
        raise ValueError("ruff did not print a JSON array")
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("ruff printed a record that is not an object")
        start = item.get("location") or {}
        end = item.get("end_location") or {}
        code = item.get("code")
        severity = item.get("severity")  # newer ruff says so itself; older does not
        if severity not in ("error", "warning", "info"):
            # Ruff gives no severity. A syntax error (no rule code) and the rules it documents as
            # "fails at runtime" are errors; everything else is style or likely-bug advice.
            fatal = code is None or str(code).startswith(_RUFF_ERROR_PREFIXES)
            severity = "error" if fatal else "warning"
        out.append({
            "linter": "ruff", "file": item.get("filename"), "line": _int(start.get("row")),
            "column": _int(start.get("column")), "end_line": _int(end.get("row")),
            "end_column": _int(end.get("column")), "code": code, "rule_name": item.get("name"),
            "severity": severity, "message": _short(item.get("message")),
            "fixable": bool(item.get("fix")),
        })
    return out


_MYPY_LINE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<sev>error|warning|note):\s*(?P<msg>.*?)"
    r"(?:\s+\[(?P<code>[A-Za-z0-9_\-]+)\])?\s*$"
)


def parse_mypy(stdout: str) -> list[dict[str, Any]]:
    """mypy's ``--show-column-numbers --no-error-summary`` text -> diagnostics. Lines that are not a
    finding (a ``note:`` continuation is kept, as severity ``info``) are ignored."""
    out: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        match = _MYPY_LINE.match(raw)
        if not match:
            continue
        severity = {"error": "error", "warning": "warning", "note": "info"}[match["sev"]]
        out.append({
            "linter": "mypy", "file": match["file"], "line": int(match["line"]),
            "column": int(match["col"]) if match["col"] else None, "end_line": None, "end_column": None,
            "code": match["code"], "rule_name": None, "severity": severity,
            "message": _short(match["msg"]), "fixable": False,
        })
    return out


_PYFLAKES_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):?)?\s+(?P<msg>.+)$")


def parse_pyflakes(stdout: str) -> list[dict[str, Any]]:
    """pyflakes' ``file:line:col: message`` -> diagnostics (it has no rule codes)."""
    out: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        match = _PYFLAKES_LINE.match(raw)
        if not match:
            continue
        message = match["msg"].strip()
        fatal = message.startswith("undefined name") or "syntax" in message.lower()
        out.append({
            "linter": "pyflakes", "file": match["file"], "line": int(match["line"]),
            "column": int(match["col"]) if match["col"] else None, "end_line": None, "end_column": None,
            "code": None, "rule_name": None, "severity": "error" if fatal else "warning",
            "message": _short(message), "fixable": False,
        })
    return out


def parse_eslint(stdout: str) -> list[dict[str, Any]]:
    """``eslint -f json`` -> diagnostics. Raises ``ValueError`` when stdout is not that JSON."""
    data = json.loads(stdout)
    if not isinstance(data, list):
        raise ValueError("eslint did not print a JSON array")
    out: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("eslint printed a record that is not an object")
        for message in entry.get("messages") or ():
            level = message.get("severity")
            out.append({
                "linter": "eslint", "file": entry.get("filePath"), "line": _int(message.get("line")),
                "column": _int(message.get("column")), "end_line": _int(message.get("endLine")),
                "end_column": _int(message.get("endColumn")), "code": message.get("ruleId"),
                "rule_name": None,
                # eslint: 2 = error, 1 = warning; a parse failure is "fatal" and has no rule.
                "severity": "error" if (level == 2 or message.get("fatal")) else "warning",
                "message": _short(message.get("message")), "fixable": bool(message.get("fix")),
            })
    return out


_RUFF_WOULD_REFORMAT = re.compile(r"^Would reformat:\s*(?P<file>.+?)\s*$")
_RUFF_ARROW = re.compile(r"^\s*-->\s*(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)\s*$")


def parse_ruff_format_check(stdout: str, stderr: str = "") -> list[dict[str, Any]]:
    """``ruff format --check`` (either its older ``Would reformat: path`` lines or the newer
    ``unformatted: ...`` / ``--> path:line:col`` blocks, or its JSON) -> one record per file."""
    text = stdout.strip()
    if text.startswith("["):
        out = []
        for item in parse_ruff_check(text):
            item["linter"] = "ruff format"
            out.append(item)
        return out
    out = []
    pending: str | None = None
    for raw in (stdout + "\n" + stderr).splitlines():
        match = _RUFF_WOULD_REFORMAT.match(raw)
        if match:
            out.append({"file": match["file"], "line": None, "column": None, "status": "needs_formatting",
                        "message": "File would be reformatted"})
            continue
        head = re.match(r"^(unformatted|invalid-syntax):\s*(?P<msg>.*)$", raw)
        if head:
            pending = raw
            continue
        arrow = _RUFF_ARROW.match(raw)
        if arrow and pending is not None:
            kind = "needs_formatting" if pending.startswith("unformatted") else "syntax_error"
            out.append({"file": arrow["file"], "line": int(arrow["line"]), "column": int(arrow["col"]),
                        "status": kind, "message": _short(pending.split(":", 1)[1])})
            pending = None
    return out


def parse_prettier_check(stdout: str, stderr: str = "") -> list[dict[str, Any]]:
    """``prettier --check`` -> one record per file it would change or could not parse.

    Prettier writes ``[warn] path`` for each unformatted file and ``[error] path: SyntaxError: ...``
    for one it could not read; the closing ``[warn] Code style issues found ...`` is a summary.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in (stdout + "\n" + stderr).splitlines():
        line = raw.strip()
        if line.startswith("[warn] "):
            name = line[len("[warn] "):].strip()
            if name.startswith(("Code style issues", "Run Prettier")) or name in seen:
                continue
            seen.add(name)
            out.append({"file": name, "line": None, "column": None, "status": "needs_formatting",
                        "message": "File would be reformatted"})
        elif line.startswith("[error] "):
            body = line[len("[error] "):]
            match = re.match(r"^(?P<file>.+?): (?P<kind>SyntaxError): (?P<msg>.*?)(?: \((?P<l>\d+):(?P<c>\d+)\))?$", body)
            if match:
                out.append({"file": match["file"], "line": int(match["l"]) if match["l"] else None,
                            "column": int(match["c"]) if match["c"] else None, "status": "syntax_error",
                            "message": _short(f"{match['kind']}: {match['msg']}")})
            elif body.startswith("No parser could be inferred for file"):
                name = body.split('"')[1] if '"' in body else body
                out.append({"file": name, "line": None, "column": None, "status": "unsupported",
                            "message": "prettier has no parser for this file type"})
    return out


# --------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------


def _is_missing(result: Any, launcher: Sequence[str], tool_name: str) -> bool:
    """Did the *launcher* fail to start (as opposed to the tool running and reporting something)?"""
    # 126/127: a POSIX shell's "cannot execute"/"not found" (and what the fake runner returns for a
    # missing binary); 9009: cmd.exe's "not recognised", also what the Microsoft Store python stub exits.
    if result.exit_code in (126, 127, 9009):
        return True
    if result.exit_code == 0:
        return False
    stderr = (result.stderr or "").lower()[:4000]
    return any(phrase in stderr for phrase in _MISSING_STDERR) and (
        tool_name in stderr or launcher[0].lower() in stderr
    )


def _timed_out(result: Any) -> bool:
    return result.exit_code == 124 and "timed out" in (result.stderr or "").lower()


async def _run_tool(
    runner: Any, handle: Any, tool_name: str, args: Sequence[str], *, timeout_s: float
) -> tuple[Any | None, str | None]:
    """Run the first launcher of ``tool_name`` that exists. ``(None, None)`` when none does."""
    for launcher in _LAUNCHERS[tool_name]:
        result = await runner.run(handle, [*launcher, *args], timeout_s=timeout_s)
        if _is_missing(result, launcher, tool_name):
            continue
        return result, " ".join(launcher)
    return None, None


def _excerpt(result: Any) -> str:
    text = (result.stderr or "").strip() or (result.stdout or "").strip()
    return _short(text, _EXCERPT_CHARS) if text else f"exit code {result.exit_code} with no output"


async def _lint_one(
    runner: Any, handle: Any, linter: str, paths: Sequence[str], timeout_s: float
) -> _Outcome:
    if linter == "ruff":
        args = ["check", "--output-format", "json", "--no-cache", *paths]
    elif linter == "mypy":
        args = ["--show-column-numbers", "--no-error-summary", "--no-pretty", "--no-color-output",
                "--hide-error-context", *paths]
    elif linter == "pyflakes":
        args = list(paths)
    else:  # eslint
        args = ["-f", "json", *paths]
    result, launcher = await _run_tool(runner, handle, linter, args, timeout_s=timeout_s)
    if result is None:
        return _Outcome("not_installed", [], detail=f"{linter} is not installed in this workspace")
    if _timed_out(result):
        return _Outcome("timeout", [], result.exit_code, launcher, f"{linter} did not finish within {timeout_s:.0f}s")
    if len(result.stdout or "") > _MAX_OUTPUT_CHARS:
        return _Outcome("failed", [], result.exit_code, launcher,
                        f"{linter} printed more than {_MAX_OUTPUT_CHARS:,} characters; narrow `paths`")
    try:
        if linter == "ruff":
            found = parse_ruff_check(result.stdout)
            usable = result.exit_code in (0, 1) or bool(found)
        elif linter == "eslint":
            found = parse_eslint(result.stdout)
            usable = result.exit_code in (0, 1)
        elif linter == "mypy":
            found = parse_mypy(result.stdout)
            usable = result.exit_code in (0, 1, 2) and (bool(found) or result.exit_code == 0)
        else:
            found = parse_pyflakes(result.stdout)
            usable = result.exit_code in (0, 1) and (bool(found) or result.exit_code == 0)
    except ValueError:
        return _Outcome("failed", [], result.exit_code, launcher,
                        f"{linter} exited {result.exit_code} and its output could not be read: {_excerpt(result)}")
    if not usable:
        # A non-zero exit with nothing parseable (bad config, unreadable path, internal error): the
        # run did not happen, so it must not read as a clean one.
        return _Outcome("failed", found, result.exit_code, launcher,
                        f"{linter} exited {result.exit_code}: {_excerpt(result)}")
    return _Outcome("findings" if found else "ok", found, result.exit_code, launcher)


async def _scan_one(runner: Any, handle: Any, scanner: str, paths: Sequence[str], timeout_s: float,
                    history: bool) -> _Outcome:
    """Run one security scanner and read its machine output. Never "ok" unless it really ran."""
    findings: list[dict[str, Any]] = []
    notes: list[str] = []
    launcher_used: str | None = None
    exit_code: int | None = None
    for args in scanner_args(scanner, paths, history=history):
        result, launcher = await _run_tool(runner, handle, scanner, args, timeout_s=timeout_s)
        if result is None:
            return _Outcome("not_installed", [], detail=f"{scanner} is not installed in this workspace's "
                            "sandbox image (the server image bundles it; a paired desktop needs it installed)")
        launcher_used, exit_code = launcher, result.exit_code
        if _timed_out(result):
            return _Outcome("timeout", findings, result.exit_code, launcher, f"{scanner} did not finish within "
                            f"{timeout_s:.0f}s")
        if len(result.stdout or "") > _MAX_OUTPUT_CHARS:
            return _Outcome("failed", [], result.exit_code, launcher,
                            f"{scanner} printed more than {_MAX_OUTPUT_CHARS:,} characters; narrow `paths`")
        try:
            if scanner == "gitleaks":
                found = parse_gitleaks(result.stdout)
                usable = result.exit_code == 0
            elif scanner == "bandit":
                found, errs = parse_bandit(result.stdout)
                notes += errs
                usable = result.exit_code in (0, 1)
            else:
                found, errs = parse_semgrep(result.stdout)
                notes += errs
                usable = result.exit_code in (0, 1)
        except ValueError:
            return _Outcome("failed", [], result.exit_code, launcher,
                            f"{scanner} exited {result.exit_code} and its output could not be read: {_excerpt(result)}")
        if not usable:
            return _Outcome("failed", found, result.exit_code, launcher,
                            f"{scanner} exited {result.exit_code}: {_excerpt(result)}")
        findings += found
    # A scanner that reported it could not read some files did not see everything.
    detail = "; ".join(dict.fromkeys(notes))[:400] if notes else None
    if notes:
        return _Outcome("incomplete", findings, exit_code, launcher_used,
                        f"{scanner} could not analyse part of the input: {detail}")
    return _Outcome("findings" if findings else "ok", findings, exit_code, launcher_used)


def _split_targets(paths: Sequence[str]) -> tuple[bool, bool]:
    """``(python_targets, js_targets)``: which language families the named paths point at."""
    js = any(p.lower().endswith(_JS_SUFFIXES) for p in paths)
    other = any(not p.lower().endswith(_JS_SUFFIXES) for p in paths)
    return other, js


def _validated_paths(tool_name: str, paths: Sequence[Any] | None) -> list[str]:
    values = [str(p) for p in (paths or ["."])]
    if not values or len(values) > 50:
        raise ToolExecutionError("give between 1 and 50 paths", tool=tool_name)
    return [_relative_path(tool_name, "paths entry", value) for value in values]


async def _workspace_root(runner: Any, handle: Any) -> str | None:
    """The workspace directory, so absolute paths in ruff/eslint output can be shown relative.

    Best effort: ``pwd`` is absent on some desktops, in which case the paths stay as the tool wrote
    them. It is never an error."""
    try:
        result = await runner.run(handle, ["pwd"], timeout_s=10.0)
    except Exception:
        return None
    root = (result.stdout or "").strip().splitlines()
    return root[0].strip() if result.exit_code == 0 and root and root[0].strip().startswith("/") else None


def _relativise(items: list[dict[str, Any]], root: str | None) -> None:
    if not root:
        return
    prefix = root.rstrip("/") + "/"
    for item in items:
        name = item.get("file")
        if isinstance(name, str) and name.startswith(prefix):
            item["file"] = name[len(prefix):]


def _sort_key(item: dict[str, Any]) -> tuple:
    return (str(item.get("file") or ""), item.get("line") or 0, item.get("column") or 0)


def build_devtools(sandbox_runner: Any, sessions: Any) -> list[Tool]:
    """``lint_code`` and ``check_format``, bound to the same sandbox ``run_command`` uses.

    ``sessions`` is the ``_SessionAdapter`` ``build_sandbox_tools`` already holds, so a file the agent
    just wrote with ``write_file`` is the file that gets linted, on the desktop or in the server
    sandbox alike.
    """
    common = dict(
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.SYSTEM,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        max_result_chars=24_000,
        # Linter output quotes the project's own code and comments: attacker-influenceable text.
        tags=("untrusted_content",),
        # Same declaration as run_command, and for the same reason: these start a program in the
        # engineer's workspace and nothing else. They cannot do what run_command cannot, they never
        # write (no --fix), and a confirmation per lint pass would only train people to click through.
        workspace_local=True,
    )

    @tool(
        "lint_code",
        "Lint the engineer workspace's code and return STRUCTURED findings (file, line, column, rule "
        "code, severity, message) with counts. linter 'auto' runs ruff on Python (pyflakes if ruff is "
        "missing) and eslint when the paths name JS/TS files; name 'mypy' for type checking or 'eslint' "
        "for a directory. A linter that is not installed, timed out or crashed is reported as such and "
        "never as 'clean'; `clean` is true only when every linter actually ran and found nothing. "
        "Read-only: nothing is auto-fixed.",
        param("paths", "array", "Files or directories inside the workspace (default: the whole workspace).",
              required=False, item_type="string"),
        param("linter", "string", "Which linter to run.", required=False, default="auto",
              enum=("auto", "ruff", "pyflakes", "mypy", "eslint")),
        param("limit", "integer", "Most findings to list (default 200); the rest are counted.",
              required=False, default=_DEFAULT_MAX_DIAGNOSTICS, minimum=1, maximum=_HARD_MAX_DIAGNOSTICS),
        param("timeout_s", "number", "Max seconds per linter (default 120).", required=False, default=120,
              minimum=5, maximum=300),
        timeout_s=605.0,
        **common,
    )
    async def lint_code(
        context: ToolContext,
        paths: list | None = None,
        linter: str = "auto",
        limit: int = _DEFAULT_MAX_DIAGNOSTICS,
        timeout_s: float = 120.0,
    ) -> str:
        name = "lint_code"
        targets = _validated_paths(name, paths)
        python_targets, js_targets = _split_targets(targets)
        if linter == "auto":
            wanted: list[str] = (["ruff"] if python_targets else []) + (["eslint"] if js_targets else [])
        elif linter in ("ruff", "pyflakes", "mypy", "eslint"):
            wanted = [linter]
        else:
            raise ToolExecutionError(f"unsupported linter {linter!r}", tool=name)
        handle = await sessions.handle(context)

        report: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        for entry in wanted:
            outcome = await _lint_one(sandbox_runner, handle, entry, targets, timeout_s)
            if entry == "ruff" and outcome.status == "not_installed" and linter == "auto":
                # The documented fallback for 'auto': a Python workspace without ruff still gets checked.
                fallback = await _lint_one(sandbox_runner, handle, "pyflakes", targets, timeout_s)
                if fallback.status != "not_installed":
                    report.append({"linter": "ruff", "status": "not_installed", "count": 0,
                                   "detail": outcome.detail, "superseded_by": "pyflakes"})
                    entry, outcome = "pyflakes", fallback
            diagnostics += outcome.diagnostics
            report.append({
                "linter": entry, "status": outcome.status, "count": len(outcome.diagnostics),
                "exit_code": outcome.exit_code, "command": outcome.launcher,
                **({"detail": outcome.detail} if outcome.detail else {}),
            })
        if not wanted:
            raise ToolExecutionError("nothing to lint: no linter applies to these paths", tool=name)

        if any(item.get("file") and str(item["file"]).startswith("/") for item in diagnostics):
            _relativise(diagnostics, await _workspace_root(sandbox_runner, handle))
        diagnostics.sort(key=_sort_key)
        counts = {
            "errors": sum(1 for d in diagnostics if d["severity"] == "error"),
            "warnings": sum(1 for d in diagnostics if d["severity"] == "warning"),
            "info": sum(1 for d in diagnostics if d["severity"] == "info"),
            "files": len({d["file"] for d in diagnostics}),
            "total": len(diagnostics),
        }
        shown = diagnostics[: int(limit)]
        # `clean` needs every linter that applies to have run and found nothing. A ruff that was
        # missing but replaced by pyflakes (the 'auto' fallback) does not veto it; a missing linter
        # that nothing replaced does.
        effective = [e for e in report if "superseded_by" not in e]
        clean = bool(effective) and all(e["status"] == "ok" for e in effective)
        return json.dumps({
            "clean": clean,
            "summary": {**counts, "shown": len(shown), "truncated": len(shown) < len(diagnostics),
                        "withheld": len(diagnostics) - len(shown)},
            "linters": report,
            "diagnostics": shown,
        }, ensure_ascii=False)

    @tool(
        "check_format",
        "Check (never rewrite) whether the workspace's files are formatted: `ruff format --check` for "
        "Python and `prettier --check` for JS/TS/JSON/CSS/Markdown. Returns STRUCTURED results: which "
        "files would change, which could not be parsed, and per-formatter status. A formatter that is "
        "not installed is reported as such, never as 'formatted'. To apply formatting use run_command "
        "with the formatter's own write mode.",
        param("paths", "array", "Files or directories inside the workspace (default: the whole workspace).",
              required=False, item_type="string"),
        param("formatter", "string", "Which formatter to run (auto: ruff for Python, prettier when the "
              "paths name JS/TS files).", required=False, default="auto", enum=("auto", "ruff", "prettier")),
        param("limit", "integer", "Most files to list (default 200); the rest are counted.",
              required=False, default=_DEFAULT_MAX_DIAGNOSTICS, minimum=1, maximum=_HARD_MAX_DIAGNOSTICS),
        param("timeout_s", "number", "Max seconds per formatter (default 120).", required=False, default=120,
              minimum=5, maximum=300),
        timeout_s=605.0,
        **common,
    )
    async def check_format(
        context: ToolContext,
        paths: list | None = None,
        formatter: str = "auto",
        limit: int = _DEFAULT_MAX_DIAGNOSTICS,
        timeout_s: float = 120.0,
    ) -> str:
        name = "check_format"
        targets = _validated_paths(name, paths)
        python_targets, js_targets = _split_targets(targets)
        if formatter == "auto":
            wanted = (["ruff"] if python_targets else []) + (["prettier"] if js_targets else [])
        elif formatter in ("ruff", "prettier"):
            wanted = [formatter]
        else:
            raise ToolExecutionError(f"unsupported formatter {formatter!r}", tool=name)
        if not wanted:
            raise ToolExecutionError("nothing to check: no formatter applies to these paths", tool=name)
        handle = await sessions.handle(context)

        report: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        for entry in wanted:
            if entry == "ruff":
                result, launcher = await _run_tool(
                    sandbox_runner, handle, "ruff",
                    ["format", "--check", "--output-format", "json", "--no-cache", *targets], timeout_s=timeout_s)
                if result is not None and result.exit_code == 2 and any(
                    phrase in (result.stderr or "").lower() for phrase in ("unexpected argument", "unrecognized")
                ):
                    # An older ruff without `format --output-format`: its plain output is parsed instead.
                    result, launcher = await _run_tool(
                        sandbox_runner, handle, "ruff", ["format", "--check", "--no-cache", *targets],
                        timeout_s=timeout_s)
            else:
                result, launcher = await _run_tool(
                    sandbox_runner, handle, "prettier", ["--check", *targets], timeout_s=timeout_s)

            if result is None:
                report.append({"formatter": entry, "status": "not_installed", "count": 0,
                               "detail": f"{entry} is not installed in this workspace"})
                continue
            if _timed_out(result):
                report.append({"formatter": entry, "status": "timeout", "count": 0, "command": launcher,
                               "detail": f"{entry} did not finish within {timeout_s:.0f}s"})
                continue
            try:
                if entry == "ruff":
                    parsed = parse_ruff_format_check(result.stdout, result.stderr)
                    for item in parsed:
                        if "linter" in item:  # the JSON form carries lint-shaped records
                            item["status"] = "needs_formatting" if item.get("code") == "unformatted" else "syntax_error"
                            for drop in ("linter", "end_line", "end_column", "rule_name", "severity", "fixable", "code"):
                                item.pop(drop, None)
                else:
                    parsed = parse_prettier_check(result.stdout, result.stderr)
            except ValueError:
                report.append({"formatter": entry, "status": "failed", "count": 0, "command": launcher,
                               "exit_code": result.exit_code,
                               "detail": f"{entry} output could not be read: {_excerpt(result)}"})
                continue
            if result.exit_code == 0 and not parsed:
                status = "ok"
            elif parsed and result.exit_code in (1, 2):
                status = "findings"
            else:
                status = "failed"
            entry_report = {"formatter": entry, "status": status, "count": len(parsed),
                            "exit_code": result.exit_code, "command": launcher}
            if status == "failed":
                entry_report["detail"] = f"{entry} exited {result.exit_code}: {_excerpt(result)}"
            report.append(entry_report)
            for item in parsed:
                item["formatter"] = entry
            files += parsed

        if any(str(item.get("file") or "").startswith("/") for item in files):
            _relativise(files, await _workspace_root(sandbox_runner, handle))
        files.sort(key=_sort_key)
        shown = files[: int(limit)]
        return json.dumps({
            "formatted": bool(report) and all(e["status"] == "ok" for e in report),
            "summary": {
                "needs_formatting": sum(1 for f in files if f["status"] == "needs_formatting"),
                "syntax_errors": sum(1 for f in files if f["status"] == "syntax_error"),
                "other": sum(1 for f in files if f["status"] not in ("needs_formatting", "syntax_error")),
                "total": len(files), "shown": len(shown), "truncated": len(shown) < len(files),
                "withheld": len(files) - len(shown),
            },
            "formatters": report,
            "files": shown,
        }, ensure_ascii=False)

    @tool(
        "scan_security",
        "Run Semgrep (bundled rules), Gitleaks (secrets, optionally git history) and Bandit (Python) in the "
        "sandbox; STRUCTURED findings, never secret values. A scanner that is missing, timed out, crashed or "
        "could not read some files is reported as such and never as clean.",
        param("paths", "array", "Workspace files or folders (default: all).", required=False, item_type="string"),
        param("scanners", "array", "Subset of semgrep, gitleaks, bandit (default: all).", required=False,
              item_type="string"),
        param("history", "boolean", "Gitleaks: scan git history too.", required=False, default=False),
        param("limit", "integer", "Most findings listed (default 200).", required=False,
              default=_DEFAULT_MAX_DIAGNOSTICS, minimum=1, maximum=_HARD_MAX_DIAGNOSTICS),
        param("timeout_s", "number", "Max seconds per scanner (default 300).", required=False, default=300,
              minimum=10, maximum=900),
        timeout_s=2_800.0,
        **common,
    )
    async def scan_security(
        context: ToolContext,
        paths: list | None = None,
        scanners: list | None = None,
        history: bool = False,
        limit: int = _DEFAULT_MAX_DIAGNOSTICS,
        timeout_s: float = 300.0,
    ) -> str:
        name = "scan_security"
        targets = _validated_paths(name, paths)
        wanted = [str(x).lower() for x in (scanners or SCANNERS)]
        unknown = [x for x in wanted if x not in SCANNERS]
        if unknown or not wanted:
            raise ToolExecutionError(f"scanners must be a subset of {', '.join(SCANNERS)}", tool=name)
        wanted = list(dict.fromkeys(wanted))
        if len(targets) > 5 and "gitleaks" in wanted:
            raise ToolExecutionError("gitleaks takes at most 5 paths; scan the folder that holds them", tool=name)
        handle = await sessions.handle(context)

        report: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        for scanner in wanted:
            outcome = await _scan_one(sandbox_runner, handle, scanner, targets, timeout_s, history)
            findings += outcome.diagnostics
            report.append({
                "scanner": scanner, "status": outcome.status, "count": len(outcome.diagnostics),
                "exit_code": outcome.exit_code, "command": outcome.launcher,
                **({"detail": outcome.detail} if outcome.detail else {}),
            })
        if any(item.get("file") and str(item["file"]).startswith("/") for item in findings):
            _relativise(findings, await _workspace_root(sandbox_runner, handle))
        findings.sort(key=_sort_key)
        order = {"error": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda f: order.get(f.get("severity"), 3))
        counts = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in ("error", "warning", "info")}
        shown = findings[: int(limit)]
        # Same rule as lint_code: clean only when every requested scanner ran fully and found nothing.
        clean = all(e["status"] == "ok" for e in report)
        return json.dumps({
            "clean": clean,
            "summary": {**counts, "total": len(findings), "files": len({f["file"] for f in findings}),
                        "shown": len(shown), "truncated": len(shown) < len(findings),
                        "withheld": len(findings) - len(shown)},
            "scanners": report,
            "findings": shown,
            "note": "Semgrep rules are the sandbox image's bundled set, pinned at build time; findings are "
                    "leads to review, and a clean result is not proof the code is secure.",
        }, ensure_ascii=False)

    return [lint_code, check_format, scan_security]
