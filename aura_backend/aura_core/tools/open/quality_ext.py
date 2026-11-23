"""Changed-line coverage and operator mutations for ``analyze_test_quality`` (ADR-044, row 21).

``TestCoverageAndMutationAnalyzer`` measures the whole module and flips only ``True``/``False``. This
adds, for the same throwaway directory the tool already runs in:

* **Coverage restricted to the changed lines** of a unified diff: the lines the diff adds, intersected
  with coverage.py's executed and missing line sets for the module, so "82% overall" no longer hides
  that none of the new lines ran. Lines that are not statements (comments, blank, ``else:``) are
  reported separately rather than counted as covered.
* **Operator mutants**: arithmetic (``+ - * / // %``), comparison (``< <= > >= == != is is-not``),
  boolean (``and``/``or``) and constant (``True``/``False``) replacements, each located by its column
  in the source, optionally only on the changed lines. A mutant counts as killed on a pytest failure,
  a collection error (exit 2) or a hang, exactly as the boolean-only version does.

Equivalent mutants (``x >= 0`` -> ``x > 0`` where x is never 0) survive by construction, so a
surviving mutant is a question about the tests, not proof of a gap. The result says how many mutants
existed, how many ran and which survived, with line numbers.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from ._ops_common import run_group
from ._process import PYTEST_ENV, scrubbed_env

__all__ = ["changed_lines", "operator_mutants", "changed_line_coverage", "evaluate_operator_mutants", "KINDS"]

KINDS = ("boolean", "comparison", "arithmetic", "logical")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

_ARITH = {ast.Add: ("+", "-"), ast.Sub: ("-", "+"), ast.Mult: ("*", "/"), ast.Div: ("/", "*"),
          ast.FloorDiv: ("//", "*"), ast.Mod: ("%", "*")}
_COMPARE = {ast.Lt: ("<", "<="), ast.LtE: ("<=", "<"), ast.Gt: (">", ">="), ast.GtE: (">=", ">"),
            ast.Eq: ("==", "!="), ast.NotEq: ("!=", "==")}


def changed_lines(diff_text: str, filename: str | None = None) -> dict[str, set[int]]:
    """``{file: {added new-side line numbers}}`` from a unified diff (``filename`` keeps one file)."""
    result: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            in_hunk, current = False, None
            continue
        match = _HUNK.match(raw)
        if match:
            in_hunk, new_line = True, int(match.group(1))
            continue
        if not in_hunk and raw.startswith("+++ "):
            target = raw[4:].split("\t")[0].strip()
            current = target[2:] if target.startswith("b/") else target
            result.setdefault(current, set())
            continue
        if not in_hunk or current is None:
            continue
        if raw.startswith("+"):
            result[current].add(new_line)
            new_line += 1
        elif raw.startswith(("-", "\\")):
            continue
        else:
            new_line += 1
    if filename:
        base = Path(filename).name
        picked = {f: s for f, s in result.items() if f == filename or Path(f).name == base}
        return picked or {}
    return result


def _gap_find(line_bytes: bytes, start: int, end: int, token: str) -> int | None:
    """Offset of ``token`` between two operand columns on one line (the operator's own position)."""
    gap = line_bytes[start:end]
    match = re.search(rb"(?<![\w<>=!/*+\-%])" + re.escape(token.encode()) + rb"(?![=<>*/])", gap)
    return start + match.start() if match else None


def operator_mutants(source: str, kinds: tuple[str, ...] = KINDS) -> list[dict[str, Any]]:
    """Every mutation point in ``source``: ``{line, col, end_col, original, mutated, kind}``."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    lines = [ln.encode("utf-8") for ln in re.split(r"\r\n|\r|\n", source)]
    found: list[dict[str, Any]] = []

    def add(kind: str, line: int, col: int, original: str, mutated: str) -> None:
        found.append({"line": line, "col": col, "end_col": col + len(original.encode()), "original": original,
                      "mutated": mutated, "kind": kind})

    for node in ast.walk(tree):
        if "boolean" in kinds and isinstance(node, ast.Constant) and isinstance(node.value, bool):
            add("boolean", node.lineno, node.col_offset, str(node.value), str(not node.value))
        elif "arithmetic" in kinds and isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
            left, right = node.left, node.right
            if left.end_lineno == right.lineno and left.lineno <= len(lines):
                orig, new = _ARITH[type(node.op)]
                pos = _gap_find(lines[left.lineno - 1], left.end_col_offset, right.col_offset, orig)
                if pos is not None:
                    add("arithmetic", left.lineno, pos, orig, new)
        elif "comparison" in kinds and isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for index, op in enumerate(node.ops):
                left, right = operands[index], operands[index + 1]
                if left.end_lineno != right.lineno or left.end_lineno > len(lines):
                    continue
                if type(op) in _COMPARE:
                    orig, new = _COMPARE[type(op)]
                elif isinstance(op, ast.Is):
                    orig, new = "is", "is not"
                elif isinstance(op, ast.IsNot):
                    orig, new = "is not", "is"
                else:
                    continue
                gap = lines[left.end_lineno - 1][left.end_col_offset:right.col_offset]
                pat = re.escape(orig.encode()).replace(rb"\ ", rb"\s+")
                hit = re.search(rb"(?<![\w<>=!])" + pat + rb"(?![\w=<>])", gap)
                if hit:
                    add("comparison", left.end_lineno, left.end_col_offset + hit.start(), orig, new)
        elif "logical" in kinds and isinstance(node, ast.BoolOp):
            orig, new = ("and", "or") if isinstance(node.op, ast.And) else ("or", "and")
            for index in range(len(node.values) - 1):
                left, right = node.values[index], node.values[index + 1]
                if left.end_lineno != right.lineno or left.end_lineno > len(lines):
                    continue
                gap = lines[left.end_lineno - 1][left.end_col_offset:right.col_offset]
                hit = re.search(rb"\b" + orig.encode() + rb"\b", gap)
                if hit:
                    add("logical", left.end_lineno, left.end_col_offset + hit.start(), orig, new)
    # a nested comparison/arithmetic point can be seen once per enclosing walk; de-duplicate by position
    unique = {(m["line"], m["col"], m["kind"]): m for m in found}
    return sorted(unique.values(), key=lambda m: (m["line"], m["col"]))


def changed_line_coverage(
    root: Path, module_name: str, changed: set[int], timeout_s: int
) -> dict[str, Any]:
    """Run pytest under coverage in ``root`` and report coverage of ``changed`` lines of the module."""
    with tempfile.TemporaryDirectory(prefix="aura-cov-") as tmp:
        data = str(Path(tmp) / ".coverage")
        out = Path(tmp) / "coverage.json"
        cmd = [sys.executable, "-m", "coverage", "run", f"--data-file={data}", f"--source={module_name}",
               "-m", "pytest", "test_generated.py", "-q", "-p", "no:cacheprovider"]
        try:
            proc = run_group(cmd, cwd=str(root), timeout_s=float(timeout_s), env=scrubbed_env(PYTEST_ENV))
            if proc.timed_out:
                return {"available": True, "error": f"coverage run timed out after {timeout_s}s"}
            run_group([sys.executable, "-m", "coverage", "json", f"--data-file={data}", "-o", str(out)],
                      cwd=str(root), timeout_s=float(timeout_s), env=scrubbed_env())
            report = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"available": True, "error": f"no coverage report: {type(exc).__name__}"}
    files = report.get("files") or {}
    info = next((v for k, v in files.items() if Path(k).name == f"{module_name}.py"), None)
    if info is None:
        return {"available": True, "error": "the module was never imported by the tests, so nothing is covered",
                "tests_passed": proc.returncode == 0, "changed_lines": sorted(changed), "covered": [],
                "uncovered": [], "percent_covered": 0.0}
    executed, missing = set(info.get("executed_lines") or []), set(info.get("missing_lines") or [])
    statements = executed | missing
    covered = sorted(changed & executed)
    uncovered = sorted(changed & missing)
    non_exec = sorted(changed - statements)
    scored = len(covered) + len(uncovered)
    return {"available": True, "tests_passed": proc.returncode == 0, "changed_lines": sorted(changed),
            "covered": covered, "uncovered": uncovered, "non_executable": non_exec,
            "percent_covered": round(100.0 * len(covered) / scored, 2) if scored else None,
            "whole_module_percent": round(float(info.get("summary", {}).get("percent_covered", 0.0)), 2)}


def evaluate_operator_mutants(
    root: Path, source_filename: str, kinds: tuple[str, ...], only_lines: set[int] | None,
    timeout_s: int, max_mutants: int = 10,
) -> dict[str, Any]:
    """Mutate ``root/source_filename`` in place (``root`` is the caller's throwaway directory) and run pytest."""
    target = root / source_filename
    original = target.read_text(encoding="utf-8")
    candidates = operator_mutants(original, kinds)
    if only_lines is not None:
        candidates = [m for m in candidates if m["line"] in only_lines]
    if not candidates:
        return {"evaluated": True, "mutants_total": 0, "mutants_killed": 0, "mutation_score": None,
                "kinds": list(kinds), "restricted_to_changed_lines": only_lines is not None,
                "note": "No mutation points of the requested kinds" + (" on the changed lines." if only_lines is not None else ".")}
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    env = scrubbed_env(PYTEST_ENV)
    baseline = run_group(cmd, cwd=str(root), timeout_s=float(timeout_s), env=env)
    if baseline.timed_out or baseline.returncode != 0:
        return {"evaluated": False, "error": "The test suite does not pass on unmodified code (or timed out); "
                                              "mutation testing requires a green suite.",
                "stdout": baseline.stdout.strip()[-1500:]}
    # spread the sample over the kinds so ten arithmetic mutants do not crowd out every comparison
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for m in candidates:
        by_kind.setdefault(m["kind"], []).append(m)
    ordered: list[dict[str, Any]] = []
    while any(by_kind.values()):
        for kind in list(by_kind):
            if by_kind[kind]:
                ordered.append(by_kind[kind].pop(0))
    limit = max(1, min(int(max_mutants), 20))
    lines = re.split(r"\r\n|\r|\n", original)
    killed = errored = skipped = tested = 0
    survivors: list[dict[str, Any]] = []
    for m in ordered:
        if tested >= limit:
            break
        old = lines[m["line"] - 1].encode("utf-8")
        if old[m["col"]:m["end_col"]] != m["original"].encode():
            skipped += 1
            continue
        mutated = (old[:m["col"]] + m["mutated"].encode() + old[m["end_col"]:]).decode("utf-8")
        try:
            ast.parse("\n".join([*lines[:m["line"] - 1], mutated, *lines[m["line"]:]]))
        except SyntaxError:
            skipped += 1
            continue
        tested += 1
        target.write_text("\n".join([*lines[:m["line"] - 1], mutated, *lines[m["line"]:]]) + "\n", encoding="utf-8")
        try:
            proc = run_group(cmd, cwd=str(root), timeout_s=float(timeout_s), env=env)
            if proc.timed_out or proc.returncode in (1, 2):
                killed += 1
            elif proc.returncode not in (0, 5):
                errored += 1
            else:
                survivors.append({k: m[k] for k in ("line", "kind", "original", "mutated")})
        except OSError:
            errored += 1
        finally:
            target.write_text(original, encoding="utf-8")
    not_tested = max(0, len(ordered) - tested - skipped)
    return {"evaluated": True, "mutants_total": tested, "mutants_killed": killed,
            "mutants_survived": tested - killed - errored, "mutants_errored": errored,
            "mutants_skipped": skipped, "mutants_candidates": len(ordered), "mutants_not_tested": not_tested,
            "sampled": not_tested > 0, "mutation_score": round(100.0 * killed / tested, 2) if tested else None,
            "survivors": survivors, "kinds": list(kinds), "restricted_to_changed_lines": only_lines is not None,
            "by_kind_candidates": {k: sum(1 for m in ordered if m["kind"] == k) for k in KINDS}}
