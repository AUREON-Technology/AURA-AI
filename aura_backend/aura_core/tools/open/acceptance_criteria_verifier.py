import re
from typing import Any, Dict, List, Optional


class AcceptanceCriteriaVerifier:
    """Verifier for converting requirements into testable criteria and validating evidence."""

    @staticmethod
    def parse_criteria_from_markdown(markdown_text: str) -> List[str]:
        """Extract criteria from Markdown checklists (- [ ], - [x]) or bulleted requirements."""
        criteria: List[str] = []
        for line in markdown_text.splitlines():
            line_str = line.strip()
            # Match checklist item
            chk = re.match(r"^[-*]\s*\[[\sxX]\]\s*(.+)$", line_str)
            if chk:
                criteria.append(chk.group(1).strip())
                continue

            # Match Gherkin scenarios
            ghk = re.match(r"^(?:Given|When|Then|And)\s+(.+)$", line_str, re.IGNORECASE)
            if ghk:
                criteria.append(ghk.group(1).strip())
                continue

            # Match numbered requirement e.g. "1. Requirement text"
            num = re.match(r"^\d+\.\s+([A-Z].+)$", line_str)
            if num and len(num.group(1)) > 8:
                criteria.append(num.group(1).strip())

        return list(dict.fromkeys(criteria))

    @staticmethod
    def convert_requirements_to_criteria(requirements: List[str]) -> List[Dict[str, Any]]:
        """Transform free-form requirements into structured verifiable criteria with verification types."""
        structured = []
        for idx, req in enumerate(requirements, start=1):
            req_lower = req.lower()
            if any(w in req_lower for w in ("test", "pass", "unit", "coverage", "pytest")):
                proof_type = "TEST_PASS"
            elif any(w in req_lower for w in ("file", "exist", "created", "path")):
                proof_type = "FILE_EXISTS"
            elif any(w in req_lower for w in ("lint", "format", "clean", "pep8", "style")):
                proof_type = "LINT_CLEAN"
            elif any(w in req_lower for w in ("fast", "latency", "ms", "budget", "performance")):
                proof_type = "LATENCY_BUDGET"
            else:
                proof_type = "FUNCTIONAL_OUTPUT"

            structured.append({
                "criterion_id": f"AC-{idx:02d}",
                "description": req.strip(),
                "proof_type": proof_type,
                "required": True,
            })
        return structured

    @staticmethod
    def verify_criteria(criteria: List[str], evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate each criterion against evidence keys/values and summarize completion."""
        breakdown: Dict[str, bool] = {}

        for criterion in criteria:
            crit_norm = criterion.strip().lower()
            matched = False

            for ev_key, ev_val in evidence.items():
                key_norm = str(ev_key).strip().lower()
                if key_norm == crit_norm or key_norm in crit_norm or crit_norm in key_norm:
                    matched = bool(ev_val)
                    break
                if isinstance(ev_val, (list, tuple, set)):
                    if any(crit_norm in str(item).lower() for item in ev_val):
                        matched = True
                        break
                elif isinstance(ev_val, str) and crit_norm and crit_norm in ev_val.lower():
                    matched = True
                    break

            breakdown[criterion] = matched

        verified_count = sum(1 for ok in breakdown.values() if ok)
        total = len(criteria)
        all_passed = (verified_count == total) and total > 0

        return {
            "all_satisfied": all_passed,
            "verified_count": verified_count,
            "total_criteria": total,
            "breakdown": breakdown,
            "status": "ACCEPTED" if all_passed else "REJECTED_CRITERIA_PENDING",
        }

    @classmethod
    def verify_against_test_results(
        cls,
        criteria: List[str],
        test_summary: Dict[str, Any],
        minimum_coverage: float = 80.0,
    ) -> Dict[str, Any]:
        """Evaluate criteria against automated test suite results and coverage percentage."""
        passed = test_summary.get("tests_passed", test_summary.get("passed", False))
        cov = float(test_summary.get("coverage_percentage", 0.0))
        failures = int(test_summary.get("failures", 0))

        evidence = {
            "tests_pass": bool(passed) and failures == 0,
            "coverage_sufficient": cov >= minimum_coverage,
            "zero_regressions": failures == 0,
        }
        for crit in criteria:
            if "coverage" in crit.lower() and cov >= minimum_coverage:
                evidence[crit] = True
            elif ("test" in crit.lower() or "pass" in crit.lower()) and passed:
                evidence[crit] = True

        return cls.verify_criteria(criteria, evidence)

    @staticmethod
    def generate_acceptance_badge_report(verification_result: Dict[str, Any]) -> str:
        """Render a clean Markdown badge report showing criteria breakdown."""
        all_ok = verification_result.get("all_satisfied", False)
        badge = "🟢 **PASSED / ACCEPTED**" if all_ok else "🔴 **PENDING / CRITERIA FAILED**"
        v_count = verification_result.get("verified_count", 0)
        t_count = verification_result.get("total_criteria", 0)

        lines = [
            f"# 🎯 Acceptance Criteria Verification",
            f"**Status:** {badge}",
            f"**Score:** {v_count} / {t_count} criteria satisfied",
            "",
            "| ID | Criterion | Result |",
            "|:---|:---|:---:|",
        ]
        breakdown = verification_result.get("breakdown", {})
        for idx, (crit, ok) in enumerate(breakdown.items(), start=1):
            res_icon = "✅ PASSED" if ok else "❌ PENDING"
            lines.append(f"| AC-{idx:02d} | {crit} | {res_icon} |")

        return "\n".join(lines)
