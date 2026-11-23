"""Evidence-carrying quality gate over any subset of checks (ADR-044, row 24).

``QualityGateAggregator`` takes six booleans. This accepts, per check, either a boolean or the
*result object another tool returned* (``review_code_diff``, ``generate_and_run_tests``,
``analyze_test_quality``, ``scan_code_security``, ``analyze_code_complexity``, ...), decides pass/fail
from the object, and keeps the reason as ``evidence`` so the decision can be audited rather than
trusted. Known check names: security, tests, coverage, complexity, lint, architecture, review,
mutation; any other name is a custom check that must be a boolean or an object with ``passed``.

Rules that make it safe to rely on:

* **Fail closed.** A required check that was not supplied is ``not_run`` and fails the gate; a result
  object that cannot be read (no recognisable fields) fails with ``reason`` saying so. A boolean
  is only a boolean (the string ``"false"`` is invalid, not truthy).
* **Any subset.** ``required_checks`` names what must be present; everything else supplied still
  contributes to the score but cannot block unless listed in ``blocking_checks``.
* **Both inputs agree or the gate fails.** A check given as a boolean *and* as a result object that
  disagree fails with the disagreement recorded.
* ``ready_to_merge`` additionally requires the gate itself to be APPROVED, so the two verdicts can
  no longer disagree (the boolean-only readiness report can say ready while the gate says FAILED).
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["evaluate", "KNOWN_CHECKS", "DEFAULT_WEIGHTS"]

DEFAULT_WEIGHTS: dict[str, float] = {
    "security": 25.0, "tests": 25.0, "coverage": 15.0, "complexity": 10.0, "review": 10.0,
    "mutation": 5.0, "lint": 5.0, "architecture": 5.0,
}
KNOWN_CHECKS = tuple(DEFAULT_WEIGHTS)
DEFAULT_REQUIRED = ("security", "tests", "coverage", "complexity")
DEFAULT_BLOCKING = ("security", "tests")
_MAX_CUSTOM = 20


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _verdict(name: str, value: Any, min_mutation_score: float, min_coverage: float) -> tuple[bool | None, str]:
    """``(passed, reason)``; ``passed`` is None when the value is unusable (counts as a failure)."""
    if isinstance(value, bool):
        return value, "supplied as a boolean"
    if not isinstance(value, dict):
        return None, f"expected a boolean or a result object, got {type(value).__name__}"
    if "passed" in value and isinstance(value["passed"], bool) and name not in ("tests", "coverage", "mutation"):
        return value["passed"], f"result says passed={value['passed']}"

    if name == "review":
        if isinstance(value.get("approved"), bool):
            crit, major = _count(value.get("critical_issues")) or 0, _count(value.get("major_issues")) or 0
            deep = value.get("deep_review") or {}
            return bool(value["approved"]), (
                f"approved={value['approved']}, {crit} critical, {major} major, "
                f"{(deep.get('by_category') or {}) or 'no deep findings'}")
        return None, "review result has no 'approved' field"
    if name == "tests":
        execution = value.get("execution") if isinstance(value.get("execution"), dict) else value
        counts = execution.get("counts") if isinstance(execution.get("counts"), dict) else execution
        failed, errors, passed = (_count(counts.get(k)) for k in ("failed", "errors", "passed"))
        if passed is None and failed is None and isinstance(value.get("verified"), bool):
            return value["verified"], f"verified={value['verified']}"
        if passed is None and failed is None:
            return None, "no passed/failed counts in the test result"
        ok = (failed or 0) == 0 and (errors or 0) == 0 and (passed or 0) > 0
        return ok, f"{passed or 0} passed, {failed or 0} failed, {errors or 0} errors" + (
            f"; verification level {value.get('verification_level')}" if value.get("verification_level") else "")
    if name == "coverage":
        scoped = value.get("changed_line_coverage")
        cov = value.get("coverage") if isinstance(value.get("coverage"), dict) else value
        if isinstance(scoped, dict) and isinstance(scoped.get("percent_covered"), (int, float)):
            pct = float(scoped["percent_covered"])
            return pct >= min_coverage, f"changed lines {pct}% covered (needs {min_coverage}%)"
        pct = cov.get("coverage_percentage", cov.get("percent_covered"))
        if isinstance(pct, (int, float)) and not isinstance(pct, bool) and math.isfinite(pct):
            return float(pct) >= min_coverage, f"{pct}% covered (needs {min_coverage}%)"
        ratio = cov.get("coverage_ratio")
        if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and math.isfinite(ratio):
            return ratio * 100 >= min_coverage, f"{round(ratio * 100, 2)}% covered (needs {min_coverage}%)"
        if isinstance(cov.get("passed_threshold"), bool):
            return cov["passed_threshold"], f"passed_threshold={cov['passed_threshold']}"
        return None, "no coverage figure in the result"
    if name == "mutation":
        mut = value.get("mutation") if isinstance(value.get("mutation"), dict) else value
        score = mut.get("mutation_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return float(score) >= min_mutation_score, (
                f"mutation score {score}% over {mut.get('mutants_total')} mutants (needs {min_mutation_score}%)")
        if mut.get("mutants_total") == 0:
            return None, "no mutants were run, so the tests' strength is unmeasured"
        return None, "no mutation score in the result"
    if name == "security":
        findings = value.get("findings")
        if isinstance(findings, (list, tuple)):
            bad = [f for f in findings if not isinstance(f, dict) or str(f.get("severity", "")).upper()
                   in ("CRITICAL", "HIGH", "MAJOR", "MEDIUM", "")]
            ok = (not bad and value.get("clean", True) is not False and value.get("secure", True) is not False
                  and value.get("has_secrets", False) is not True and value.get("complete", True) is not False)
            return ok, f"{len(findings)} findings ({len(bad)} medium or worse)"
        for flag in ("secure", "clean"):
            if isinstance(value.get(flag), bool):
                return value[flag], f"{flag}={value[flag]}"
        return None, "no findings list in the security result"
    if name == "complexity":
        flagged = _count(value.get("flagged_count"))
        if flagged is None and isinstance(value.get("functions"), list):
            flagged = sum(1 for f in value["functions"] if isinstance(f, dict) and f.get("flagged"))
        if flagged is None:
            return None, "no flagged_count in the complexity result"
        return flagged == 0, f"{flagged} function(s) over the limits"
    if name == "lint":
        for key in ("passed", "clean", "ok"):
            if isinstance(value.get(key), bool):
                return value[key], f"{key}={value[key]}"
        code = value.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool):
            return code == 0, f"exit_code={code}"
        return None, "no pass flag or exit_code in the lint result"
    if name == "architecture":
        violations = value.get("violations", [])
        if value.get("is_consistent") is True and isinstance(violations, (list, tuple)) and not violations:
            return True, "no layer violations"
        if "is_consistent" in value or violations:
            return False, f"{len(violations) if isinstance(violations, (list, tuple)) else '?'} violation(s)"
        return None, "no is_consistent field in the architecture result"
    return None, f"custom check '{name}' needs a boolean or an object with a boolean 'passed'"


def evaluate(
    *, operation: str, booleans: dict[str, bool | None], results: dict[str, Any],
    required: list[str] | None = None, blocking: list[str] | None = None,
    weights: dict[str, float] | None = None, min_score: float = 80.0,
    min_mutation_score: float = 50.0, min_coverage: float = 80.0,
) -> dict[str, Any]:
    custom = [n for n in results if n not in KNOWN_CHECKS]
    if len(custom) > _MAX_CUSTOM:
        raise ValueError(f"at most {_MAX_CUSTOM} custom checks, got {len(custom)}")
    w = dict(DEFAULT_WEIGHTS)
    for name, weight in (weights or {}).items():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0:
            raise ValueError(f"weight for '{name}' must be a non-negative number")
        w[str(name)] = float(weight)
    for name in custom:
        w.setdefault(name, 5.0)
    names = list(dict.fromkeys([*KNOWN_CHECKS, *custom]))
    required_set = list(required) if required is not None else list(DEFAULT_REQUIRED)
    blocking_set = list(blocking) if blocking is not None else list(DEFAULT_BLOCKING)
    unknown = sorted({*required_set, *blocking_set} - set(names))
    if unknown:
        raise ValueError(f"Unknown check name(s) in required/blocking: {unknown}. Known: {sorted(names)}")

    evidence: dict[str, dict[str, Any]] = {}
    verdicts: dict[str, bool] = {}
    invalid: list[str] = []
    for name in names:
        from_bool = booleans.get(name)
        from_obj = results.get(name)
        bool_ok = from_bool if isinstance(from_bool, bool) else None
        if from_bool is not None and not isinstance(from_bool, bool):
            invalid.append(name)
            evidence[name] = {"passed": False, "source": "boolean", "reason": "not a boolean, so not a result"}
            verdicts[name] = False
            continue
        obj_ok, reason = (None, "")
        if from_obj is not None:
            obj_ok, reason = _verdict(name, from_obj, min_mutation_score, min_coverage)
            if obj_ok is None:
                invalid.append(name)
                evidence[name] = {"passed": False, "source": "result", "reason": f"unreadable: {reason}"}
                verdicts[name] = False
                continue
        if bool_ok is not None and obj_ok is not None and bool_ok != obj_ok:
            evidence[name] = {"passed": False, "source": "boolean+result",
                              "reason": f"the boolean ({bool_ok}) and the result object ({obj_ok}: {reason}) disagree"}
            verdicts[name] = False
        elif obj_ok is not None:
            evidence[name] = {"passed": obj_ok, "source": "result", "reason": reason}
            verdicts[name] = obj_ok
        elif bool_ok is not None:
            evidence[name] = {"passed": bool_ok, "source": "boolean", "reason": "supplied as a boolean, no evidence attached"}
            verdicts[name] = bool_ok
    not_run = [n for n in names if n not in verdicts and n in (set(required_set) | set(blocking_set))]
    optional_not_run = [n for n in names if n not in verdicts and n not in not_run]
    failed = [n for n, ok in verdicts.items() if not ok] + [n for n in not_run if n not in verdicts]
    gate_passed = bool(verdicts) and not failed
    gate = {
        "status": "APPROVED" if gate_passed else "FAILED", "passed": gate_passed,
        "checks": verdicts, "not_run": not_run + optional_not_run, "failed_checks": failed,
        "evidence": evidence, "required_checks": required_set,
    }
    if invalid:
        gate["invalid_checks"] = invalid
    if not verdicts:
        gate["error"] = "No check result was supplied, so nothing was approved."
    if operation == "gate":
        return gate

    scored = {n: (w.get(n, 0.0), verdicts.get(n, False)) for n in names
              if n in verdicts or n in required_set or n in blocking_set}
    total = sum(weight for weight, _ in scored.values())
    score = round(sum(weight for weight, ok in scored.values() if ok) / total * 100.0, 2) if total > 0 else 0.0
    blockers = [b for b in blocking_set if b in failed or b in not_run]
    ready = bool(verdicts) and not blockers and score >= min_score and gate_passed
    return {
        "ready_to_merge": ready, "gate_status": gate["status"], "quality_score": score,
        "min_score_threshold": min_score, "blocker_failures": blockers, "failed_checks": failed,
        "not_run": gate["not_run"], "evidence": evidence,
        "score_breakdown": {n: {"weight": wt, "passed": ok} for n, (wt, ok) in scored.items()},
        "invalid_checks": invalid,
        "note": "ready_to_merge also requires the gate to be APPROVED in this mode",
    }
