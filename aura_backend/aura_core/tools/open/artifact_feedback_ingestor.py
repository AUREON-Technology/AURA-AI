"""Classify user feedback and apply it to plan lists and Markdown artifacts.

Text in, dict out. A ``bytes`` document (``patch_markdown_artifact``, ``generate_feedback_diff``) is
read through ``_input``: a PDF is read in full, every page. A PDF can be *diffed* (its extracted text
is compared) but not *patched* -- patching a Markdown section of a PDF is meaningless, so that is
refused with an explicit error.

Review notes -- bugs fixed here:

* Intent keywords were substring tests: "address" matched ``add``, "disapprove" matched ``approve``
  and "I don't approve" was classified APPROVE. Matching is now on whole words, with negation.
* ``apply_feedback`` deleted every plan step that contained *any* word longer than three letters from
  the feedback ("remove the testing step please" removed every step containing "please"). Stop-words
  are ignored and "remove step 3" removes step 3.
* ``patch_markdown_artifact`` matched ``## Test`` against ``## Test-plan``, treated ``# comment``
  lines inside code fences as headings, dropped the document's trailing newline, and accepted an
  unknown ``mode``.
* ``generate_feedback_diff`` produced a garbled diff when a file did not end with a newline, and
  never counted an added line whose text began with ``++``.
* Arabic feedback was always ``GENERAL_COMMENT``.
"""

import difflib
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from ._input import SourceRead, SourceReadError, read_source_bytes

_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟـ]")
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "؟": "?"})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return _TASHKEEL.sub("", text).translate(_ALEF)


_APPROVE = ("approve", "approved", "looks good", "lgtm", "proceed", "accepted", "perfect", "go ahead",
            "موافق", "اوافق", "تمام", "ممتاز", "اعتمد", "اعتماد")
_REJECT = ("reject", "rejected", "cancel", "stop", "abort", "wrong", "broken", "unacceptable", "disapprove",
           "disapproved", "مرفوض", "ارفض", "الغاء", "الغ", "توقف", "خطا", "غلط")
_REMOVE = ("remove", "delete", "drop", "omit", "exclude", "skip", "احذف", "حذف", "ازاله", "ازل", "شيل", "استبعد")
_ADD = ("add", "include", "also", "insert", "need to", "اضف", "اضافه", "ضيف", "ادرج", "ايضا")
_MODIFY = ("change", "modify", "update", "instead", "replace", "refactor", "عدل", "تعديل", "غير", "استبدل", "بدلا")
_QUESTION = ("why", "what", "how", "explain", "clarify", "?", "لماذا", "ليش", "كيف", "ماذا", "وضح")
_NEGATION = re.compile(r"(?:\b(?:not|no|never|don'?t|do not|didn'?t|cannot|can'?t|won'?t|isn'?t|aren'?t)\b|\bلا\b|\bلم\b|\bغير\b)\s+(?:\w+\s+){0,2}$")
_STOP = {
    "the", "this", "that", "these", "those", "with", "from", "into", "onto", "your", "please", "also", "step", "steps",
    "task", "tasks", "item", "items", "plan", "about", "should", "would", "could", "need", "needs", "have", "will",
    "just", "then", "than", "them", "they", "make", "sure", "all", "and", "for", "remove", "delete", "drop", "skip",
    "everything", "related", "stuff", "more", "some", "any", "can", "you", "its", "our", "not",
}
_MAX_STEPS = 10_000


def _has(word: str, folded: str) -> Optional["re.Match[str]"]:
    if word == "?":
        return re.search(r"\?", folded)
    if re.search(r"[؀-ۿ]", word):
        return re.search(re.escape(word), folded)  # Arabic clitics attach (وال, بال): substring
    return re.search(rf"\b{re.escape(word)}\b", folded)


def _matches_any(words: Tuple[str, ...], folded: str) -> bool:
    return any(_has(w, folded) for w in words)


def _negated(word_set: Tuple[str, ...], folded: str) -> bool:
    """True when every occurrence of the words is preceded by a negation within two words."""
    found = False
    for w in word_set:
        for m in re.finditer(r"\b" + re.escape(w) + r"\b" if w.isascii() else re.escape(w), folded):
            found = True
            if not _NEGATION.search(folded[: m.start()]):
                return False
    return found


def _steps(plan: Any) -> List[str]:
    if plan is None:
        return []
    if isinstance(plan, str):
        return [ln.strip() for ln in plan.splitlines() if ln.strip()]
    if isinstance(plan, dict):
        return [str(plan)]
    try:
        return [x if isinstance(x, str) else str(x) for x in list(plan)[:_MAX_STEPS]]
    except TypeError:
        return [str(plan)]


def _text_arg(value: Any, name: str) -> Tuple[str, Optional[SourceRead]]:
    """str/None/bytes -> text. A PDF is read in full; the SourceRead carries its coverage."""
    if value is None:
        return "", None
    if isinstance(value, str):
        return value, None
    if isinstance(value, (bytes, bytearray)):
        try:
            read = read_source_bytes(bytes(value), name=name)
        except SourceReadError as exc:
            raise ValueError(str(exc)) from exc
        return read.text, read
    raise ValueError(f"{name} must be text, got {type(value).__name__}.")


def _unified(a: str, b: str, fromfile: str, tofile: str, n: int = 2) -> List[str]:
    out: List[str] = []
    for line in difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True), fromfile=fromfile, tofile=tofile, n=n
    ):
        out.append(line)
        if not line.endswith("\n"):
            out.append("\n\\ No newline at end of file\n")
    return out


class ArtifactFeedbackIngestor:
    """Ingestor for processing, classifying, and applying user feedback to plans and task artifacts."""

    @staticmethod
    def classify_feedback_intent(feedback_text: str) -> Dict[str, Any]:
        """Classify user feedback into actionable intent categories with urgency level (English/Arabic).

        Approval only counts when nothing asks for a change and it is not negated ("don't approve").
        Empty feedback is ``GENERAL_COMMENT`` with a warning, never an approval.
        """
        raw = feedback_text if isinstance(feedback_text, str) else ("" if feedback_text is None else str(feedback_text))
        folded = _fold(raw.strip())

        intent = "GENERAL_COMMENT"
        urgency = "LOW"

        approve = _matches_any(_APPROVE, folded) and not _negated(_APPROVE, folded) and not _has("disapprove", folded)
        reject = _matches_any(_REJECT, folded) and not _negated(_REJECT, folded)
        # "not approved" / "don't approve" is a rejection of the plan, not a comment.
        if _matches_any(_APPROVE, folded) and _negated(_APPROVE, folded):
            reject = True

        if reject:
            intent, urgency = "REJECT", "HIGH"
        elif _matches_any(_REMOVE, folded):
            intent, urgency = "REMOVE_STEP", "MEDIUM"
        elif _matches_any(_ADD, folded):
            intent, urgency = "ADD_STEP", "MEDIUM"
        elif _matches_any(_MODIFY, folded):
            intent, urgency = "MODIFY_STEP", "MEDIUM"
        elif approve:
            intent, urgency = "APPROVE", "LOW"
        elif _matches_any(_QUESTION, folded):
            intent, urgency = "CLARIFICATION_QUESTION", "LOW"

        result: Dict[str, Any] = {
            "intent": intent,
            "urgency": urgency,
            "raw_feedback": feedback_text,
            "requires_plan_update": intent in ("REMOVE_STEP", "ADD_STEP", "MODIFY_STEP", "REJECT"),
        }
        if not folded:
            result["warnings"] = ["The feedback is empty."]
        return result

    @staticmethod
    def apply_feedback(current_plan: List[str], user_feedback: str) -> Dict[str, Any]:
        """Update a plan list based on natural-language feedback instructions.

        Handles "remove step N", "remove <words>" (whole words, stop-words ignored, only steps that
        contain *all* the distinctive words go) and "add ...". Anything else is recorded without
        changing the plan.
        """
        original = _steps(current_plan)
        revised = list(original)
        actions: List[str] = []
        feedback = user_feedback if isinstance(user_feedback, str) else ("" if user_feedback is None else str(user_feedback))
        lower = _fold(feedback.strip())

        if _matches_any(("remove", "delete", "drop", "skip", "احذف", "حذف", "ازل", "ازاله", "شيل", "استبعد"), lower):
            m = re.search(r"\b(?:step|task|item)\s*#?\s*(\d+)\b|(?:خطوه|الخطوه)\s*(\d+)", lower)
            if m:
                n = int(m.group(1) or m.group(2))
                if 1 <= n <= len(revised):
                    actions.append(f"Removed step {n}: '{revised[n - 1]}'")
                    del revised[n - 1]
                else:
                    actions.append(f"Step {n} does not exist (the plan has {len(revised)} steps); nothing removed.")
            else:
                keywords = [
                    w for w in dict.fromkeys(re.findall(r"\w+", lower))
                    if len(w) > 3 and w not in _STOP and not _has(w, " remove delete drop skip ")
                ]
                kept: List[str] = []
                for step in revised:
                    step_words = set(re.findall(r"\w+", _fold(step)))
                    if keywords and all(kw in step_words for kw in keywords):
                        actions.append(f"Removed step: '{step}'")
                    else:
                        kept.append(step)
                if keywords and len(kept) == len(revised):
                    actions.append(f"No step contained all of: {', '.join(keywords)}; nothing removed.")
                revised = kept

        if _matches_any(("add", "include", "also", "اضف", "ضيف", "ادرج"), lower) and not _matches_any(
            ("remove", "delete", "drop", "skip", "احذف", "حذف", "ازل"), lower
        ):
            new_item = feedback.strip()
            revised.append(new_item)
            actions.append(f"Added step: '{new_item}'")

        if not any(a.startswith(("Removed", "Added")) for a in actions):
            actions.append("Recorded feedback without structural plan changes.")

        return {
            "original_steps_count": len(original),
            "revised_steps_count": len(revised),
            "actions": actions,
            "revised_plan": revised,
        }

    @staticmethod
    def patch_markdown_artifact(
        markdown_text: str,
        section_name: str,
        new_content: str,
        mode: str = "replace",
    ) -> Dict[str, Any]:
        """Patch a specific section in a Markdown artifact document based on user critique.

        The section is the heading whose title equals ``section_name`` (case-insensitive), else the
        first whose title starts with it. Headings inside code fences are ignored. A missing section
        is appended. ``mode`` is ``"replace"`` or ``"append"``. Errors come back as
        ``{"patched": False, "error": ...}``.
        """
        def fail(msg: str) -> Dict[str, Any]:
            return {"patched": False, "error": msg, "section": section_name, "content": markdown_text if isinstance(markdown_text, str) else ""}

        try:
            text, read = _text_arg(markdown_text, "markdown_text")
        except ValueError as exc:
            return fail(str(exc))
        if read is not None and read.kind == "pdf":
            return fail(
                f"The artifact is a PDF ({read.pages} page(s), read in full) - a PDF cannot be patched as "
                "Markdown. Patch the source Markdown instead."
            )
        if mode not in ("replace", "append"):
            return fail(f"mode must be 'replace' or 'append', got {mode!r}.")
        name = " ".join(str(section_name or "").split())
        if not name:
            return fail("section_name is empty.")
        content = "" if new_content is None else str(new_content)

        lines = text.splitlines()
        trailing_newline = text.endswith("\n")

        # Headings outside code fences only.
        headings: List[Tuple[int, int, str]] = []
        fence: Optional[str] = None
        for i, line in enumerate(lines):
            fm = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if fm:
                marker = fm.group(1)[0]
                if fence is None:
                    fence = marker
                elif fence == marker:
                    fence = None
                continue
            if fence is not None:
                continue
            hm = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
            if hm:
                headings.append((i, len(hm.group(1)), hm.group(2)))

        target = None
        for h in headings:
            if h[2].strip().lower() == name.lower():
                target = h
                break
        if target is None:
            pat = re.compile(rf"^{re.escape(name)}(?!\w)", re.IGNORECASE)
            for h in headings:
                if pat.match(h[2].strip()):
                    target = h
                    break

        if target is None:
            patched = text.rstrip() + f"\n\n## {name}\n{content}\n"
            return {"patched": True, "action": "appended_new_section", "section": section_name, "content": patched}

        start_idx, level, _title = target
        end_idx = len(lines)
        for idx, lvl, _t in headings:
            if idx > start_idx and lvl <= level:
                end_idx = idx
                break

        header_line = lines[start_idx]
        if mode == "append":
            existing = lines[start_idx + 1 : end_idx]
            while existing and not existing[-1].strip():
                existing.pop()
            replacement = [header_line] + existing + ["", content]
        else:
            replacement = [header_line, content]
        if end_idx < len(lines):
            replacement.append("")  # keep a blank line before the next heading

        patched_text = "\n".join(lines[:start_idx] + replacement + lines[end_idx:])
        if trailing_newline and not patched_text.endswith("\n"):
            patched_text += "\n"
        return {
            "patched": True,
            "action": f"{mode}d_section",
            "section": section_name,
            "content": patched_text,
        }

    @staticmethod
    def generate_feedback_diff(original_text: str, revised_text: str) -> Dict[str, Any]:
        """Compute unified line-by-line diff comparing original vs feedback-revised artifact.

        Either side may be ``bytes`` (a PDF is read in full and its text compared); ``input`` then
        reports each side's page coverage and, if a page could not be read, ``complete`` is false and
        the diff covers only the readable pages.
        """
        try:
            a, ra = _text_arg(original_text, "original_text")
            b, rb = _text_arg(revised_text, "revised_text")
        except ValueError as exc:
            return {"has_changes": False, "lines_added": 0, "lines_deleted": 0, "unified_diff": "", "error": str(exc)}
        diff = _unified(a, b, "original_artifact.md", "revised_artifact.md")
        body = diff[2:] if len(diff) >= 2 else []
        additions = sum(1 for ln in body if ln.startswith("+"))
        deletions = sum(1 for ln in body if ln.startswith("-"))

        result: Dict[str, Any] = {
            "has_changes": len(diff) > 0,
            "lines_added": additions,
            "lines_deleted": deletions,
            "unified_diff": "".join(diff),
        }
        reads = [r for r in (ra, rb) if r is not None]
        if reads:
            result["input"] = {"original": ra.describe() if ra else None, "revised": rb.describe() if rb else None}
            result["complete"] = all(r.complete for r in reads)
            if not result["complete"]:
                result["warnings"] = [w for r in reads for w in r.warnings()] + [
                    "Unread pages could differ: this diff does not prove the unread parts are unchanged."
                ]
        return result

    @classmethod
    def resolve_plan_conflicts(
        cls,
        current_plan: List[str],
        feedback_batch: List[str],
    ) -> Dict[str, Any]:
        """Sequentially apply a batch of feedback comments while preventing duplicate steps."""
        initial = _steps(current_plan)
        working = list(initial)
        all_actions: List[str] = []
        batch = [feedback_batch] if isinstance(feedback_batch, str) else list(feedback_batch or [])

        for feedback in batch:
            res = cls.apply_feedback(working, feedback)
            working = res["revised_plan"]
            all_actions.extend(res["actions"])

        # Deduplicate while preserving order
        seen = set()
        deduped: List[str] = []
        for step in working:
            if step not in seen:
                seen.add(step)
                deduped.append(step)

        return {
            "initial_step_count": len(initial),
            "final_step_count": len(deduped),
            "actions_applied": all_actions,
            "resolved_plan": deduped,
        }
