"""Turn requirements into checkable criteria and judge them against supplied evidence.

The contract that matters: **a criterion is satisfied only by evidence that affirmatively says so.**
Missing evidence is "unverified", contradicting evidence is "contradicted", and neither is a pass.
The previous matcher treated any evidence key that was a substring of (or contained in) a criterion
as a hit, accepted any non-empty string as success (``"failed"`` is truthy), matched an empty
criterion to the first evidence key, and let a test summary with ``failed: 2`` pass because it only
read the key ``failures``.

Specs may be Markdown text, or a document: ``parse_criteria_from_markdown`` also takes ``bytes``, and
:meth:`AcceptanceCriteriaVerifier.parse_criteria_from_file` takes a path. A PDF is read in full
(every page); if some pages are scans that cannot be read, the file variant says so in ``warnings``
and the text variant refuses (it can only return a list, and a list that silently omits pages would
look complete).
"""

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._input import SourceRead, SourceReadError, read_source_bytes, read_source_file


class CriteriaInputError(ValueError):
    """The specification could not be read as text (unreadable file, or a PDF with unread pages)."""


_TRUE_WORDS = {"true", "yes", "y", "ok", "pass", "passed", "passing", "success", "succeeded", "done", "verified", "met", "satisfied", "1"}
_FALSE_WORDS = {"false", "no", "n", "fail", "failed", "failing", "error", "none", "null", "0", "unmet", "pending", ""}
_FAILURE_MARK = re.compile(
    r"\b(?:fail(?:ed|ing|s|ure|ures)?|errors?|not|no|never|missing|broken|unmet|pending|incomplete|todo|regress(?:ed|ion|ions)?)\b|فشل|خطأ|لم |غير ",
    re.I,
)
_CRITERIA_HEADING = re.compile(r"(?i)acceptance|criteria|requirements?|معايير|متطلبات|شروط")


def _norm(text: Any) -> str:
    return " ".join(str(text).replace("_", " ").replace("-", " ").lower().split())


def _contains_phrase(haystack: str, needle: str) -> bool:
    """``needle`` occurs in ``haystack`` on word boundaries (both already normalised)."""
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _is_true(value: Any) -> bool:
    """Does this evidence value affirmatively say 'satisfied'?"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return math.isfinite(value) and value != 0
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        return not _FAILURE_MARK.search(low)  # descriptive evidence counts unless it reports a problem
    if isinstance(value, dict):
        return bool(value) and all(_is_true(v) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return bool(value) and all(_is_true(v) for v in value)
    return False


def _read_spec(source: Union[str, bytes, bytearray, None]) -> Optional[SourceRead]:
    if isinstance(source, (bytes, bytearray)):
        try:
            return read_source_bytes(bytes(source), name="the specification")
        except SourceReadError as exc:
            raise CriteriaInputError(str(exc)) from exc
    return None


def _parse_text(markdown_text: str) -> List[str]:
    criteria: List[str] = []
    in_criteria_section = False
    for line in markdown_text.splitlines():
        line_str = line.strip()
        heading = re.match(r"^#{1,6}\s+(.*)$", line_str)
        if heading:
            in_criteria_section = bool(_CRITERIA_HEADING.search(heading.group(1)))
            continue

        chk = re.match(r"^[-*+]\s*\[[\sxX]\]\s*(.+)$", line_str)
        if chk:
            criteria.append(chk.group(1).strip())
            continue

        ghk = re.match(r"^(?:Given|When|Then|And)\s+(.+)$", line_str, re.IGNORECASE)
        if ghk:
            criteria.append(ghk.group(1).strip())
            continue

        # "1. text" / "1) text" -- any script, any case (the old pattern required a capital Latin
        # letter, which silently dropped every Arabic or lower-case requirement).
        num = re.match(r"^\d+[.)]\s+(\S.*)$", line_str)
        if num and len(num.group(1)) > 8:
            criteria.append(num.group(1).strip())
            continue

        bullet = re.match(r"^[-*+]\s+(\S.*)$", line_str)
        if bullet and in_criteria_section:
            criteria.append(bullet.group(1).strip())

    return list(dict.fromkeys(criteria))


class AcceptanceCriteriaVerifier:
    """Verifier for converting requirements into testable criteria and validating evidence."""

    @staticmethod
    def parse_criteria_from_markdown(markdown_text: Union[str, bytes, None]) -> List[str]:
        """Extract criteria: checklist items, Gherkin steps, numbered requirements (any script), and
        plain bullets under a heading about criteria/requirements/acceptance (or its Arabic).

        ``bytes`` are accepted (a PDF is read in full); a PDF with unreadable scanned pages raises
        :class:`CriteriaInputError` rather than returning a partial list. Use
        :meth:`parse_criteria_from_file` to receive coverage details instead.
        """
        if markdown_text is None:
            return []
        read = _read_spec(markdown_text)
        if read is not None:
            if not read.complete:
                raise CriteriaInputError("; ".join(read.warnings()) + " Use parse_criteria_from_file for details.")
            return _parse_text(read.text)
        if not isinstance(markdown_text, str):
            raise CriteriaInputError(f"markdown_text must be a string, got {type(markdown_text).__name__}.")
        return _parse_text(markdown_text)

    @staticmethod
    def parse_criteria_from_file(file_path: str, base_dir: Union[str, Path, None] = None) -> Dict[str, Any]:
        """Read a spec file (text or PDF, every page) and extract its criteria.

        ``{"criteria": [...], "input": {...coverage...}, "complete": bool, "warnings": [...]}`` or
        ``{"criteria": [], "error": "..."}``. When ``complete`` is false the list covers only the
        readable pages.
        """
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"criteria": [], "complete": False, "error": str(exc)}
        except (OSError, TypeError, ValueError) as exc:
            return {"criteria": [], "complete": False, "error": f"Invalid path: {type(exc).__name__}"}
        criteria = _parse_text(read.text)
        result: Dict[str, Any] = {
            "criteria": criteria,
            "input": read.describe(),
            "complete": read.complete,
            "warnings": read.warnings(),
        }
        if not criteria:
            result["warnings"] = result["warnings"] + ["No criteria were recognised in the document."]
        return result

    @staticmethod
    def convert_requirements_to_criteria(requirements: List[str]) -> List[Dict[str, Any]]:
        """Transform free-form requirements into structured verifiable criteria with verification types."""
        if isinstance(requirements, str):
            requirements = [requirements]
        if not isinstance(requirements, (list, tuple, set)):
            requirements = []
        structured = []
        idx = 0
        for req in requirements or []:
            text = "" if req is None else str(req).strip()
            if not text:
                continue
            idx += 1
            low = text.lower()
            if re.search(r"\b(?:tests?|pass(?:es|ed|ing)?|unit|coverage|pytest)\b|اختبار", low):
                proof_type = "TEST_PASS"
            elif re.search(r"\b(?:files?|exists?|created|paths?)\b|ملف|مسار", low):
                proof_type = "FILE_EXISTS"
            elif re.search(r"\b(?:lint|linted|format|formatted|clean|pep8|style)\b", low):
                proof_type = "LINT_CLEAN"
            elif re.search(r"\b(?:fast|latency|ms|budget|performance)\b|سرعة|أداء|زمن", low):
                proof_type = "LATENCY_BUDGET"
            else:
                proof_type = "FUNCTIONAL_OUTPUT"

            structured.append({
                "criterion_id": f"AC-{idx:02d}",
                "description": text,
                "proof_type": proof_type,
                "required": True,
            })
        return structured

    @staticmethod
    def verify_criteria(criteria: List[str], evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate each criterion against the evidence.

        A criterion is satisfied only when some evidence key equals it (or names it / is named by it
        on word boundaries) with an affirmative value, or a list/string value mentions it
        affirmatively, and *no* matching evidence contradicts it. ``unverified_criteria`` lists those
        with no matching evidence, ``contradicted_criteria`` those with evidence saying otherwise.
        Duplicates are counted once. An empty criterion is never satisfied.
        """
        if isinstance(criteria, str):
            criteria = [criteria]
        if not isinstance(criteria, (list, tuple, set)):
            criteria = []
        if not isinstance(evidence, dict):
            evidence = {}
        unique: List[str] = []
        for c in criteria or []:
            text = "" if c is None else str(c)
            if text not in unique:
                unique.append(text)

        breakdown: Dict[str, bool] = {}
        unverified: List[str] = []
        contradicted: List[str] = []

        for criterion in unique:
            crit_norm = _norm(criterion)
            hits_true = 0
            hits_false = 0

            if crit_norm:
                for ev_key, ev_val in evidence.items():
                    key_norm = _norm(ev_key)
                    key_matches = bool(key_norm) and len(key_norm) >= 3 and (
                        key_norm == crit_norm
                        or _contains_phrase(crit_norm, key_norm)
                        or _contains_phrase(key_norm, crit_norm)
                    )
                    if key_matches:
                        if _is_true(ev_val):
                            hits_true += 1
                        else:
                            hits_false += 1
                        continue
                    if isinstance(ev_val, (list, tuple, set, frozenset)):
                        for item in ev_val:
                            if _contains_phrase(_norm(item), crit_norm):
                                if _is_true(item) and not _FAILURE_MARK.search(str(item)):
                                    hits_true += 1
                                else:
                                    hits_false += 1
                    elif isinstance(ev_val, str) and _contains_phrase(_norm(ev_val), crit_norm):
                        if _FAILURE_MARK.search(ev_val):
                            hits_false += 1
                        else:
                            hits_true += 1

            ok = hits_true > 0 and hits_false == 0
            breakdown[criterion] = ok
            if hits_false:
                contradicted.append(criterion)
            elif not hits_true:
                unverified.append(criterion)

        verified_count = sum(1 for ok in breakdown.values() if ok)
        total = len(breakdown)
        all_passed = (verified_count == total) and total > 0

        return {
            "all_satisfied": all_passed,
            "verified_count": verified_count,
            "total_criteria": total,
            "breakdown": breakdown,
            "unverified_criteria": unverified,
            "contradicted_criteria": contradicted,
            "status": "ACCEPTED" if all_passed else "REJECTED_CRITERIA_PENDING",
        }

    @classmethod
    def verify_against_test_results(
        cls,
        criteria: List[str],
        test_summary: Dict[str, Any],
        minimum_coverage: float = 80.0,
    ) -> Dict[str, Any]:
        """Evaluate criteria against automated test suite results and coverage percentage.

        Failures are read from ``failures``, ``failed`` and ``errors`` (a pytest-style summary uses
        ``failed``); any positive count means the suite did not pass. Only a criterion that is
        itself about tests/coverage is satisfied by a green suite.
        """
        summary = test_summary if isinstance(test_summary, dict) else {}

        def count(*keys: str) -> int:
            total = 0
            for k in keys:
                v = summary.get(k)
                if isinstance(v, bool):
                    total += int(v)
                elif isinstance(v, (int, float)) and math.isfinite(v):
                    total += max(0, int(v))
            return total

        failures = count("failures", "failed", "errors")
        passed_raw = summary.get("tests_passed", summary.get("passed", False))
        if isinstance(passed_raw, bool):
            passed = passed_raw
        elif isinstance(passed_raw, (int, float)):
            passed = math.isfinite(passed_raw) and passed_raw > 0
        else:
            passed = False
        try:
            cov = float(summary.get("coverage_percentage", 0.0))
        except (TypeError, ValueError):
            cov = 0.0
        try:
            min_cov = float(minimum_coverage)
        except (TypeError, ValueError):
            min_cov = 80.0

        tests_ok = passed and failures == 0
        coverage_ok = cov >= min_cov  # False for NaN
        evidence: Dict[str, Any] = {
            "tests pass": tests_ok,
            "coverage sufficient": coverage_ok,
            "zero regressions": failures == 0 and bool(summary),
        }
        if isinstance(criteria, str):
            criteria = [criteria]
        if not isinstance(criteria, (list, tuple, set)):
            criteria = []
        for crit in criteria:
            low = str(crit).lower()
            if re.search(r"\bcoverage\b", low):
                evidence[str(crit)] = coverage_ok
            elif re.search(r"\b(?:tests?|pytest|test suite|unit tests?)\b", low):
                evidence[str(crit)] = tests_ok

        result = cls.verify_criteria(criteria, evidence)
        result["test_summary_used"] = {"tests_ok": tests_ok, "failures": failures, "coverage_percentage": cov}
        return result

    @staticmethod
    def generate_acceptance_badge_report(verification_result: Dict[str, Any]) -> str:
        """Render a Markdown badge report showing criteria breakdown.

        The badge is computed from the counts, not just from ``all_satisfied``, so a hand-built
        result that says "all satisfied" with 2 of 5 verified still renders as failed.
        """
        vr = verification_result if isinstance(verification_result, dict) else {}
        v_count = vr.get("verified_count", 0)
        t_count = vr.get("total_criteria", 0)
        try:
            all_ok = bool(vr.get("all_satisfied", False)) and int(t_count) > 0 and int(v_count) == int(t_count)
        except (TypeError, ValueError):
            all_ok = False
        badge = "🟢 **PASSED / ACCEPTED**" if all_ok else "🔴 **PENDING / CRITERIA FAILED**"

        lines = [
            "# 🎯 Acceptance Criteria Verification",
            f"**Status:** {badge}",
            f"**Score:** {v_count} / {t_count} criteria satisfied",
            "",
            "| ID | Criterion | Result |",
            "|:---|:---|:---:|",
        ]
        breakdown = vr.get("breakdown", {})
        if not isinstance(breakdown, dict):
            breakdown = {}
        for idx, (crit, ok) in enumerate(breakdown.items(), start=1):
            res_icon = "✅ PASSED" if ok else "❌ PENDING"
            cell = " ".join(str(crit).split()).replace("|", "\\|")
            lines.append(f"| AC-{idx:02d} | {cell} | {res_icon} |")

        return "\n".join(lines)
