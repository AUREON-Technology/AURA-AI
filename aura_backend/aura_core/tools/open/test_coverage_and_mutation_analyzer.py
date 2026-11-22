import ast
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._input import SourceReadError, read_source_bytes, read_source_file
from ._ops_common import PathRefused, confine, run_group
from ._process import PYTEST_ENV, scrubbed_env

#: Not worth copying into the mutation workspace, and a venv or ``.git`` can be gigabytes.
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache", "*.pyc"
)
#: The mutation workspace is a *copy* of working_dir. Pointing it at ``/`` or a home directory must
#: fail fast, not copy the disk.
_MAX_COPY_BYTES = 200 * 1024 * 1024
_MAX_COPY_FILES = 20_000
_SOURCE_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.,/\\:\-]*$")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _copy_ignore(directory: str, names: List[str]) -> set:
    """``_COPY_IGNORE`` plus every symlink: a link must not be followed (or recreated) in the copy."""
    skip = set(_COPY_IGNORE(directory, names))
    skip.update(n for n in names if os.path.islink(os.path.join(directory, n)))
    return skip


def _copy_budget_error(root: Path) -> Optional[str]:
    """Why ``root`` is too big to copy for mutation testing, or None."""
    files = 0
    size = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        skip = _copy_ignore(dirpath, dirnames + filenames)
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            if name in skip:
                continue
            files += 1
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
            if files > _MAX_COPY_FILES or size > _MAX_COPY_BYTES:
                return (
                    f"working_dir is too large to copy for mutation testing (more than {_MAX_COPY_FILES} "
                    f"files or {_MAX_COPY_BYTES // (1024 * 1024)} MB). Point it at the project's source folder."
                )
    return None


class TestCoverageAndMutationAnalyzer:
    """Analyzer for line/branch test coverage percentages, coverage reports, and basic mutation checks.

    ``parse_coverage_json`` and ``evaluate_mutations_against_tests`` read a file through the shared
    reader (``_input``): a **PDF** is read in full (every page) and then refused with a message saying
    it is not a coverage report / Python source; a binary file or an oversized one is refused, never
    partly read. Every child process runs in its own process group with a scrubbed environment, and a
    timeout kills the whole group. Both runners execute the target project's tests -- they run code --
    so they belong behind a capability and a confirmation (ADR-036 group B). ``base_dir`` confines
    paths.
    """

    @staticmethod
    def calculate_coverage_ratio(
        covered_lines: int,
        total_lines: int,
        threshold: float = 80.0,
    ) -> Dict[str, Any]:
        """Compute coverage percentage and check against a minimum threshold.

        Impossible input (None, NaN, negative, ``covered_lines > total_lines``) is reported as
        ``valid: False`` and never passes; 10 of 5 lines used to be 200% and "passed".
        """
        if (
            not _is_number(covered_lines)
            or not _is_number(total_lines)
            or not _is_number(threshold)
            or covered_lines < 0
            or covered_lines > total_lines
        ):
            return {
                "covered_lines": covered_lines,
                "total_lines": total_lines,
                "coverage_percentage": 0.0,
                "threshold": threshold,
                "passed_threshold": False,
                "valid": False,
                "error": "covered_lines and total_lines must be numbers with 0 <= covered_lines <= total_lines "
                         "and threshold must be a number.",
            }
        if total_lines <= 0:
            return {
                "covered_lines": covered_lines,
                "total_lines": total_lines,
                "coverage_percentage": 0.0,
                "threshold": threshold,
                "passed_threshold": False,
                "valid": True,
            }

        pct = round((covered_lines / total_lines) * 100.0, 2)
        return {
            "covered_lines": covered_lines,
            "total_lines": total_lines,
            "coverage_percentage": pct,
            "threshold": threshold,
            "passed_threshold": pct >= threshold,
            "valid": True,
        }

    @staticmethod
    def parse_coverage_json(coverage_json_path: str, *, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Parse a coverage.py JSON export file into per-file and overall metrics."""
        try:
            read = read_source_file(coverage_json_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"loaded": False, "error": str(exc)}
        if read.kind == "pdf":
            return {
                "loaded": False,
                "error": f"The file is a PDF ({read.pages} page(s), read in full); a coverage report must be "
                         "coverage.py's JSON export.",
                "input": read.describe(),
                "warnings": read.warnings(),
            }

        try:
            data = json.loads(read.text)
        except (json.JSONDecodeError, RecursionError) as exc:
            return {"loaded": False, "error": f"Invalid JSON: {exc}"}
        if not isinstance(data, dict) or not isinstance(data.get("totals"), dict) \
                or not isinstance(data.get("files", {}), dict):
            return {"loaded": False, "error": "Not a coverage.py JSON report (no 'totals' / 'files' objects)."}

        def num(value: Any, default: float = 0.0) -> float:
            return float(value) if _is_number(value) else default

        files_summary: List[Dict[str, Any]] = []
        for fname, info in data.get("files", {}).items():
            if not isinstance(info, dict):
                continue
            summary = info.get("summary", {})
            if not isinstance(summary, dict):
                summary = {}
            missing = info.get("missing_lines", [])
            files_summary.append({
                "file": fname,
                "covered_lines": summary.get("covered_lines", 0),
                "num_statements": summary.get("num_statements", 0),
                "missing_lines": missing if isinstance(missing, list) else [],
                "percent_covered": round(num(summary.get("percent_covered")), 2),
            })

        totals = data["totals"]
        return {
            "loaded": True,
            "percent_covered": round(num(totals.get("percent_covered")), 2),
            "covered_lines": totals.get("covered_lines", 0),
            "num_statements": totals.get("num_statements", 0),
            "files": files_summary,
        }

    @classmethod
    def run_pytest_with_coverage(
        cls,
        test_target: str,
        source_package: Optional[str] = None,
        working_dir: Optional[str] = None,
        threshold: float = 80.0,
        timeout_seconds: int = 60,
        *,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run pytest under coverage.py and return parsed coverage metrics if available.

        ``passed`` is true only when the tests passed, a report was produced, and coverage met the
        threshold; ``passed_threshold`` is never true without a report (threshold 0 used to "pass" a
        run that executed nothing).
        """
        if (
            not isinstance(test_target, str)
            or not test_target.strip()
            or test_target.startswith("-")
            or "\x00" in test_target
        ):
            return {"available": True, "passed": False,
                    "error": "test_target must be a path or node id (not empty, and not starting with '-')."}
        if source_package is not None and (
            not isinstance(source_package, str) or not _SOURCE_PACKAGE_RE.match(source_package)
        ):
            return {"available": True, "passed": False,
                    "error": "source_package must be a package name or directory (not starting with '-')."}
        try:
            seconds = max(1.0, min(float(timeout_seconds), 3600.0))
        except (TypeError, ValueError):
            seconds = 60.0
        try:
            cwd: Optional[str] = str(confine(working_dir, base_dir, kind="dir")) if working_dir is not None \
                else None
            if base_dir is not None:
                confine(Path(cwd or ".") / test_target.split("::", 1)[0], base_dir, must_exist=False)
        except PathRefused as exc:
            return {"available": True, "passed": False, "error": str(exc)}

        with tempfile.TemporaryDirectory() as tmpdir:
            cov_json = Path(tmpdir) / "coverage.json"
            cov_data = str(Path(tmpdir) / ".coverage")
            coverage_cmd = [sys.executable, "-m", "coverage"]
            cmd = [*coverage_cmd, "run", f"--data-file={cov_data}", "-m", "pytest", test_target, "-q"]
            if source_package:
                cmd = [
                    *coverage_cmd, "run", f"--data-file={cov_data}",
                    f"--source={source_package}", "-m", "pytest", test_target, "-q",
                ]

            try:
                proc = run_group(cmd, cwd=cwd, timeout_s=seconds, env=scrubbed_env(PYTEST_ENV))
            except FileNotFoundError:
                return {
                    "available": False,
                    "passed": False,
                    "error": "The Python interpreter could not be started.",
                }
            except OSError as exc:
                return {"available": True, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
            if proc.timed_out:
                return {
                    "available": True,
                    "passed": False,
                    "error": f"Coverage test run timed out after {seconds:g}s.",
                }

            if "No module named coverage" in (proc.stderr or ""):
                return {
                    "available": False,
                    "passed": False,
                    "error": "coverage is not installed for this interpreter.",
                }

            try:
                run_group(
                    [*coverage_cmd, "json", f"--data-file={cov_data}", "-o", str(cov_json)],
                    cwd=cwd, timeout_s=seconds, env=scrubbed_env(),
                )
            except OSError:
                pass  # the report is simply missing; parse_coverage_json reports that
            parsed = cls.parse_coverage_json(str(cov_json))
            loaded = bool(parsed.get("loaded"))
            pct = parsed.get("percent_covered", 0.0) if loaded else 0.0
            tests_passed = proc.returncode == 0
            met = loaded and pct >= threshold

            return {
                "available": True,
                "tests_passed": tests_passed,
                "coverage_percentage": pct,
                "threshold": threshold,
                "passed_threshold": met,
                "passed": tests_passed and met,
                "report": parsed,
                "exit_code": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }

    @staticmethod
    def generate_boolean_mutations(source_code: Any) -> List[Dict[str, Any]]:
        """Generate simple boolean constant mutations (True <-> False) for lightweight mutation checks.

        Accepts text or file bytes (a PDF's pages are read in full and parsed as text, which is not
        Python, so it yields nothing). Source that does not parse -- including NUL bytes -- gives ``[]``;
        use ``evaluate_mutations_against_tests`` when you need the reason.
        """
        if isinstance(source_code, (bytes, bytearray)):
            try:
                source_code = read_source_bytes(bytes(source_code)).text
            except SourceReadError:
                return []
        if not isinstance(source_code, str):
            return []
        try:
            tree = ast.parse(source_code)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return []

        mutations: List[Dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, bool):
                mutated_val = not node.value
                mutations.append({
                    "line": getattr(node, "lineno", 0),
                    "col": getattr(node, "col_offset", 0),
                    "end_col": getattr(node, "end_col_offset", 0),
                    "original": node.value,
                    "mutated": mutated_val,
                })
        return mutations

    @classmethod
    def evaluate_mutations_against_tests(
        cls,
        source_filename: str,
        working_dir: str,
        timeout_seconds: int = 30,
        *,
        base_dir: Optional[str] = None,
        max_mutants: int = 10,
    ) -> Dict[str, Any]:
        """
        Apply simple boolean mutations to a target file inside working_dir and run pytest
        to measure how many mutants are killed by the test suite.

        All mutations are performed on a temporary copy of the project -- the original
        source files are never modified. At most ``max_mutants`` (default 10, up to 50) are run;
        ``mutants_candidates`` and ``mutants_not_tested`` say how many more existed, so a score over
        a sample is never presented as a score over everything. A mutant counts as killed when pytest
        reports failures (exit 1), a collection/import error (2) or hangs; other exit codes are counted
        under ``mutants_errored`` and do not raise the score.
        """
        if not isinstance(source_filename, str) or not source_filename.strip():
            return {"evaluated": False, "error": "source_filename is required."}
        try:
            root = confine(working_dir, base_dir, kind="dir")
        except PathRefused as exc:
            return {"evaluated": False, "error": str(exc)}
        try:
            target_path = (root / source_filename).resolve()
        except (OSError, RuntimeError, ValueError):
            return {"evaluated": False, "error": f"Invalid source_filename: {source_filename!r}"}
        if target_path != root and root not in target_path.parents:
            return {"evaluated": False, "error": f"source_filename escapes working_dir: {source_filename}"}
        if not target_path.exists():
            return {"evaluated": False, "error": f"Target file not found: {target_path}"}

        try:
            read = read_source_file(target_path, base_dir=root)
        except SourceReadError as exc:
            return {"evaluated": False, "error": str(exc)}
        if read.kind == "pdf":
            return {
                "evaluated": False,
                "error": f"{source_filename} is a PDF ({read.pages} page(s), read in full), not Python source; "
                         "mutation testing needs a .py file.",
                "input": read.describe(),
                "warnings": read.warnings(),
            }
        original_text = read.text
        try:
            ast.parse(original_text)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            return {"evaluated": False, "error": f"{source_filename} is not valid Python source ({type(exc).__name__})."}
        candidates = cls.generate_boolean_mutations(original_text)

        if not candidates:
            return {
                "evaluated": True,
                "mutants_total": 0,
                "mutants_killed": 0,
                "mutation_score": None,
                "note": "No boolean constants found — mutation testing not applicable.",
            }

        try:
            seconds = max(1.0, min(float(timeout_seconds), 600.0))
            limit = max(1, min(int(max_mutants), 50))
        except (TypeError, ValueError):
            seconds, limit = 30.0, 10
        budget_error = _copy_budget_error(root)
        if budget_error:
            return {"evaluated": False, "error": budget_error}

        pytest_cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        env = scrubbed_env(PYTEST_ENV)

        # --- Work on a temporary copy so the original files are never touched ---
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_working = Path(tmpdir) / "workspace"
            try:
                shutil.copytree(root, str(tmp_working), ignore=_copy_ignore)
            except (OSError, shutil.Error) as exc:
                return {"evaluated": False, "error": f"Could not copy working_dir for mutation testing: {exc}"}
            tmp_target = tmp_working / target_path.relative_to(root)
            if not tmp_target.is_file():
                return {"evaluated": False,
                        "error": "The target file is inside an excluded directory (.git, venv, node_modules, ...)."}

            # --- Validate: tests must pass on the unmodified code first ---
            try:
                baseline = run_group(pytest_cmd, cwd=tmp_working, timeout_s=seconds, env=env)
            except FileNotFoundError:
                return {"evaluated": False, "error": "Python interpreter not found for pytest."}
            except OSError as exc:
                return {"evaluated": False, "error": f"{type(exc).__name__}: {exc}"}
            if baseline.timed_out:
                return {
                    "evaluated": False,
                    "error": (
                        f"The baseline test run timed out after {seconds:g}s. Mutation "
                        "testing requires a green, reasonably fast test suite."
                    ),
                }
            if baseline.returncode != 0:
                reason = (
                    "No tests were collected by pytest."
                    if baseline.returncode == 5
                    else "The test suite does not pass on unmodified code."
                )
                return {
                    "evaluated": False,
                    "error": (
                        f"{reason} Mutation testing requires a green test suite. "
                        f"pytest exit code: {baseline.returncode}"
                    ),
                    "stdout": baseline.stdout.strip(),
                    "stderr": baseline.stderr.strip(),
                }

            # ast numbers lines on \n, \r\n and \r only (not form feed, U+2028 ...), and its column
            # offsets are UTF-8 BYTE offsets, so work on encoded lines.
            lines = re.split(r"\r\n|\r|\n", original_text)
            killed = 0
            tested = 0
            skipped = 0
            errored = 0

            for mut in candidates:
                if tested >= limit:
                    break
                line_idx = mut["line"] - 1
                if line_idx < 0 or line_idx >= len(lines):
                    skipped += 1
                    continue

                old_bytes = lines[line_idx].encode("utf-8")
                col_start = mut["col"]
                col_end = mut["end_col"]

                # Validate that the AST offsets actually match the expected token
                target_str = "True" if mut["original"] else "False"
                if old_bytes[col_start:col_end] != target_str.encode():
                    skipped += 1
                    continue

                repl_str = "False" if mut["original"] else "True"

                # AST-precise replacement using column offsets
                mutated_line = (
                    old_bytes[:col_start] + repl_str.encode() + old_bytes[col_end:]
                ).decode("utf-8")

                tested += 1
                mutated_lines = list(lines)
                mutated_lines[line_idx] = mutated_line
                tmp_target.write_text("\n".join(mutated_lines) + "\n", encoding="utf-8")

                try:
                    proc = run_group(pytest_cmd, cwd=tmp_working, timeout_s=seconds, env=env)
                    if proc.timed_out:
                        killed += 1  # a hang caused by the mutation counts as detected
                    elif proc.returncode in (1, 2):
                        killed += 1
                    elif proc.returncode not in (0, 5):
                        errored += 1  # usage/internal error or a signal: not evidence of a kill
                except OSError:
                    errored += 1

                # Restore the copy to its original state for the next mutation
                tmp_target.write_text(original_text, encoding="utf-8")

        score = round((killed / tested) * 100.0, 2) if tested > 0 else None
        not_tested = max(0, len(candidates) - tested - skipped)
        return {
            "evaluated": True,
            "mutants_total": tested,
            "mutants_killed": killed,
            "mutants_survived": tested - killed - errored,
            "mutants_errored": errored,
            "mutants_skipped": skipped,
            "mutants_candidates": len(candidates),
            "mutants_not_tested": not_tested,
            "sampled": not_tested > 0,
            "mutation_score": score,
        }
