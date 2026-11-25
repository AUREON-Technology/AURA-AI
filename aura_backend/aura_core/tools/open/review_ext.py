"""Deeper static review for ``review_code_diff`` (ADR-046, row 5).

``PeerCodeReviewerAgent`` applies fixed line rules. This adds three things it lacked, all pure
text-in/JSON-out and all reported with line numbers:

* **bug patterns** found on the AST (mutable default arguments, ``== None``, ``is`` against a
  literal, ``except: pass``, unreachable statements, duplicate dict keys, a tuple ``assert``, a
  comparison whose result is thrown away, ``open()`` without ``with``, shadowed builtins,
  ``return`` inside ``finally``, ``await``-less coroutine calls to ``asyncio.sleep``);
* **complexity** hits (cyclomatic complexity / length over the limit);
* **architecture rule hits**: the file's imports checked against layer rules
  (``ArchitecturalConsistencyChecker``) when the module's dotted name is known.

For a *diff* the new side of every hunk is rebuilt. A file that the diff adds whole is parsed and
gets every check; for a partial hunk only line-oriented patterns run on the added lines (a hunk
alone is not parseable Python), and the result says which mode each file got. Nothing is executed
and nothing is imported from the reviewed text.

This is still a rule engine, not an independent reviewer: it finds what its rules name. The result
says so (``method``) rather than implying a model looked at the change.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from .architectural_consistency_checker import ArchitecturalConsistencyChecker
from .complexity_and_duplication_analyzer import ComplexityAndDuplicationAnalyzer

__all__ = ["deep_review", "module_name_from_path"]

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_SHADOW_WATCH = frozenset({"id", "type", "input", "filter", "format", "help", "hash", "object", "dir", "all", "any",
                            "next", "iter", "max", "min", "sum", "map", "set", "list", "dict", "str", "int",
                            "float", "bytes", "bool", "len", "vars", "compile", "range"})
_MAX_FINDINGS = 200


def module_name_from_path(path: str) -> str:
    """``aura_core/domain/x.py`` -> ``aura_core.domain.x`` ('' when it is not a relative .py path)."""
    text = str(path or "").replace("\\", "/").strip("/")
    if not text.endswith(".py") or text.startswith(("..", "<")):
        return ""
    parts = [p for p in text[:-3].split("/") if p and p != "."]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _finding(code: str, severity: str, filename: str, line: int | None, message: str, **extra: Any) -> dict[str, Any]:
    out = {"code": code, "severity": severity, "category": extra.pop("category", "bug"), "file": filename,
           "line": line, "message": message}
    out.update(extra)
    return out


def _terminates(stmt: ast.stmt) -> bool:
    return isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break))


def _ast_bugs(tree: ast.AST, filename: str, line_offset: int = 0) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def at(node: ast.AST) -> int:
        return getattr(node, "lineno", 0) + line_offset

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for default in [*args.defaults, *[d for d in args.kw_defaults if d is not None]]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)) or (
                    isinstance(default, ast.Call) and isinstance(default.func, ast.Name)
                    and default.func.id in {"list", "dict", "set"}
                ):
                    found.append(_finding(
                        "BUG-MUTABLE-DEFAULT", "MAJOR", filename, at(default),
                        "Mutable default argument is created once and shared by every call; use None and build it inside."))
            if not isinstance(node, ast.Lambda):
                for argument in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                    if argument.arg in _SHADOW_WATCH:
                        found.append(_finding(
                            "BUG-SHADOWED-BUILTIN", "MINOR", filename, at(argument),
                            f"Parameter '{argument.arg}' shadows the builtin of the same name."))
        elif isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators):
                none_side = isinstance(right, ast.Constant) and right.value is None
                if isinstance(op, (ast.Eq, ast.NotEq)) and none_side:
                    found.append(_finding(
                        "BUG-EQ-NONE", "MINOR", filename, at(node),
                        "Compare with None using 'is' / 'is not', not '=='."))
                if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(right, ast.Constant) and not (
                    right.value is None or isinstance(right.value, bool) or right.value is Ellipsis
                ):
                    found.append(_finding(
                        "BUG-IS-LITERAL", "MAJOR", filename, at(node),
                        "'is' against a literal tests identity, not equality; use '=='."))
        elif isinstance(node, ast.ExceptHandler):
            only_pass = all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                            for s in node.body)
            if only_pass:
                found.append(_finding(
                    "BUG-SWALLOWED-EXCEPTION", "MAJOR", filename, at(node),
                    "The exception is caught and silently discarded; log it or let it propagate."))
        elif isinstance(node, ast.Assert) and isinstance(node.test, ast.Tuple) and node.test.elts:
            found.append(_finding(
                "BUG-ASSERT-TUPLE", "MAJOR", filename, at(node),
                "assert on a non-empty tuple is always true; remove the parentheses around (test, message)."))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Compare):
            found.append(_finding(
                "BUG-DISCARDED-COMPARISON", "MAJOR", filename, at(node),
                "The result of this comparison is discarded; it was probably meant to be an assert or an assignment."))
        elif isinstance(node, ast.Dict):
            seen: set[Any] = set()
            for key in node.keys:
                if isinstance(key, ast.Constant):
                    marker = (type(key.value).__name__, key.value)
                    if marker in seen:
                        found.append(_finding(
                            "BUG-DUPLICATE-DICT-KEY", "MAJOR", filename, at(key),
                            f"Dictionary literal repeats the key {key.value!r}; the earlier value is lost."))
                    seen.add(marker)
        elif isinstance(node, ast.Try):
            for stmt in node.finalbody:
                if isinstance(stmt, ast.Return):
                    found.append(_finding(
                        "BUG-RETURN-IN-FINALLY", "MAJOR", filename, at(stmt),
                        "'return' inside 'finally' discards any in-flight exception."))
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "open":
                found.append(_finding(
                    "BUG-OPEN-WITHOUT-WITH", "MINOR", filename, at(node),
                    "open() result is bound without a 'with' block; the file is closed only by garbage collection."))
    # statements after return/raise/continue/break in the same block
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list):
                for index, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, ast.stmt) and _terminates(stmt):
                        nxt = block[index + 1]
                        found.append(_finding(
                            "BUG-UNREACHABLE", "MAJOR", filename, at(nxt),
                            "Statement is unreachable: it follows a return/raise/continue/break in the same block."))
                        break
    found.extend(_unawaited(tree, filename, line_offset))
    return found


def _unawaited(tree: ast.AST, filename: str, line_offset: int) -> list[dict[str, Any]]:
    """``asyncio.sleep(...)`` used as a bare statement inside an ``async def`` (never awaited)."""
    out: list[dict[str, Any]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Attribute) and func.attr == "sleep" and isinstance(func.value, ast.Name) \
                        and func.value.id == "asyncio":
                    out.append(_finding(
                        "BUG-UNAWAITED-COROUTINE", "MAJOR", filename, node.lineno + line_offset,
                        "asyncio.sleep(...) is never awaited, so it does nothing."))
    return out


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return found


def _architecture(
    tree: ast.AST, filename: str, module_name: str, rules: dict[str, list[str]] | None
) -> tuple[list[dict[str, Any]], bool]:
    if not module_name:
        return [], False
    findings: list[dict[str, Any]] = []
    layer_known = False
    for imported, line in _imports(tree):
        check = ArchitecturalConsistencyChecker.check_module_imports(module_name, [imported], forbidden_rules=rules)
        layer_known = layer_known or bool(check.get("layer_known"))
        for violation in check.get("violations", []):
            findings.append(_finding("ARCH-LAYER-VIOLATION", "MAJOR", filename, line, violation, category="architecture"))
    return findings, layer_known


def _complexity(source: str, filename: str, max_complexity: int, max_lines: int) -> list[dict[str, Any]]:
    report = ComplexityAndDuplicationAnalyzer.analyze_functions(source, max_complexity=max_complexity, max_lines=max_lines)
    return [
        _finding(
            "CPLX-TOO-COMPLEX", "MAJOR" if fn["cyclomatic_complexity"] > 2 * max_complexity else "MINOR",
            filename, fn["start_line"],
            f"Function '{fn['name']}' has cyclomatic complexity {fn['cyclomatic_complexity']} "
            f"over {fn['line_count']} lines (limits {max_complexity} / {max_lines}).",
            category="complexity", symbol=fn["name"], complexity=fn["cyclomatic_complexity"],
        )
        for fn in report.get("functions", []) if fn.get("flagged")
    ]


def _review_source(
    source: str, filename: str, module_name: str, rules: dict[str, list[str]] | None,
    max_complexity: int, max_lines: int, line_offset: int = 0,
) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"parsed": False, "error": f"SyntaxError: {exc}", "findings": []}
    except (ValueError, RecursionError, MemoryError) as exc:
        return {"parsed": False, "error": f"{type(exc).__name__}: {exc}", "findings": []}
    arch, layer_known = _architecture(tree, filename, module_name, rules)
    findings = _ast_bugs(tree, filename, line_offset) + arch + _complexity(source, filename, max_complexity, max_lines)
    return {"parsed": True, "findings": findings, "layer_known": layer_known}


# Line patterns for hunks that are not parseable alone. Each is conservative on purpose.
_LINE_RULES: tuple[tuple[str, str, str, re.Pattern[str], str], ...] = (
    ("BUG-MUTABLE-DEFAULT", "MAJOR", "bug", re.compile(r"\bdef\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\))"),
     "Mutable default argument is created once and shared by every call; use None and build it inside."),
    ("BUG-EQ-NONE", "MINOR", "bug", re.compile(r"[=!]=\s*None\b"),
     "Compare with None using 'is' / 'is not', not '=='."),
    ("BUG-IS-LITERAL", "MAJOR", "bug", re.compile(r"\bis\s+not?\s+(?:\d+|['\"])|\bis\s+(?:\d+|['\"])"),
     "'is' against a literal tests identity, not equality; use '=='."),
    ("BUG-ASSERT-TUPLE", "MAJOR", "bug", re.compile(r"^\s*assert\s*\(.*,.*\)\s*$"),
     "assert on a parenthesised (test, message) tuple is always true."),
    ("BUG-SWALLOWED-EXCEPTION", "MAJOR", "bug", re.compile(r"\bexcept\b[^:]*:\s*pass\b"),
     "The exception is caught and silently discarded; log it or let it propagate."),
)
_IMPORT_LINE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\b|import\s+([\w.]+))")


def _review_diff(
    diff_text: str, rules: dict[str, list[str]] | None, max_complexity: int, max_lines: int
) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    new_line = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            in_hunk = False
            current = None
            continue
        match = _HUNK.match(raw)
        if match:
            in_hunk = True
            new_line = int(match.group(1))
            if current is not None:
                current["hunks"] += 1
                current.setdefault("first_new_line", new_line)
            continue
        if not in_hunk and raw.startswith("+++ "):
            target = raw[4:].split("\t")[0].strip()
            name = target[2:] if target.startswith("b/") else target
            current = files.setdefault(name, {"added": [], "lines": {}, "hunks": 0, "context": {}})
            continue
        if not in_hunk or current is None:
            continue
        if raw.startswith("+"):
            current["added"].append((new_line, raw[1:]))
            current["lines"][new_line] = raw[1:]
            new_line += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        else:
            current["context"][new_line] = raw[1:]
            current["lines"][new_line] = raw[1:]
            new_line += 1

    findings: list[dict[str, Any]] = []
    per_file: list[dict[str, Any]] = []
    for name, info in files.items():
        if not name.endswith(".py") or name == "/dev/null":
            per_file.append({"file": name, "mode": "skipped (not a Python file)"})
            continue
        module = module_name_from_path(name)
        lines = info["lines"]
        whole = bool(lines) and min(lines) == 1 and sorted(lines) == list(range(1, max(lines) + 1)) \
            and not info["context"]
        if whole:
            result = _review_source("\n".join(lines[i] for i in sorted(lines)), name, module, rules,
                                    max_complexity, max_lines)
            if result["parsed"]:
                findings += result["findings"]
                per_file.append({"file": name, "mode": "whole file parsed", "layer_known": result["layer_known"]})
                continue
            per_file.append({"file": name, "mode": "line patterns (added file did not parse)", "error": result["error"]})
        else:
            per_file.append({"file": name, "mode": "line patterns on added lines (a partial hunk is not parseable)"})
        layer_known = False
        for number, text in info["added"]:
            stripped = text.strip()
            if stripped.startswith("#"):
                continue
            for code, severity, category, pattern, message in _LINE_RULES:
                if pattern.search(text):
                    findings.append(_finding(code, severity, name, number, message, category=category,
                                             snippet=stripped[:300]))
            imp = _IMPORT_LINE.match(text)
            if imp and module:
                imported = imp.group(1) or imp.group(2)
                check = ArchitecturalConsistencyChecker.check_module_imports(module, [imported], forbidden_rules=rules)
                layer_known = layer_known or bool(check.get("layer_known"))
                for violation in check.get("violations", []):
                    findings.append(_finding("ARCH-LAYER-VIOLATION", "MAJOR", name, number, violation,
                                             category="architecture", snippet=stripped[:300]))
        per_file[-1]["layer_known"] = layer_known
    return {"findings": findings, "files": per_file}


def deep_review(
    *,
    source_code: str | None = None,
    diff_text: str | None = None,
    filename: str = "<string>",
    module_name: str | None = None,
    layer_rules: dict[str, list[str]] | None = None,
    max_complexity: int = 10,
    max_lines: int = 50,
) -> dict[str, Any]:
    """Bug-pattern, complexity and architecture findings (each with a line number) for source and/or a diff."""
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    modes: list[dict[str, Any]] = []
    if source_code is not None:
        module = module_name if module_name is not None else module_name_from_path(filename)
        result = _review_source(source_code, filename, module, layer_rules, max_complexity, max_lines)
        if result["parsed"]:
            findings += result["findings"]
            modes.append({"file": filename, "mode": "whole file parsed", "module": module or None,
                          "layer_known": result["layer_known"]})
        else:
            errors.append(f"source_code: {result['error']}")
    if diff_text is not None:
        result = _review_diff(diff_text, layer_rules, max_complexity, max_lines)
        findings += result["findings"]
        modes += result["files"]
    findings.sort(key=lambda f: (f["file"], f["line"] or 0, f["code"]))
    by_category: dict[str, int] = {}
    for f in findings:
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1
    truncated = len(findings) > _MAX_FINDINGS
    return {
        "method": "AST and line-pattern rules; not an independent or model-based review",
        "findings": findings[:_MAX_FINDINGS],
        "findings_count": len(findings),
        "by_category": by_category,
        "files": modes,
        "errors": errors,
        "truncated": truncated,
        "blocking": sum(1 for f in findings if f["severity"] in ("MAJOR", "CRITICAL")),
    }
