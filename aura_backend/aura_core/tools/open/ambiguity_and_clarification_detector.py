import re
from typing import Any, Dict, List


class AmbiguityAndClarificationDetector:
    """Detector for identifying vague requirements, missing constraints, and clarification prompts."""

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
    ]

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