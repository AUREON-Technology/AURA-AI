from pathlib import Path
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

    @staticmethod
    def generate_task_list(
        title: str,
        tasks: List[Dict[str, Any]],
        calculate_progress: bool = True,
    ) -> Dict[str, Any]:
        """Generate structured interactive task checklist with completion percentage."""
        total = len(tasks)
        done = sum(1 for t in tasks if t.get("completed", False))
        pct = round((done / total) * 100.0, 1) if total > 0 else 0.0

        md: List[str] = [
            f"# 📋 {title}",
            f"**Progress:** {done}/{total} tasks ({pct}%)",
            "",
            "| Status | Task | Priority |",
            "|:---:|:---|:---:|",
        ]
        for t in tasks:
            is_done = t.get("completed", False)
            box = "[x]" if is_done else "[ ]"
            name = t.get("name", "Unnamed task")
            pri = t.get("priority", "normal")
            md.append(f"| {box} | {name} | `{pri}` |")

        return {
            "title": title,
            "total_tasks": total,
            "completed_tasks": done,
            "progress_percentage": pct,
            "markdown_content": "\n".join(md),
        }

    @staticmethod
    def generate_implementation_plan(
        title: str,
        stages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Generate an execution roadmap with dependencies, outcomes, and risk factors."""
        md: List[str] = [
            f"# 🗺️ Implementation Roadmap: {title}",
            f"**Stages Count:** {len(stages)} | **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        for idx, stg in enumerate(stages, start=1):
            name = stg.get("name", f"Stage {idx}")
            desc = stg.get("description", "No description provided.")
            deps = stg.get("dependencies", [])
            risks = stg.get("risks", "Low")
            md.extend([
                f"### {idx}. {name}",
                f"{desc}",
                f"- **Dependencies:** `{', '.join(deps) if deps else 'None'}`",
                f"- **Risk Level:** `{risks}`",
                "",
            ])

        return {
            "title": title,
            "stages_count": len(stages),
            "markdown_content": "\n".join(md).strip(),
        }

    @staticmethod
    def generate_progress_report(
        title: str,
        completed_items: List[str],
        pending_items: List[str],
        blockers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate executive progress artifact with completed milestones and active blockers."""
        blks = blockers or []
        total = len(completed_items) + len(pending_items)
        rate = round((len(completed_items) / total) * 100.0, 1) if total > 0 else 0.0

        md: List[str] = [
            f"# 📊 Progress Report: {title}",
            f"**Completion Rate:** {rate}% ({len(completed_items)} completed, {len(pending_items)} pending)",
            f"**Timestamp:** `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
            "",
            "## ✅ Completed Milestones",
        ]
        for c in completed_items:
            md.append(f"- {c}")

        md.extend(["", "## ⏳ Pending Tasks"])
        for p in pending_items:
            md.append(f"- {p}")

        if blks:
            md.extend(["", "## ⚠️ Active Blockers"])
            for b in blks:
                md.append(f"- **BLOCKER:** {b}")

        return {
            "title": title,
            "completion_rate": rate,
            "has_blockers": len(blks) > 0,
            "markdown_content": "\n".join(md).strip(),
        }

    @staticmethod
    def generate_walkthrough(
        title: str,
        changes_summary: str,
        verification_steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Generate final solution walkthrough artifact with verification steps and expected outputs."""
        md: List[str] = [
            f"# 🏁 Solution Walkthrough: {title}",
            f"**Date:** `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
            "",
            "## 📝 Changes Overview",
            f"{changes_summary}",
            "",
            "## 🔍 Verification & Demonstration",
        ]
        for idx, step in enumerate(verification_steps, start=1):
            action = step.get("action", f"Step {idx}")
            cmd = step.get("command")
            expected = step.get("expected", "Success")
            md.append(f"### Step {idx}: {action}")
            if cmd:
                md.extend(["```bash", str(cmd), "```"])
            md.extend([f"**Expected Result:** {expected}", ""])

        md.append("## ✅ Verification Status: Complete & Ready for Review")
        return {
            "title": title,
            "verification_steps_count": len(verification_steps),
            "markdown_content": "\n".join(md).strip(),
        }

    @staticmethod
    def save_artifact_to_disk(markdown_content: str, file_path: str) -> Dict[str, Any]:
        """Safely write rendered artifact markdown to target path, creating parents if missing."""
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown_content, encoding="utf-8")
            return {
                "saved": True,
                "file_path": str(path.resolve()),
                "bytes_written": len(markdown_content.encode("utf-8")),
            }
        except Exception as exc:
            return {"saved": False, "error": str(exc), "file_path": file_path}
