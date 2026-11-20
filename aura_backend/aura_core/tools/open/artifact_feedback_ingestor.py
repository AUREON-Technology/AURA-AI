import difflib
import re
from typing import Any, Dict, List, Optional


class ArtifactFeedbackIngestor:
    """Ingestor for processing, classifying, and applying user feedback to plans and task artifacts."""

    @staticmethod
    def classify_feedback_intent(feedback_text: str) -> Dict[str, Any]:
        """Classify user feedback into actionable intent categories with urgency level."""
        lower = feedback_text.strip().lower()

        intent = "GENERAL_COMMENT"
        urgency = "LOW"

        if any(w in lower for w in ("approve", "looks good", "lgtm", "proceed", "accepted", "perfect")):
            intent = "APPROVE"
            urgency = "LOW"
        elif any(w in lower for w in ("reject", "cancel", "stop", "abort", "wrong", "broken", "unacceptable")):
            intent = "REJECT"
            urgency = "HIGH"
        elif any(w in lower for w in ("remove", "delete", "drop", "omit", "exclude", "skip")):
            intent = "REMOVE_STEP"
            urgency = "MEDIUM"
        elif any(w in lower for w in ("add", "include", "also", "insert", "need to")):
            intent = "ADD_STEP"
            urgency = "MEDIUM"
        elif any(w in lower for w in ("change", "modify", "update", "instead", "replace", "refactor")):
            intent = "MODIFY_STEP"
            urgency = "MEDIUM"
        elif any(w in lower for w in ("why", "what", "how", "explain", "clarify", "?")):
            intent = "CLARIFICATION_QUESTION"
            urgency = "LOW"

        return {
            "intent": intent,
            "urgency": urgency,
            "raw_feedback": feedback_text,
            "requires_plan_update": intent in ("REMOVE_STEP", "ADD_STEP", "MODIFY_STEP", "REJECT"),
        }

    @staticmethod
    def apply_feedback(current_plan: List[str], user_feedback: str) -> Dict[str, Any]:
        """Update a plan list based on natural-language feedback instructions."""
        revised = list(current_plan)
        actions: List[str] = []
        lower = user_feedback.strip().lower()

        remove_triggers = ("remove ", "delete ", "drop ", "skip ")
        if any(trig in lower for trig in remove_triggers):
            keywords = [
                w.strip(".,!?") for w in lower.split()
                if len(w.strip(".,!?")) > 3 and w not in {"remove", "delete", "step", "task", "drop", "skip"}
            ]
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

    @staticmethod
    def patch_markdown_artifact(
        markdown_text: str,
        section_name: str,
        new_content: str,
        mode: str = "replace",
    ) -> Dict[str, Any]:
        """Patch a specific section in a Markdown artifact document based on user critique."""
        lines = markdown_text.splitlines()
        header_pattern = re.compile(rf"^(#+)\s+{re.escape(section_name)}\b", re.IGNORECASE)

        start_idx = None
        end_idx = None
        header_level = 0

        for i, line in enumerate(lines):
            match = header_pattern.match(line)
            if match:
                start_idx = i
                header_level = len(match.group(1))
                break

        if start_idx is None:
            # Section not found — append at the end
            patched = markdown_text.rstrip() + f"\n\n## {section_name}\n{new_content}\n"
            return {
                "patched": True,
                "action": "appended_new_section",
                "section": section_name,
                "content": patched,
            }

        # Find the end of this section (next header with same or higher level)
        next_header_pattern = re.compile(rf"^#{{1,{header_level}}}\s+")
        for i in range(start_idx + 1, len(lines)):
            if next_header_pattern.match(lines[i]):
                end_idx = i
                break
        if end_idx is None:
            end_idx = len(lines)

        header_line = lines[start_idx]
        if mode == "append":
            existing_section = lines[start_idx + 1 : end_idx]
            replacement_lines = [header_line] + existing_section + ["", new_content]
        else:  # replace
            replacement_lines = [header_line, new_content]

        new_lines = lines[:start_idx] + replacement_lines + lines[end_idx:]
        patched_text = "\n".join(new_lines)
        return {
            "patched": True,
            "action": f"{mode}d_section",
            "section": section_name,
            "content": patched_text,
        }

    @staticmethod
    def generate_feedback_diff(original_text: str, revised_text: str) -> Dict[str, Any]:
        """Compute unified line-by-line diff comparing original vs feedback-revised artifact."""
        diff = list(difflib.unified_diff(
            original_text.splitlines(keepends=True),
            revised_text.splitlines(keepends=True),
            fromfile="original_artifact.md",
            tofile="revised_artifact.md",
            n=2,
        ))
        diff_str = "".join(diff)
        additions = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
        deletions = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))

        return {
            "has_changes": len(diff) > 0,
            "lines_added": additions,
            "lines_deleted": deletions,
            "unified_diff": diff_str,
        }

    @classmethod
    def resolve_plan_conflicts(
        cls,
        current_plan: List[str],
        feedback_batch: List[str],
    ) -> Dict[str, Any]:
        """Sequentially apply a batch of feedback comments while preventing duplicate steps."""
        working = list(current_plan)
        all_actions = []

        for feedback in feedback_batch:
            res = cls.apply_feedback(working, feedback)
            working = res["revised_plan"]
            all_actions.extend(res["actions"])

        # Deduplicate while preserving order
        deduped: List[str] = []
        for step in working:
            if step not in deduped:
                deduped.append(step)

        return {
            "initial_step_count": len(current_plan),
            "final_step_count": len(deduped),
            "actions_applied": all_actions,
            "resolved_plan": deduped,
        }
