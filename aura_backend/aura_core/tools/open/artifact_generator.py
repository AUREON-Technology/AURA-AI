import time
from typing import Any, Dict, List, Optional


class ArtifactGenerator:
    """Generator for structured Markdown task checklists, plans, progress reports, and walkthroughs."""

    SUPPORTED_TYPES = {"task_list", "implementation_plan", "progress_report", "walkthrough"}

    @classmethod
    def generate(
        cls,
        artifact_type: str,
        title: str,
        items: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Render a structured Markdown document for the specified artifact_type."""
        atype = artifact_type.strip().lower()
        if atype not in cls.SUPPORTED_TYPES:
            atype = "walkthrough"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        lines: List[str] = [
            f"# {title}",
            "",
            f"- Type: `{atype}`",
            f"- Generated: `{timestamp}`",
            "",
        ]

        if atype == "task_list":
            lines.append("## Checklist")
            for item in items:
                lines.append(f"- [ ] {item}")
        elif atype == "implementation_plan":
            lines.append("## Implementation Plan")
            for idx, item in enumerate(items, start=1):
                lines.append(f"{idx}. {item}")
        elif atype == "progress_report":
            lines.append("## Progress Report")
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append("## Walkthrough")
            for idx, item in enumerate(items, start=1):
                lines.append(f"### Step {idx}")
                lines.append(item)
                lines.append("")

        markdown_text = "\n".join(lines).strip()
        return {
            "title": title,
            "artifact_type": atype,
            "items_count": len(items),
            "metadata": dict(metadata) if metadata else {},
            "markdown_content": markdown_text,
        }