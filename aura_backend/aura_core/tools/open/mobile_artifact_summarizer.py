import math
import re
from typing import Any, Dict, List, Optional

from .scope_and_diff_guard import DiffInputError, ScopeAndDiffGuard


def _count(value: Any) -> int:
    """A test count from a caller-supplied value. Anything unusable is 0, never an exception."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value) if math.isfinite(value) and value >= 0 else 0
    if isinstance(value, str):
        m = re.match(r"\s*(\d+)", value)
        return int(m.group(1)) if m else 0
    return 0


def _clip(text: str, limit: int) -> str:
    """Cut to at most ``limit`` characters, with an ellipsis when there is room for one."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _as_limit(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return max(0, int(value))


class MobileArtifactSummarizer:
    """Summarizer for condensing engineering plans, git diffs, CI test suites, and audit logs into compact mobile cards.

    Text only. A ``bytes`` diff is read through ``_input``: a PDF is read in full and then refused
    with an error, since a PDF is not a diff.
    """

    @staticmethod
    def count_diff_lines(diff_text: Any) -> Dict[str, int]:
        """Count added and removed lines in a unified diff string.

        Lines are classified by hunk position, so a removed ``-- comment`` (shown as ``--- comment``)
        or an added ``++i;`` is counted, while the ``---``/``+++`` file headers are not.
        Raises ``DiffInputError`` for non-text input.
        """
        files = ScopeAndDiffGuard.parse_diff_files(diff_text)
        return {
            "added": sum(f["lines_added"] for f in files),
            "removed": sum(f["lines_deleted"] for f in files),
        }

    @classmethod
    def summarize(
        cls,
        task_summary: Optional[str] = None,
        diff_text: Optional[str] = None,
        test_results: Optional[Dict[str, Any]] = None,
        max_chars: int = 600,
    ) -> Dict[str, Any]:
        """Build a concise mobile-friendly summary within max_chars."""
        cards: List[str] = []
        warnings: List[str] = []
        limit = _as_limit(max_chars, 600)

        if task_summary:
            clean_task = " ".join(str(task_summary).strip().split())
            if len(clean_task) > 120:
                clean_task = clean_task[:117] + "..."
            cards.append(f"Task: {clean_task}")

        if diff_text:
            try:
                counts = cls.count_diff_lines(diff_text)
                cards.append(f"Diff: +{counts['added']} / -{counts['removed']} lines")
            except DiffInputError as exc:
                warnings.append(str(exc))

        if test_results and isinstance(test_results, dict):
            passed = _count(test_results.get("passed", 0))
            failed = _count(test_results.get("failed", 0))
            errors = _count(test_results.get("errors", 0))
            line = f"Tests: {passed} passed, {failed} failed"
            if errors:
                line += f", {errors} errors"
            cards.append(line)
        elif test_results:
            warnings.append("test_results was not a dict and was ignored; use summarize_test_results for text.")

        summary_text = _clip("\n".join(cards), limit)

        result: Dict[str, Any] = {
            "mobile_ready": True,
            "summary_text": summary_text,
            "character_count": len(summary_text),
            "cards_count": len(cards),
        }
        if warnings:
            result["warnings"] = warnings
        if not cards:
            result["warnings"] = result.get("warnings", []) + ["Nothing was summarised: no usable input was given."]
        return result

    @classmethod
    def summarize_diff_compact(cls, diff_text: Any, max_files: int = 5) -> Dict[str, Any]:
        """Extract top modified files and change counts suitable for small screens."""
        try:
            parsed = ScopeAndDiffGuard.parse_diff_files(diff_text)
        except DiffInputError as exc:
            return {"total_files": 0, "files_preview": [], "compact_string": "", "error": str(exc)}

        files: List[Dict[str, Any]] = [
            {"file": f["path"].split("/")[-1], "path": f["path"], "added": f["lines_added"], "deleted": f["lines_deleted"]}
            for f in parsed
            if f["path"] != "unknown"
        ]
        files.sort(key=lambda x: x["added"] + x["deleted"], reverse=True)
        top_files = files[: _as_limit(max_files, 5)]
        extra_count = len(files) - len(top_files)

        preview_lines = [f"{f['file']} (+{f['added']}/-{f['deleted']})" for f in top_files]
        if extra_count > 0:
            preview_lines.append(f"...and {extra_count} more files")

        return {
            "total_files": len(files),
            "files_preview": top_files,
            "compact_string": ", ".join(preview_lines),
        }

    @staticmethod
    def summarize_test_results(pytest_output_or_dict: Any) -> Dict[str, Any]:
        """Convert pytest output string or test result dictionary into a mobile banner.

        ``PASSED`` requires at least one passing test and no failures or errors. Text that contains
        no recognisable result counts yields ``UNKNOWN``, never ``PASSED``.
        """
        recognised = True
        if isinstance(pytest_output_or_dict, dict):
            passed = _count(pytest_output_or_dict.get("passed", 0))
            failed = _count(pytest_output_or_dict.get("failed", 0))
            errors = _count(pytest_output_or_dict.get("errors", 0))
            duration = pytest_output_or_dict.get("duration", "N/A")
            recognised = any(k in pytest_output_or_dict for k in ("passed", "failed", "errors"))
        else:
            text = "" if pytest_output_or_dict is None else str(pytest_output_or_dict)
            passed_m = re.search(r"(\d+)\s+passed", text)
            failed_m = re.search(r"(\d+)\s+failed", text)
            errors_m = re.search(r"(\d+)\s+errors?\b", text)
            dur_m = re.search(r"in\s+([\d\.]+s)", text)

            passed = int(passed_m.group(1)) if passed_m else 0
            failed = int(failed_m.group(1)) if failed_m else 0
            errors = int(errors_m.group(1)) if errors_m else 0
            duration = dur_m.group(1) if dur_m else "N/A"
            recognised = bool(passed_m or failed_m or errors_m)
            if not recognised and re.search(r"\bno tests ran\b", text, re.I):
                recognised = True

        if not recognised:
            return {
                "status": "UNKNOWN",
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "duration": duration,
                "banner": "❔ No test results recognised",
            }

        status_ok = (failed == 0 and errors == 0 and passed > 0)
        badge = "PASSED" if status_ok else "FAILED"
        icon = "✅" if status_ok else "❌"

        text_banner = f"{icon} {passed} passed, {failed} failed ({duration})"
        if errors:
            text_banner = f"{icon} {passed} passed, {failed} failed, {errors} errors ({duration})"

        return {
            "status": badge,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "duration": duration,
            "banner": text_banner,
        }

    @staticmethod
    def format_mobile_notification(
        title: str,
        body: str,
        status_emoji: str = "⚡",
        max_length: int = 140,
    ) -> str:
        """Format an SMS or push notification string constrained to max_length."""
        raw = f"{status_emoji} {str(title).strip()}: {str(body).strip()}"
        raw = " ".join(raw.split())  # one line: a newline in a push title/body is a rendering hazard
        return _clip(raw, _as_limit(max_length, 140))

    @staticmethod
    def generate_mobile_card_json(
        title: str,
        sections: List[Dict[str, str]],
        action_buttons: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Produce structured card JSON schema for mobile app renderers."""
        return {
            "type": "mobile_card",
            "title": title,
            "sections": sections if isinstance(sections, list) else [],
            "actions": action_buttons or [
                {"label": "Approve", "action_id": "approve", "style": "primary"},
                {"label": "Reject", "action_id": "reject", "style": "danger"},
            ],
        }
