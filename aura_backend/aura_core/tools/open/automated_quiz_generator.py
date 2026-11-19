import re
from typing import Any, Dict, List


class AutomatedQuizGenerator:
    """Generator for technical multiple-choice questions and coding exercises from source text."""

    @staticmethod
    def generate_quiz(
        topic: str,
        source_text: str,
        num_questions: int = 3,
    ) -> Dict[str, Any]:
        """Generate technical quiz items and a practice coding prompt based on source_text."""
        tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", source_text)
        stopwords = {"return", "class", "def", "import", "from", "true", "false", "none", "self", "dict", "list"}
        keywords: List[str] = []
        for tok in tokens:
            if tok.lower() not in stopwords and tok not in keywords:
                keywords.append(tok)
            if len(keywords) >= 12:
                break

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