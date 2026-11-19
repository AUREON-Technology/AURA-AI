from typing import Any, Dict, List, Optional


class MobileArtifactSummarizer:
    """Summarizer for condensing plans, git diffs, and test results into compact mobile cards."""

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