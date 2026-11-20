from typing import Any, Dict, Iterable, List, Optional


class QualityGateAggregator:
    """Aggregator that combines test, lint, security, coverage, and complexity checks into one gate decision.

    The gate fails closed: a check that is *required* and was not supplied counts as failed and is
    listed under ``not_run``. Defaulting a missing result to "pass" would let a caller that ran
    nothing receive ``APPROVED``.
    """

    CHECK_WEIGHTS: Dict[str, float] = {
        "security": 25.0,
        "tests": 25.0,
        "coverage": 20.0,
        "complexity": 15.0,
        "lint": 10.0,
        "architecture": 5.0,
    }
    #: Required unless the caller says otherwise; ``lint`` and ``architecture`` are optional.
    DEFAULT_REQUIRED_CHECKS = ("security", "tests", "coverage", "complexity")

    @classmethod
    def _validated(cls, names: Iterable[str], label: str) -> List[str]:
        names = list(names)
        unknown = sorted(set(names) - set(cls.CHECK_WEIGHTS))
        if unknown:
            raise ValueError(
                f"Unknown check name(s) in {label}: {unknown}. Known: {sorted(cls.CHECK_WEIGHTS)}"
            )
        return names

    @classmethod
    def evaluate_checks(
        cls,
        security_pass: Optional[bool] = None,
        tests_pass: Optional[bool] = None,
        coverage_pass: Optional[bool] = None,
        complexity_pass: Optional[bool] = None,
        lint_pass: Optional[bool] = None,
        architecture_pass: Optional[bool] = None,
        details: Optional[Dict[str, Any]] = None,
        required_checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Evaluate boolean quality checks and return a unified APPROVED or FAILED decision.

        ``None`` means "not run". A not-run check that is in ``required_checks`` (default:
        security, tests, coverage, complexity) fails the gate; a not-run optional check is skipped.
        """
        supplied = {
            "security": security_pass,
            "tests": tests_pass,
            "coverage": coverage_pass,
            "complexity": complexity_pass,
            "lint": lint_pass,
            "architecture": architecture_pass,
        }
        required = set(
            cls._validated(
                required_checks if required_checks is not None else cls.DEFAULT_REQUIRED_CHECKS,
                "required_checks",
            )
        )
        checks = {name: bool(v) for name, v in supplied.items() if v is not None}
        not_run = [name for name, v in supplied.items() if v is None]
        failed_checks = [name for name, ok in checks.items() if not ok]
        failed_checks += [name for name in not_run if name in required]
        passed = len(failed_checks) == 0

        return {
            "status": "APPROVED" if passed else "FAILED",
            "passed": passed,
            "checks": checks,
            "not_run": not_run,
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
        security_pass: Optional[bool] = None,
        tests_pass: Optional[bool] = None,
        coverage_pass: Optional[bool] = None,
        complexity_pass: Optional[bool] = None,
        lint_pass: Optional[bool] = None,
        architecture_pass: Optional[bool] = None,
        min_score_threshold: float = 80.0,
        blocking_checks: Optional[List[str]] = None,
        required_checks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Produce a structured release/merge readiness decision combining hard blockers and score.

        A blocking check that was not run is a blocker failure. Unknown names in
        ``blocking_checks`` or ``required_checks`` raise ``ValueError`` instead of being treated
        as passing.
        """
        blockers = cls._validated(
            blocking_checks if blocking_checks is not None else ["security", "tests"],
            "blocking_checks",
        )
        gate = cls.evaluate_checks(
            security_pass=security_pass,
            tests_pass=tests_pass,
            coverage_pass=coverage_pass,
            complexity_pass=complexity_pass,
            lint_pass=lint_pass,
            architecture_pass=architecture_pass,
            required_checks=required_checks,
        )
        required = set(
            required_checks if required_checks is not None else cls.DEFAULT_REQUIRED_CHECKS
        )

        weighted: Dict[str, tuple[float, bool]] = {}
        for name, weight in cls.CHECK_WEIGHTS.items():
            if name in gate["checks"]:
                weighted[name] = (weight, gate["checks"][name])
            elif name in required:
                weighted[name] = (weight, False)
        score_info = cls.compute_quality_score(weighted)

        blocker_failed = [
            b for b in blockers if b in gate["failed_checks"] or b in gate["not_run"]
        ]
        ready = (len(blocker_failed) == 0) and (score_info["score"] >= min_score_threshold)

        return {
            "ready_to_merge": ready,
            "gate_status": gate["status"],
            "quality_score": score_info["score"],
            "min_score_threshold": min_score_threshold,
            "blocker_failures": blocker_failed,
            "failed_checks": gate["failed_checks"],
            "not_run": gate["not_run"],
            "score_breakdown": score_info["breakdown"],
        }
