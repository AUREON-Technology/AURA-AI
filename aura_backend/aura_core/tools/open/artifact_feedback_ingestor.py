from typing import Any, Dict, List


class ArtifactFeedbackIngestor:
    """Ingestor for applying user feedback (additions/removals) to an existing plan list."""

    @staticmethod
    def apply_feedback(current_plan: List[str], user_feedback: str) -> Dict[str, Any]:
        """Update a plan list based on natural-language feedback instructions."""
        revised = list(current_plan)
        actions: List[str] = []
        lower = user_feedback.strip().lower()

        remove_triggers = ("remove ", "delete ", "drop ")
        if any(trig in lower for trig in remove_triggers):
            keywords = [w.strip(".,!?") for w in lower.split() if len(w.strip(".,!?")) > 4 and w not in {"remove", "delete", "step", "task"}]
            kept: List[str] = []
            for step in revised:
                step_lower = step.lower()
                if keywords and any(kw in step_lower for kw in keywords):
                    actions.append(f"Removed step: '{step}'")
                else:
                    kept.append(step)
            revised = kept

        add_triggers = ("add ", "include ", "also ")
        if any(lower.startswith(trig) or f" {trig}" in lower for trig in add_triggers):
            new_item = user_feedback.strip()
            revised.append(new_item)
            actions.append(f"Added step: '{new_item}'")

        if not actions:
            actions.append("Recorded feedback without structural plan changes.")

        return {
            "original_steps_count": len(current_plan),
            "revised_steps_count": len(revised),
            "actions": actions,
            "revised_plan": revised,
        }