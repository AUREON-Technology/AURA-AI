from typing import Any, Dict, List


class AcceptanceCriteriaVerifier:
    """Verifier for checking whether acceptance criteria are satisfied against collected evidence."""

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
        all_passed = verified_count == total

        return {
            "all_satisfied": all_passed,
            "verified_count": verified_count,
            "total_criteria": total,
            "breakdown": breakdown,
            "status": "ACCEPTED" if all_passed else "REJECTED_CRITERIA_PENDING",
        }