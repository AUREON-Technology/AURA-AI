"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Template output: every
generated question has the same options and ``correct_answer`` ``"A"``. Not useful enough to
expose.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import ast
import re
from typing import Any, Dict, List, Optional


class AutomatedQuizGenerator:
    """Generator for technical multiple-choice questions, coding exercises, and comprehension assessments."""

    @staticmethod
    def extract_key_concepts(source_text: str) -> List[str]:
        """Extract significant architectural concepts, function names, and domain terms from text/code."""
        stopwords = {
            "return", "class", "def", "import", "from", "true", "false", "none",
            "self", "dict", "list", "str", "int", "bool", "any", "optional",
            "args", "kwargs", "pass", "raise", "except", "try", "finally", "while",
            "for", "with", "async", "await", "break", "continue"
        }
        tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", source_text)
        concepts: List[str] = []
        for tok in tokens:
            if tok.lower() not in stopwords and tok not in concepts:
                concepts.append(tok)
            if len(concepts) >= 20:
                break
        return concepts

    @classmethod
    def generate_quiz(
        cls,
        topic: str,
        source_text: str,
        num_questions: int = 3,
    ) -> Dict[str, Any]:
        """Generate technical quiz items and a practice coding prompt based on source_text."""
        keywords = cls.extract_key_concepts(source_text)
        count = max(1, min(num_questions, len(keywords))) if keywords else 1
        questions: List[Dict[str, Any]] = []

        for idx in range(count):
            term = keywords[idx] if idx < len(keywords) else topic
            questions.append({
                "id": idx + 1,
                "question": f"How does '{term}' contribute to '{topic}'?",
                "options": [
                    f"A) Implements core logic or state handling for {term}",
                    "B) Acts purely as an unused placeholder comment",
                    "C) Bypasses validation and error handling",
                    "D) Replaces all runtime dependencies",
                ],
                "correct_answer": "A",
                "explanation": f"'{term}' defines functional behavior, interfaces, or constraints within {topic}.",
            })

        return {
            "topic": topic,
            "questions_count": len(questions),
            "questions": questions,
            "coding_exercise": {
                "title": f"Unit & edge-case tests for {topic}",
                "prompt": f"Write tests verifying normal execution, invalid inputs, and edge cases for {topic}.",
            },
        }

    @staticmethod
    def generate_coding_challenge(
        topic: str,
        function_signature: Optional[str] = None,
        difficulty: str = "medium",
    ) -> Dict[str, Any]:
        """Generate a structured coding exercise with starter code, requirements, and test assertions."""
        clean_topic = re.sub(r"[^a-zA-Z0-9_]", "_", topic).lower().strip("_")
        sig = function_signature or f"def solve_{clean_topic}(payload: dict) -> dict:"

        starter_code = f"""{sig}
    \"\"\"
    Solve the {topic} requirement according to system specifications.
    \"\"\"
    # TODO: Implement your solution here
    raise NotImplementedError("Implement this function")
"""

        test_template = f"""def test_solve_{clean_topic}_basic():
    # Basic positive test case
    result = solve_{clean_topic}({{"valid": True}})
    assert result is not None

def test_solve_{clean_topic}_edge_cases():
    # Edge case with empty or invalid payload
    import pytest
    with pytest.raises((ValueError, KeyError, TypeError)):
        solve_{clean_topic}({{}})
"""

        return {
            "title": f"Coding Challenge: {topic}",
            "difficulty": difficulty,
            "function_signature": sig,
            "starter_code": starter_code,
            "test_template": test_template,
            "hints": [
                "Validate input types before processing.",
                "Ensure defensive handling for empty or None attributes.",
                "Assert return values match expected output contracts.",
            ],
        }

    @staticmethod
    def evaluate_quiz_answers(
        user_answers: Dict[int, str],
        answer_key: Dict[int, str],
    ) -> Dict[str, Any]:
        """Grade submitted answers against expected answers and compute diagnostic feedback."""
        total = len(answer_key)
        if total == 0:
            return {"score": 0.0, "total": 0, "correct": 0, "passed": False, "breakdown": {}}

        correct = 0
        breakdown: Dict[int, Dict[str, Any]] = {}

        for q_id, expected in answer_key.items():
            user_val = str(user_answers.get(q_id, "")).strip().upper()
            exp_val = str(expected).strip().upper()
            is_right = (user_val == exp_val)
            if is_right:
                correct += 1
            breakdown[q_id] = {
                "submitted": user_val,
                "expected": exp_val,
                "is_correct": is_right,
            }

        pct = round((correct / total) * 100.0, 2)
        return {
            "score_percentage": pct,
            "total_questions": total,
            "correct_answers": correct,
            "incorrect_answers": total - correct,
            "passed": pct >= 70.0,
            "breakdown": breakdown,
        }

    @classmethod
    def generate_comprehension_assessment(
        cls,
        topic: str,
        source_text: str,
    ) -> Dict[str, Any]:
        """Generate True/False and short-answer comprehension rubric."""
        concepts = cls.extract_key_concepts(source_text)[:4]
        tf_items = []
        for idx, c in enumerate(concepts, start=1):
            tf_items.append({
                "item_id": idx,
                "statement": f"'{c}' is an active component integrated directly into {topic}.",
                "expected": True,
            })

        return {
            "topic": topic,
            "true_false_questions": tf_items,
            "rubric": {
                "criteria": [
                    "Correct identification of core components (40%)",
                    "Understanding of error-handling boundaries (30%)",
                    "Accurate explanation of module interactions (30%)",
                ],
                "passing_score": 75.0,
            },
        }
