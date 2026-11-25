"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Scores ambiguity from a
list of vague words; the model judges whether a request is ambiguous better than a word list,
so it is not worth the planner prompt it would cost.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._input import SourceRead, SourceReadError, read_source_bytes, read_source_file


class AmbiguityInputError(ValueError):
    """The text argument is unusable (wrong type, unreadable bytes, or a PDF with unread pages)."""


_ARABIC = re.compile(r"[\u0600-\u06FF]")
_TASHKEEL = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u0640]")


def _fold(text: str) -> str:
    """Lower-case, drop Arabic diacritics/tatweel, unify alef/ya/ta-marbuta forms for matching."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _TASHKEEL.sub("", text)
    return text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))


def _has_term(term: str, folded_text: str) -> bool:
    """Whole-word match for Latin terms; substring match for Arabic ones (clitics such as و/ب/ال attach)."""
    folded_term = _fold(term)
    if _ARABIC.search(folded_term):
        return folded_term in folded_text
    return re.search(rf"\b{re.escape(folded_term)}\b", folded_text) is not None


def _coerce_text(value: Any) -> "tuple[str, Optional[SourceRead]]":
    """Text of a str / None / bytes argument. A PDF is read in full; unread pages raise."""
    if value is None:
        return "", None
    if isinstance(value, str):
        return value, None
    if isinstance(value, (bytes, bytearray)):
        try:
            read = read_source_bytes(bytes(value), name="the specification")
        except SourceReadError as exc:
            raise AmbiguityInputError(str(exc)) from exc
        if not read.complete:
            raise AmbiguityInputError(
                " ".join(read.warnings()) + " Use analyze_document(path) to get the partial result with coverage."
            )
        return read.text, read
    raise AmbiguityInputError(f"Expected text, got {type(value).__name__}.")


class AmbiguityAndClarificationDetector:
    """Detector for identifying vague requirements, missing specifications, and generating structured clarification questions.

    The checks are keyword heuristics in English and Arabic: a ``READY_FOR_EXECUTION`` verdict means
    "no ambiguity pattern matched", not "the specification is complete". Conflict detection is
    English only. Text may also be ``bytes`` (a PDF is read in full, every page); use
    :meth:`analyze_document` to analyse a file and get its page coverage.
    """

    VAGUE_TERMS: List[str] = [
        "fast",
        "better",
        "fix it",
        "make it work",
        "clean up",
        "good",
        "optimize",
        "nice",
        "etc",
        "various",
        "stuff",
        "somehow",
        "scalable",
        "user-friendly",
        "robust",
        "seamless",
        # Arabic
        "سريع",
        "أفضل",
        "احسن",
        "جيد",
        "تحسين",
        "وغيرها",
        "وما شابه",
        "أشياء",
        "بشكل ما",
        "قابل للتوسع",
        "سهل الاستخدام",
        "متين",
        "سلس",
    ]

    CONFLICT_PAIRS = [
        (re.compile(r"\b(async|asynchronous|non-blocking)\b", re.I), re.compile(r"(?<!non-)(?<!non )(?<!non)\b(synchronous|blocking|single-threaded)\b", re.I), "Asynchronous vs Synchronous execution model"),
        (re.compile(r"\b(zero[ -]dependency|no (?:third[- ]party|external) (?:libraries|deps))\b", re.I), re.compile(r"\b(use|import|install)\s+(?:fastapi|pydantic|requests|numpy|pandas)\b", re.I), "Zero dependencies vs external libraries required"),
        (re.compile(r"\b(public|anonymous|unauthenticated)\b", re.I), re.compile(r"\b(require(?:s)? (?:auth|token|login|jwt)|strict authentication)\b", re.I), "Public anonymous access vs strict authentication"),
        (re.compile(r"\b(in-memory|stateless|no disk)\b", re.I), re.compile(r"\b(persist(?:ent)?|sqlite|database|write to disk)\b", re.I), "Stateless in-memory vs persistent storage"),
    ]

    CRITICAL_ASPECTS = {
        "error_handling": ["error", "fail", "exception", "edge", "invalid", "fallback", "retry", "catch",
                           "خطأ", "اخطاء", "فشل", "استثناء", "حالات حدية", "حاله حديه", "غير صالح", "غير صحيح", "احتياطي", "اعاده المحاوله"],
        "persistence": ["database", "store", "persist", "disk", "file", "sqlite", "postgres", "memory",
                        "قاعده بيانات", "قاعده البيانات", "تخزين", "حفظ", "ملف", "الذاكره"],
        "authentication": ["auth", "token", "jwt", "permission", "user", "role", "login", "anonymous",
                           "مصادقه", "توثيق", "صلاحي", "مستخدم", "تسجيل الدخول"],
        "performance_target": ["latency", "throughput", "concurrency", "rps", "ms", "seconds", "memory limit", "budget",
                               "زمن الاستجابه", "تاخير", "ثانيه", "ثواني", "مللي", "معدل", "تزامن"],
        "interface_contract": ["endpoint", "api", "function signature", "input", "output", "schema", "cli",
                               "واجهه", "نقطه نهايه", "مدخلات", "مخرجات", "دال"],
    }

    _ERROR_KEYWORDS = ("error", "fail", "exception", "edge", "invalid", "fallback", "handle",
                       "خطأ", "أخطاء", "فشل", "استثناء", "حالات حدية", "غير صالح", "معالجة")

    @staticmethod
    def _heuristic_note() -> str:
        return "Keyword heuristics (English and Arabic); a clean result means no pattern matched, not that the spec is complete."

    @classmethod
    def analyze_task_description(cls, task_description: Any, min_words: int = 5) -> Dict[str, Any]:
        """Inspect a task specification for ambiguity and return clarifying questions.

        Raises :class:`AmbiguityInputError` for a non-text argument (or a PDF with unread pages).
        """
        raw, read = _coerce_text(task_description)
        text = raw.strip()
        lower = _fold(text)
        ambiguities: List[str] = []
        questions: List[str] = []

        found_vague = [term for term in cls.VAGUE_TERMS if _has_term(term, lower)]
        if found_vague:
            ambiguities.append(f"Vague or subjective terms detected: {', '.join(found_vague)}.")
            questions.append(
                f"What measurable criteria or concrete behavior define '{', '.join(found_vague)}'?"
            )

        words = text.split()
        try:
            min_w = max(1, int(min_words))
        except (TypeError, ValueError, OverflowError):
            min_w = 5
        if len(words) < min_w:
            ambiguities.append("Task description is too brief to specify clear requirements.")
            questions.append("Can you provide expected inputs, outputs, and target components?")

        if not any(_has_term(kw, lower) for kw in cls._ERROR_KEYWORDS):
            ambiguities.append("No error handling or edge-case behavior specified.")
            questions.append("How should invalid inputs, failures, or edge cases be handled?")

        is_ambiguous = len(ambiguities) > 0
        result: Dict[str, Any] = {
            "is_ambiguous": is_ambiguous,
            "ambiguities_count": len(ambiguities),
            "ambiguities": ambiguities,
            "clarification_questions": questions,
            "readiness_status": "NEEDS_CLARIFICATION" if is_ambiguous else "READY_FOR_EXECUTION",
            "heuristic": True,
        }
        if read is not None:
            result["input"] = read.describe()
        return result

    @classmethod
    def analyze_document(cls, file_path: str, base_dir: Union[str, Path, None] = None) -> Dict[str, Any]:
        """Analyse a specification *file* (text or PDF, every page) for ambiguity.

        Returns :meth:`analyze_task_description`'s result plus ``score`` and ``missing``, the
        ``input`` coverage and ``warnings``. If some PDF pages are scans that could not be read, the
        verdict is ``INCOMPLETE_INPUT`` (never ``READY_FOR_EXECUTION``): ambiguity in the unread
        pages is unknown. An unreadable path returns ``{"error": ...}``.
        """
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"error": str(exc), "readiness_status": "UNREADABLE_INPUT", "is_ambiguous": True}
        except (OSError, TypeError, ValueError) as exc:
            return {"error": f"Invalid path: {type(exc).__name__}", "readiness_status": "UNREADABLE_INPUT", "is_ambiguous": True}
        result = cls.analyze_task_description(read.text)
        result["score"] = cls.score_ambiguity(read.text)
        result["missing"] = cls.check_missing_specifications(read.text)
        result["input"] = read.describe()
        result["warnings"] = read.warnings()
        if not read.complete:
            result["readiness_status"] = "INCOMPLETE_INPUT"
            result["is_ambiguous"] = True
        elif not read.text.strip():
            result["warnings"].append("The document contains no readable text.")
        return result

    @classmethod
    def detect_conflicting_requirements(cls, requirements_list: List[str]) -> List[Dict[str, Any]]:
        """Identify contradictory statements across requirement specifications (English patterns only)."""
        if isinstance(requirements_list, str):
            requirements_list = [requirements_list]
        if not isinstance(requirements_list, (list, tuple, set)):
            requirements_list = []
        parts: List[str] = []
        for r in requirements_list:
            text, _read = _coerce_text(r) if isinstance(r, (str, bytes, bytearray)) or r is None else (str(r), None)
            parts.append(text)
        joined_text = "\n".join(parts)
        conflicts: List[Dict[str, Any]] = []

        for p1, p2, desc in cls.CONFLICT_PAIRS:
            m1 = p1.search(joined_text)
            m2 = p2.search(joined_text)
            if m1 and m2:
                conflicts.append({
                    "conflict_type": desc,
                    "matched_pattern_1": m1.group(0),
                    "matched_pattern_2": m2.group(0),
                    "recommendation": f"Clarify whether the architecture requires {m1.group(0)} or {m2.group(0)}.",
                })

        return conflicts

    @classmethod
    def score_ambiguity(cls, text: Any) -> Dict[str, Any]:
        """Compute a calibrated ambiguity score from 0.0 (crystal clear) to 1.0 (highly ambiguous)."""
        raw, _read = _coerce_text(text)
        words = raw.strip().split()
        total_words = max(1, len(words))
        lower = _fold(raw)

        vague_count = sum(1 for term in cls.VAGUE_TERMS if _has_term(term, lower))
        vague_density = min(1.0, vague_count / (total_words / 15.0))

        # Check for numbers / concrete constraints
        has_numbers = bool(re.search(r"\b\d+(?:\.\d+)?(?:ms|s|gb|mb|kb|%)?\b", raw))
        concrete_penalty = 0.0 if has_numbers else 0.25

        brevity_penalty = 0.35 if total_words < 12 else (0.15 if total_words < 25 else 0.0)

        # Missing aspects penalty
        missing_aspects = cls.check_missing_specifications(raw)["missing_aspects"]
        missing_penalty = min(0.3, len(missing_aspects) * 0.08)

        score = min(1.0, vague_density * 0.35 + concrete_penalty + brevity_penalty + missing_penalty)

        return {
            "ambiguity_score": round(score, 2),
            "clarity_level": "CLEAR" if score < 0.3 else ("MODERATE" if score < 0.6 else "AMBIGUOUS"),
            "total_words": len(words),
            "vague_words_count": vague_count,
            "has_concrete_metrics": has_numbers,
        }

    @classmethod
    def check_missing_specifications(cls, text: Any) -> Dict[str, Any]:
        """Verify whether vital software engineering constraints are addressed."""
        raw, _read = _coerce_text(text)
        lower = _fold(raw)
        present: List[str] = []
        missing: List[str] = []

        for aspect, keywords in cls.CRITICAL_ASPECTS.items():
            if any(_has_term(kw, lower) for kw in keywords):
                present.append(aspect)
            else:
                missing.append(aspect)

        return {
            "complete": len(missing) == 0,
            "present_aspects": present,
            "missing_aspects": missing,
        }

    @classmethod
    def generate_clarification_questions(
        cls,
        task_description: Any,
        domain_context: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Generate structured questions with multi-choice options for user clarification.

        ``domain_context`` (optional) is searched together with the task for the aspects it already
        settles, so a question the context answers is not asked.
        """
        raw, _read = _coerce_text(task_description)
        analysis = cls.analyze_task_description(raw)
        combined = raw if not domain_context else f"{raw}\n{domain_context}"
        missing_info = cls.check_missing_specifications(combined)["missing_aspects"]
        questions: List[Dict[str, Any]] = []

        if "error_handling" in missing_info:
            questions.append({
                "question": "How should errors and edge cases be handled?",
                "options": [
                    "Raise custom domain exceptions with error codes",
                    "Return a Result/Dict object with success=False and error details",
                    "Log warning and return fallback default value",
                ],
                "is_multi_select": False,
            })

        if "interface_contract" in missing_info:
            questions.append({
                "question": "What is the expected interface/calling convention?",
                "options": [
                    "Synchronous Python class method",
                    "Asynchronous coroutine function (async def)",
                    "REST API endpoint schema",
                    "CLI command line arguments",
                ],
                "is_multi_select": False,
            })

        for q in analysis["clarification_questions"]:
            questions.append({
                "question": q,
                "options": ["Provide custom clarification in response"],
                "is_multi_select": False,
            })

        return questions[:5]
