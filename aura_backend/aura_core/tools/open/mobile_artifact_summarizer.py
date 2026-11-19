import re
from typing import Any, Dict, List, Optional


class MobileArtifactSummarizer:
    """Summarizer for condensing engineering plans, git diffs, CI test suites, and audit logs into compact mobile cards."""

    @staticmethod
    def count_diff_lines(diff_text: str) -> Dict[str, int]:
        """Count added and removed lines in a unified diff string."""
        added = 0
        removed = 0
        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return {"added": added, "removed": removed}

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

        if task_summary:
            clean_task = " ".join(task_summary.strip().split())
            if len(clean_task) > 120:
                clean_task = clean_task[:117] + "..."
            cards.append(f"Task: {clean_task}")

        if diff_text:
            counts = cls.count_diff_lines(diff_text)
            cards.append(f"Diff: +{counts['added']} / -{counts['removed']} lines")

        if test_results:
            passed = int(test_results.get("passed", 0))
            failed = int(test_results.get("failed", 0))
            cards.append(f"Tests: {passed} passed, {failed} failed")

        summary_text = "\n".join(cards)
        if len(summary_text) > max_chars:
            summary_text = summary_text[: max(0, max_chars - 3)] + "..."

        return {
            "mobile_ready": True,
            "summary_text": summary_text,
            "character_count": len(summary_text),
            "cards_count": len(cards),
        }

    @classmethod
    def summarize_diff_compact(cls, diff_text: str, max_files: int = 5) -> Dict[str, Any]:
        """Extract top modified files and change counts suitable for small screens."""
        files: List[Dict[str, Any]] = []
        current_file = None
        added = 0
        deleted = 0

        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                if current_file:
                    files.append({"file": current_file, "added": added, "deleted": deleted})
                current_file = line[6:].strip().split("/")[-1]
                added = 0
                deleted = 0
            elif line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                deleted += 1

        if current_file:
            files.append({"file": current_file, "added": added, "deleted": deleted})

        files.sort(key=lambda x: x["added"] + x["deleted"], reverse=True)
        top_files = files[:max_files]
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
        """Convert pytest output string or test result dictionary into a mobile banner."""
        if isinstance(pytest_output_or_dict, dict):
            passed = int(pytest_output_or_dict.get("passed", 0))
            failed = int(pytest_output_or_dict.get("failed", 0))
            errors = int(pytest_output_or_dict.get("errors", 0))
            duration = pytest_output_or_dict.get("duration", "N/A")
        else:
            text = str(pytest_output_or_dict)
            passed_m = re.search(r"(\d+)\s+passed", text)
            failed_m = re.search(r"(\d+)\s+failed", text)
            errors_m = re.search(r"(\d+)\s+error", text)
            dur_m = re.search(r"in\s+([\d\.]+s)", text)

            passed = int(passed_m.group(1)) if passed_m else 0
            failed = int(failed_m.group(1)) if failed_m else 0
            errors = int(errors_m.group(1)) if errors_m else 0
            duration = dur_m.group(1) if dur_m else "N/A"

        status_ok = (failed == 0 and errors == 0 and passed > 0)
        badge = "PASSED" if status_ok else "FAILED"
        icon = "✅" if status_ok else "❌"

        text_banner = f"{icon} {passed} passed, {failed} failed ({duration})"

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
        raw = f"{status_emoji} {title.strip()}: {body.strip()}"
        if len(raw) <= max_length:
            return raw
        return raw[: max(0, max_length - 3)] + "..."

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
            "sections": sections,
            "actions": action_buttons or [
                {"label": "Approve", "action_id": "approve", "style": "primary"},
                {"label": "Reject", "action_id": "reject", "style": "danger"},
            ],
        }
