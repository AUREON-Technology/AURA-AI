import ast
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


class TestCoverageAndMutationAnalyzer:
    """Analyzer for line/branch test coverage percentages, coverage reports, and basic mutation checks."""

    @staticmethod
    def calculate_coverage_ratio(
        covered_lines: int,
        total_lines: int,
        threshold: float = 80.0,
    ) -> Dict[str, Any]:
        """Compute coverage percentage and check against a minimum threshold."""
        if total_lines <= 0:
            return {
                "covered_lines": covered_lines,
                "total_lines": total_lines,
                "coverage_percentage": 0.0,
                "threshold": threshold,
                "passed_threshold": False,
            }

        pct = round((covered_lines / total_lines) * 100.0, 2)
        return {
            "covered_lines": covered_lines,
            "total_lines": total_lines,
            "coverage_percentage": pct,
            "threshold": threshold,
            "passed_threshold": pct >= threshold,
        }

    @staticmethod
    def parse_coverage_json(coverage_json_path: str) -> Dict[str, Any]:
        """Parse a coverage.py JSON export file into per-file and overall metrics."""
        path = Path(coverage_json_path)
        if not path.exists():
            return {"loaded": False, "error": f"Coverage JSON not found: {coverage_json_path}"}

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {"loaded": False, "error": f"Invalid JSON: {exc}"}

        files_summary: List[Dict[str, Any]] = []
        for fname, info in data.get("files", {}).items():
            summary = info.get("summary", {})
            files_summary.append({
                "file": fname,
                "covered_lines": summary.get("covered_lines", 0),
                "num_statements": summary.get("num_statements", 0),
                "missing_lines": info.get("missing_lines", []),
                "percent_covered": round(float(summary.get("percent_covered", 0.0)), 2),
            })

        totals = data.get("totals", {})
        return {
            "loaded": True,
            "percent_covered": round(float(totals.get("percent_covered", 0.0)), 2),
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
    ) -> Dict[str, Any]:
        """Run pytest under coverage.py and return parsed coverage metrics if available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cov_json = Path(tmpdir) / "coverage.json"
            cov_data = str(Path(tmpdir) / ".coverage")
            cmd = ["coverage", "run", f"--data-file={cov_data}", "-m", "pytest", test_target, "-q"]
            if source_package:
                cmd = [
                    "coverage", "run", f"--data-file={cov_data}",
                    f"--source={source_package}", "-m", "pytest", test_target, "-q",
                ]

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except FileNotFoundError:
                return {
                    "available": False,
                    "passed": False,
                    "error": "coverage CLI not installed in current environment.",
                }
            except subprocess.TimeoutExpired:
                return {
                    "available": True,
                    "passed": False,
                    "error": f"Coverage test run timed out after {timeout_seconds}s.",
                }

            subprocess.run(
                ["coverage", "json", f"--data-file={cov_data}", "-o", str(cov_json)],
                cwd=working_dir,
                capture_output=True,
                text=True,
            )
            parsed = cls.parse_coverage_json(str(cov_json))
            pct = parsed.get("percent_covered", 0.0) if parsed.get("loaded") else 0.0

            return {
                "available": True,
                "tests_passed": proc.returncode == 0,
                "coverage_percentage": pct,
                "threshold": threshold,
                "passed_threshold": pct >= threshold,
                "report": parsed,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }

    @staticmethod
    def generate_boolean_mutations(source_code: str) -> List[Dict[str, Any]]:
        """Generate simple boolean constant mutations (True <-> False) for lightweight mutation checks."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
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
    ) -> Dict[str, Any]:
        """
        Apply simple boolean mutations to a target file inside working_dir and run pytest
        to measure how many mutants are killed by the test suite.

        All mutations are performed on a temporary copy of the project — the original
        source files are never modified.
        """
        target_path = Path(working_dir) / source_filename
        if not target_path.exists():
            return {"evaluated": False, "error": f"Target file not found: {target_path}"}

        original_text = target_path.read_text(encoding="utf-8")
        candidates = cls.generate_boolean_mutations(original_text)

        if not candidates:
            return {
                "evaluated": True,
                "mutants_total": 0,
                "mutants_killed": 0,
                "mutation_score": None,
                "note": "No boolean constants found — mutation testing not applicable.",
            }

        # --- Work on a temporary copy so the original files are never touched ---
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_working = Path(tmpdir) / "workspace"
            shutil.copytree(working_dir, str(tmp_working))
            tmp_target = tmp_working / source_filename

            # --- Validate: tests must pass on the unmodified code first ---
            baseline = subprocess.run(
                ["pytest", "-q"],
                cwd=str(tmp_working),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
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

            lines = original_text.splitlines()
            killed = 0
            tested = 0

            for mut in candidates[:10]:
                line_idx = mut["line"] - 1
                if line_idx < 0 or line_idx >= len(lines):
                    continue

                old_line = lines[line_idx]
                col_start = mut["col"]
                col_end = mut["end_col"]

                # Validate that the AST offsets actually match the expected token
                target_str = "True" if mut["original"] else "False"
                if old_line[col_start:col_end] != target_str:
                    continue

                repl_str = "False" if mut["original"] else "True"

                # AST-precise replacement using column offsets
                mutated_line = old_line[:col_start] + repl_str + old_line[col_end:]

                tested += 1
                mutated_lines = list(lines)
                mutated_lines[line_idx] = mutated_line
                tmp_target.write_text("\n".join(mutated_lines) + "\n", encoding="utf-8")

                try:
                    proc = subprocess.run(
                        ["pytest", "-q"],
                        cwd=str(tmp_working),
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                    )
                    # Exit code 5 means no tests collected — not a kill
                    if proc.returncode != 0 and proc.returncode != 5:
                        killed += 1
                except subprocess.TimeoutExpired:
                    # Timeout means the mutant caused a hang — treat as killed
                    killed += 1

                # Restore the copy to its original state for the next mutation
                tmp_target.write_text(original_text, encoding="utf-8")

        score = round((killed / tested) * 100.0, 2) if tested > 0 else None
        return {
            "evaluated": True,
            "mutants_total": tested,
            "mutants_killed": killed,
            "mutants_survived": tested - killed,
            "mutation_score": score,
        }
