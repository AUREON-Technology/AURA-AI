import ast
import json
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
            cmd = ["coverage", "run", "-m", "pytest", test_target, "-q"]
            if source_package:
                cmd = ["coverage", "run", f"--source={source_package}", "-m", "pytest", test_target, "-q"]

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
                ["coverage", "json", "-o", str(cov_json)],
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
                    "original": node.value,
                    "mutated": mutated_val,
                })
        return mutations

    @classmethod
    def evaluate_mutations_against_tests(
        cls,
        source_code: str,
        source_filename: str,
        test_code: str,
        working_dir: str,
        timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        """
        Apply simple boolean mutations to a target file inside working_dir and run pytest
        to measure how many mutants are killed by the test suite.
        """
        target_path = Path(working_dir) / source_filename
        if not target_path.exists():
            return {"evaluated": False, "error": f"Target file not found: {target_path}"}

        original_text = target_path.read_text(encoding="utf-8")
        lines = original_text.splitlines()
        candidates = cls.generate_boolean_mutations(original_text)

        if not candidates:
            return {
                "evaluated": True,
                "mutants_total": 0,
                "mutants_killed": 0,
                "mutation_score": 100.0,
            }

        killed = 0
        tested = 0
        try:
            for mut in candidates[:10]:
                line_idx = mut["line"] - 1
                if line_idx < 0 or line_idx >= len(lines):
                    continue
                old_line = lines[line_idx]
                target_str = "True" if mut["original"] else "False"
                repl_str = "False" if mut["original"] else "True"
                if target_str not in old_line:
                    continue

                tested += 1
                lines[line_idx] = old_line.replace(target_str, repl_str, 1)
                target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

                proc = subprocess.run(
                    ["pytest", "-q"],
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
                if proc.returncode != 0:
                    killed += 1

                lines[line_idx] = old_line
                target_path.write_text(original_text, encoding="utf-8")
        finally:
            target_path.write_text(original_text, encoding="utf-8")

        score = round((killed / tested) * 100.0, 2) if tested > 0 else 100.0
        return {
            "evaluated": True,
            "mutants_total": tested,
            "mutants_killed": killed,
            "mutants_survived": tested - killed,
            "mutation_score": score,
        }