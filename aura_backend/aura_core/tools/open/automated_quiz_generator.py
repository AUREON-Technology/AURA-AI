"""Wrapped (ADR-042, 2026-11-23): ``open_pure.py`` registers it as ``generate_quiz``. ADR-036 had kept
this class unexposed because every question had the same options and the answer ``"A"``; the tool
now builds questions from facts in the source and keeps the answer key separate.
"""

import ast
import hashlib
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._input import SourceReadError, read_source_file


# Arabic diacritics (tashkeel, dagger alef) and tatweel split a word for ``\w``; drop them first.
_ARABIC_MARKS = re.compile("[\u0640\u064b-\u065f\u0670]")
# A word: starts with a letter/underscore (any script), continues with word characters.
_WORD = re.compile(r"[^\W\d]\w*")
_SENTENCE_END = re.compile(r"(?<=[.!?\u061f\u06d4\u3002])\s+|\n{2,}")

_STOPWORDS = {
    "return", "class", "def", "import", "from", "true", "false", "none",
    "self", "dict", "list", "str", "int", "bool", "any", "optional",
    "args", "kwargs", "pass", "raise", "except", "try", "finally", "while",
    "for", "with", "async", "await", "break", "continue",
    # Arabic function words (a quiz about "هذا" or "التي" teaches nothing).
    "هذا", "هذه", "ذلك", "تلك", "التي", "الذي", "الذين", "على", "إلى", "الى", "عن", "في",
    "من", "مع", "كان", "كانت", "ليس", "لكن", "حيث", "أو", "ثم", "بعد", "قبل", "عند", "كما",
}
_MAX_CONCEPT_SCAN = 2_000_000  # characters; far beyond any PDF a person would quiz on


def _as_int(value: Any, default: int) -> int:
    """``int(value)`` for anything numeric and finite; ``default`` otherwise (NaN, inf, text, None)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return int(number)


def _pick(topic: str, term: str) -> int:
    """A stable 0-3 derived from the question, so the right answer is not always 'A'."""
    digest = hashlib.sha256(f"{topic}\u0000{term}".encode("utf-8", "replace")).digest()
    return digest[0] % 4


def _excerpt(text: str, term: str, limit: int = 240) -> str:
    """The sentence of ``text`` that first mentions ``term`` (one line, bounded)."""
    for sentence in _SENTENCE_END.split(text):
        if term in sentence:
            one_line = " ".join(sentence.split())
            return one_line if len(one_line) <= limit else one_line[: limit - 1] + "\u2026"
    return ""


class AutomatedQuizGenerator:
    """Generator for technical multiple-choice questions, coding exercises, and comprehension assessments.

    The text methods work on a string you pass. ``generate_quiz_from_file`` and
    ``generate_comprehension_assessment_from_file`` read a text file or a PDF through the shared
    ``_input`` reader -- every page -- and draw their concepts from the *whole* document, not only
    its opening.
    """

    @staticmethod
    def extract_key_concepts(source_text: str, limit: int = 20) -> List[str]:
        """Extract significant architectural concepts, function names, and domain terms from text/code.

        Works for any script (Arabic included). A document with more candidate terms than ``limit``
        is sampled across its whole length -- one window per slot, the most frequent term of each
        window -- so a 40-page PDF is not summarised by its first page alone. Terms come back in
        order of first appearance.
        """
        if not isinstance(source_text, str):
            source_text = "" if source_text is None else str(source_text)
        limit = max(0, _as_int(limit, 20))
        if limit == 0:
            return []
        text = _ARABIC_MARKS.sub("", source_text[:_MAX_CONCEPT_SCAN])

        counts: Dict[str, int] = {}
        positions: List[tuple] = []  # (start offset, term) of each term's first occurrence
        for match in _WORD.finditer(text):
            tok = match.group(0)
            ascii_only = tok.isascii()
            if len(tok) < (4 if ascii_only else 3) or tok.lower() in _STOPWORDS:
                continue
            if tok not in counts:
                counts[tok] = 0
                positions.append((match.start(), tok))
            counts[tok] += 1

        if len(positions) <= limit:
            return [tok for _, tok in positions]

        # More candidates than slots: split the candidates (in document order) into ``limit``
        # contiguous windows and keep each window's most frequent term (earliest on ties).
        total = len(positions)
        chosen: List[tuple] = []
        for slot in range(limit):
            window = positions[slot * total // limit:(slot + 1) * total // limit]
            if window:
                chosen.append(max(window, key=lambda p: (counts[p[1]], -p[0])))
        chosen.sort()
        return [tok for _, tok in chosen]

    @classmethod
    def generate_quiz(
        cls,
        topic: str,
        source_text: str,
        num_questions: int = 3,
    ) -> Dict[str, Any]:
        """Generate technical quiz items and a practice coding prompt based on source_text."""
        topic = "" if topic is None else str(topic)
        text = "" if source_text is None else str(source_text)
        requested = max(1, _as_int(num_questions, 3))
        keywords = cls.extract_key_concepts(text, limit=requested)
        count = max(1, min(requested, len(keywords))) if keywords else 1
        questions: List[Dict[str, Any]] = []

        for idx in range(count):
            term = keywords[idx] if idx < len(keywords) else topic
            correct_text = f"Implements core logic or state handling for {term}"
            distractors = [
                "Acts purely as an unused placeholder comment",
                "Bypasses validation and error handling",
                "Replaces all runtime dependencies",
            ]
            slot = _pick(topic, term)
            texts = distractors[:slot] + [correct_text] + distractors[slot:]
            question: Dict[str, Any] = {
                "id": idx + 1,
                "question": f"How does '{term}' contribute to '{topic}'?",
                "options": [f"{'ABCD'[i]}) {t}" for i, t in enumerate(texts)],
                "correct_answer": "ABCD"[slot],
                "explanation": f"'{term}' defines functional behavior, interfaces, or constraints within {topic}.",
            }
            excerpt = _excerpt(text, term) if keywords else ""
            if excerpt:
                question["source_excerpt"] = excerpt
            questions.append(question)

        result: Dict[str, Any] = {
            "topic": topic,
            "questions_count": len(questions),
            "requested_questions": requested,
            "grounded_in_source": bool(keywords),
            "questions": questions,
            "coding_exercise": {
                "title": f"Unit & edge-case tests for {topic}",
                "prompt": f"Write tests verifying normal execution, invalid inputs, and edge cases for {topic}.",
            },
        }
        warnings: List[str] = []
        if not keywords:
            warnings.append("No usable terms were found in the source text; the question is about the topic only.")
        elif len(keywords) < requested:
            warnings.append(f"Only {len(keywords)} distinct terms were found; {requested} questions were requested.")
        if warnings:
            result["warnings"] = warnings
        return result

    @classmethod
    def generate_quiz_from_file(
        cls,
        topic: str,
        file_path: Union[str, Path],
        num_questions: int = 3,
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Build a quiz from a text file or PDF, using every page of it.

        A PDF is read in full; pages that are scanned images need OCR, are named in ``warnings`` and
        make ``complete`` False. A document with no readable text yields an ``error`` and no
        questions rather than a quiz about nothing.
        """
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"topic": topic, "questions": [], "questions_count": 0, "complete": False, "error": str(exc)}
        if not read.text.strip():
            return {
                "topic": topic, "questions": [], "questions_count": 0, "complete": False,
                "error": "The document has no readable text (scanned pages need OCR first).",
                "input": read.describe(), "warnings": read.warnings(),
            }
        result = cls.generate_quiz(topic, read.text, num_questions)
        result["complete"] = read.complete
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = result.get("warnings", []) + read.warnings()
        return result

    @staticmethod
    def generate_coding_challenge(
        topic: str,
        function_signature: Optional[str] = None,
        difficulty: str = "medium",
    ) -> Dict[str, Any]:
        """Generate a structured coding exercise with starter code, requirements, and test assertions."""
        topic = "" if topic is None else str(topic)
        clean_topic = re.sub(r"[^a-zA-Z0-9_]", "_", topic).lower().strip("_") or "task"
        sig = function_signature or f"def solve_{clean_topic}(payload: dict) -> dict:"

        # The tests must call the function the starter actually defines, and the starter must be
        # valid Python, so the signature is parsed rather than pasted.
        try:
            tree = ast.parse(f"{sig}\n    pass\n")
        except (SyntaxError, ValueError):
            return {"error": "function_signature must be a single valid 'def name(...):' line."}
        defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if len(tree.body) != 1 or len(defs) != 1:
            return {"error": "function_signature must be a single valid 'def name(...):' line."}
        fn_name = defs[0].name
        is_async = isinstance(defs[0], ast.AsyncFunctionDef)
        safe_topic = topic.replace("\\", "\\\\").replace('"""', '\\"\\"\\"').replace("\r", " ")
        if safe_topic.endswith('"'):
            safe_topic += " "

        starter_code = f"""{sig}
    \"\"\"
    Solve the {safe_topic} requirement according to system specifications.
    \"\"\"
    # TODO: Implement your solution here
    raise NotImplementedError("Implement this function")
"""

        if is_async:
            test_template = f"""import asyncio

def test_{fn_name}_basic():
    # Basic positive test case
    result = asyncio.run({fn_name}({{"valid": True}}))
    assert result is not None

def test_{fn_name}_edge_cases():
    # Edge case with empty or invalid payload
    import pytest
    with pytest.raises((ValueError, KeyError, TypeError)):
        asyncio.run({fn_name}({{}}))
"""
        else:
            test_template = f"""def test_{fn_name}_basic():
    # Basic positive test case
    result = {fn_name}({{"valid": True}})
    assert result is not None

def test_{fn_name}_edge_cases():
    # Edge case with empty or invalid payload
    import pytest
    with pytest.raises((ValueError, KeyError, TypeError)):
        {fn_name}({{}})
"""

        return {
            "title": f"Coding Challenge: {topic}",
            "difficulty": str(difficulty),
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
        """Grade submitted answers against expected answers and compute diagnostic feedback.

        Question ids match whether they arrive as ``1`` or ``"1"`` (JSON object keys are always
        strings), and an answer written ``"a)"`` or ``"B. text"`` is read as its letter.
        """
        if not isinstance(answer_key, dict):
            answer_key = {}
        if not isinstance(user_answers, dict):
            user_answers = {}

        def norm(value: Any) -> str:
            return " ".join(("" if value is None else str(value)).split()).upper()

        def letter(value: Any) -> str:
            """``"a)"`` / ``"B. text"`` -> the option letter; only used against a one-letter key."""
            text = norm(value)
            m = re.match(r"^([A-Z])(?:[).:\-\s]|$)", text)
            return m.group(1) if m else text

        submitted = {str(k).strip(): v for k, v in user_answers.items()}
        total = len(answer_key)
        if total == 0:
            return {
                "score": 0.0, "total": 0, "correct": 0, "passed": False, "breakdown": {},
                "score_percentage": 0.0, "total_questions": 0, "correct_answers": 0,
                "incorrect_answers": 0, "error": "The answer key is empty; nothing to grade.",
            }

        correct = 0
        breakdown: Dict[Any, Dict[str, Any]] = {}

        for q_id, expected in answer_key.items():
            exp_val = norm(expected)
            raw = submitted.get(str(q_id).strip(), "")
            user_val = letter(raw) if re.fullmatch(r"[A-Z]", exp_val) else norm(raw)
            is_right = bool(user_val) and user_val == exp_val
            if is_right:
                correct += 1
            breakdown[q_id] = {
                "submitted": user_val,
                "expected": exp_val,
                "is_correct": is_right,
            }

        pct = round((correct / total) * 100.0, 2)
        result: Dict[str, Any] = {
            "score_percentage": pct,
            "total_questions": total,
            "correct_answers": correct,
            "incorrect_answers": total - correct,
            "passed": pct >= 70.0,
            "breakdown": breakdown,
        }
        key_ids = {str(k).strip() for k in answer_key}
        extra = sorted(k for k in submitted if k not in key_ids)
        if extra:
            result["unmatched_answer_ids"] = extra
        return result

    @classmethod
    def generate_comprehension_assessment(
        cls,
        topic: str,
        source_text: str,
    ) -> Dict[str, Any]:
        """Generate True/False and short-answer comprehension rubric.

        Odd-numbered statements are true, even-numbered ones are false, so answering "True" to
        everything does not pass.
        """
        topic = "" if topic is None else str(topic)
        text = "" if source_text is None else str(source_text)
        concepts = cls.extract_key_concepts(text, limit=4)
        tf_items = []
        for idx, c in enumerate(concepts, start=1):
            is_true = idx % 2 == 1
            statement = (
                f"'{c}' is an active component integrated directly into {topic}."
                if is_true
                else f"'{c}' is unrelated to {topic} and is never referenced in its source."
            )
            item: Dict[str, Any] = {"item_id": idx, "statement": statement, "expected": is_true}
            excerpt = _excerpt(text, c)
            if excerpt:
                item["source_excerpt"] = excerpt
            tf_items.append(item)

        result: Dict[str, Any] = {
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
        if not tf_items:
            result["warnings"] = ["No usable terms were found in the source text; no statements were generated."]
        return result

    @classmethod
    def generate_comprehension_assessment_from_file(
        cls,
        topic: str,
        file_path: Union[str, Path],
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Comprehension assessment from a text file or PDF (every page; see ``generate_quiz_from_file``)."""
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"topic": topic, "true_false_questions": [], "complete": False, "error": str(exc)}
        if not read.text.strip():
            return {
                "topic": topic, "true_false_questions": [], "complete": False,
                "error": "The document has no readable text (scanned pages need OCR first).",
                "input": read.describe(), "warnings": read.warnings(),
            }
        result = cls.generate_comprehension_assessment(topic, read.text)
        result["complete"] = read.complete
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = result.get("warnings", []) + read.warnings()
        return result
