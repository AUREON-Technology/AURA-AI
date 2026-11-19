from typing import Any, Dict, List, Optional


class QualityGateAggregator:
    """Aggregator that combines test, lint, security, coverage, and complexity checks into one gate decision."""

    @staticmethod
    def evaluate_checks(
        security_pass: bool = True,
        tests_pass: bool = True,
        coverage_pass: bool = True,
        complexity_pass: bool = True,
        lint_pass: bool = True,
        architecture_pass: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluate boolean quality checks and return a unified APPROVED or FAILED decision."""
        checks = {
            "security": bool(security_pass),
            "tests": bool(tests_pass),
            "coverage": bool(coverage_pass),
            "complexity": bool(complexity_pass),
            "lint": bool(lint_pass),
            "architecture": bool(architecture_pass),
        }
        failed_checks = [name for name, ok in checks.items() if not ok]
        passed = len(failed_checks) == 0

        return {
            "status": "APPROVED" if passed else "FAILED",
            "passed": passed,
            "checks": checks,
            "failed_checks": failed_checks,
            "details": details or {},
        }

    @staticmethod
    def compute_quality_score(weights_and_results: Dict[str, tuple[float, bool]]) -> Dict[str, Any]:
        """Compute a weighted 0-100 quality score from (weight, passed) tuples."""
        total_weight = sum(max(0.0, w) for w, _ in weights_and_results.values())
        if total_weight <= 0:
            return {"score": 0.0, "breakdown": {}}

        earned = 0.0
        breakdown: Dict[str, Dict[str, Any]] = {}
        for name, (weight, passed) in weights_and_results.items():
            w = max(0.0, weight)
            if passed:
                earned += w
            breakdown[name] = {"weight": w, "passed": bool(passed)}

        score = round((earned / total_weight) * 100.0, 2)
        return {"score": score, "breakdown": breakdown}

    @classmethod
    def build_release_readiness_report(
        cls,
        security_pass: bool,
        tests_pass: bool,
        coverage_pass: bool,
        complexity_pass: bool,
        lint_pass: bool = True,
        architecture_pass: bool = True,
        min_score_threshold: float = 80.0,
        blocking_checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Produce a structured release/merge readiness decision combining hard blockers and score."""
        gate = cls.evaluate_checks(
            security_pass=security_pass,
            tests_pass=tests_pass,
            coverage_pass=coverage_pass,
            complexity_pass=complexity_pass,
            lint_pass=lint_pass,
            architecture_pass=architecture_pass,
        )
        score_info = cls.compute_quality_score({
            "security": (25.0, security_pass),
            "tests": (25.0, tests_pass),
            "coverage": (20.0, coverage_pass),
            "complexity": (15.0, complexity_pass),
            "lint": (10.0, lint_pass),
            "architecture": (5.0, architecture_pass),
        })

        blockers = blocking_checks if blocking_checks is not None else ["security", "tests"]
        blocker_failed = [b for b in blockers if not gate["checks"].get(b, True)]
        ready = (len(blocker_failed) == 0) and (score_info["score"] >= min_score_threshold)

        return {
            "ready_to_merge": ready,
            "gate_status": gate["status"],
            "quality_score": score_info["score"],
            "min_score_threshold": min_score_threshold,
            "blocker_failures": blocker_failed,
            "failed_checks": gate["failed_checks"],
            "score_breakdown": score_info["breakdown"],
        }