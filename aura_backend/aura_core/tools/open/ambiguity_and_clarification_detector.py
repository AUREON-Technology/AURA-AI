"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Scores ambiguity from a
list of vague words; the model judges whether a request is ambiguous better than a word list,
so it is not worth the planner prompt it would cost.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import re
from typing import Any, Dict, List, Optional


class AmbiguityAndClarificationDetector:
    """Detector for identifying vague requirements, missing specifications, and generating structured clarification questions."""

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
    ]

    CONFLICT_PAIRS = [
        (re.compile(r"\b(async|asynchronous|non-blocking)\b", re.I), re.compile(r"\b(synchronous|blocking|single-threaded)\b", re.I), "Asynchronous vs Synchronous execution model"),
        (re.compile(r"\b(zero[ -]dependency|no (?:third[- ]party|external) (?:libraries|deps))\b", re.I), re.compile(r"\b(use|import|install)\s+(?:fastapi|pydantic|requests|numpy|pandas)\b", re.I), "Zero dependencies vs external libraries required"),
        (re.compile(r"\b(public|anonymous|unauthenticated)\b", re.I), re.compile(r"\b(require(?:s)? (?:auth|token|login|jwt)|strict authentication)\b", re.I), "Public anonymous access vs strict authentication"),
        (re.compile(r"\b(in-memory|stateless|no disk)\b", re.I), re.compile(r"\b(persist(?:ent)?|sqlite|database|write to disk)\b", re.I), "Stateless in-memory vs persistent storage"),
    ]

    CRITICAL_ASPECTS = {
        "error_handling": ["error", "fail", "exception", "edge", "invalid", "fallback", "retry", "catch"],
        "persistence": ["database", "store", "persist", "disk", "file", "sqlite", "postgres", "memory"],
        "authentication": ["auth", "token", "jwt", "permission", "user", "role", "login", "anonymous"],
        "performance_target": ["latency", "throughput", "concurrency", "rps", "ms", "seconds", "memory limit", "budget"],
        "interface_contract": ["endpoint", "api", "function signature", "input", "output", "schema", "cli"],
    }

    @classmethod
    def analyze_task_description(cls, task_description: str, min_words: int = 5) -> Dict[str, Any]:
        """Inspect a task specification for ambiguity and return clarifying questions."""
        text = task_description.strip()
        lower = text.lower()
        ambiguities: List[str] = []
        questions: List[str] = []

        found_vague = [
            term for term in cls.VAGUE_TERMS
            if re.search(rf"\b{re.escape(term)}\b", lower)
        ]
        if found_vague:
            ambiguities.append(f"Vague or subjective terms detected: {', '.join(found_vague)}.")
            questions.append(
                f"What measurable criteria or concrete behavior define '{', '.join(found_vague)}'?"
            )

        words = text.split()
        if len(words) < max(1, min_words):
            ambiguities.append("Task description is too brief to specify clear requirements.")
            questions.append("Can you provide expected inputs, outputs, and target components?")

        error_keywords = ("error", "fail", "exception", "edge", "invalid", "fallback", "handle")
        if not any(re.search(rf"\b{kw}\b", lower) for kw in error_keywords):
            ambiguities.append("No error handling or edge-case behavior specified.")
            questions.append("How should invalid inputs, failures, or edge cases be handled?")

        is_ambiguous = len(ambiguities) > 0
        return {
            "is_ambiguous": is_ambiguous,
            "ambiguities_count": len(ambiguities),
            "ambiguities": ambiguities,
            "clarification_questions": questions,
            "readiness_status": "NEEDS_CLARIFICATION" if is_ambiguous else "READY_FOR_EXECUTION",
        }

    @classmethod
    def detect_conflicting_requirements(cls, requirements_list: List[str]) -> List[Dict[str, Any]]:
        """Identify contradictory statements across requirement specifications."""
        joined_text = "\n".join(requirements_list)
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
    def score_ambiguity(cls, text: str) -> Dict[str, Any]:
        """Compute a calibrated ambiguity score from 0.0 (crystal clear) to 1.0 (highly ambiguous)."""
        words = text.strip().split()
        total_words = max(1, len(words))
        lower = text.lower()

        vague_count = sum(1 for term in cls.VAGUE_TERMS if re.search(rf"\b{re.escape(term)}\b", lower))
        vague_density = min(1.0, vague_count / (total_words / 15.0))

        # Check for numbers / concrete constraints
        has_numbers = bool(re.search(r"\b\d+(?:\.\d+)?(?:ms|s|gb|mb|kb|%)?\b", text))
        concrete_penalty = 0.0 if has_numbers else 0.25

        brevity_penalty = 0.35 if total_words < 12 else (0.15 if total_words < 25 else 0.0)

        # Missing aspects penalty
        missing_aspects = cls.check_missing_specifications(text)["missing_aspects"]
        missing_penalty = min(0.3, len(missing_aspects) * 0.08)

        score = min(1.0, vague_density * 0.35 + concrete_penalty + brevity_penalty + missing_penalty)

        return {
            "ambiguity_score": round(score, 2),
            "clarity_level": "CLEAR" if score < 0.3 else ("MODERATE" if score < 0.6 else "AMBIGUOUS"),
            "total_words": total_words,
            "vague_words_count": vague_count,
            "has_concrete_metrics": has_numbers,
        }

    @classmethod
    def check_missing_specifications(cls, text: str) -> Dict[str, Any]:
        """Verify whether vital software engineering constraints are addressed."""
        lower = text.lower()
        present: List[str] = []
        missing: List[str] = []

        for aspect, keywords in cls.CRITICAL_ASPECTS.items():
            if any(re.search(rf"\b{kw}\b", lower) for kw in keywords):
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
        task_description: str,
        domain_context: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Generate structured questions with multi-choice options for user clarification."""
        analysis = cls.analyze_task_description(task_description)
        missing_info = cls.check_missing_specifications(task_description)["missing_aspects"]
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
