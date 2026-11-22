"""Render task lists, plans, progress reports and walkthroughs as Markdown, and save them to disk.

Text in, Markdown out -- no input is read from a file, so there is no PDF input here. Review notes:

* Caller text is untrusted: a title or item containing a newline could forge headings or list rows,
  a ``|`` or newline broke the task-list table, and a command containing a code fence closed the
  walkthrough's code block. Single-line fields are collapsed to one line, table cells escape ``|``,
  and a fence is always longer than any run of backticks inside it.
* The walkthrough used to end with "Verification Status: Complete & Ready for Review" whatever the
  steps said (even with none). The status is now derived from per-step results the caller supplies.
* ``completed: "false"`` (a string) counted as done; booleans are parsed strictly.
* ``save_artifact_to_disk`` is an arbitrary-file write. It only writes ``.md``/``.markdown``/``.txt``
  files, and with ``base_dir`` it refuses any path outside that directory.
"""

import re
import tempfile
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union

_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
_SAVE_SUFFIXES = {".md", ".markdown", ".txt"}
_TRUE = {"true", "yes", "y", "1", "done", "x", "complete", "completed", "✓", "✅", "تم", "نعم"}


def _line(value: Any, default: str = "") -> str:
    """One line of plain text: whitespace (including newlines) collapsed, NUL removed."""
    if value is None:
        return default
    text = " ".join(str(value).replace("\x00", "").split())
    return text or default


def _cell(value: Any, default: str = "") -> str:
    """A Markdown table cell: one line, pipe escaped."""
    return _line(value, default).replace("|", "\\|")


def _body(value: Any) -> str:
    """Multi-line text with NUL removed and any line that would start a heading demoted to plain text."""
    text = "" if value is None else str(value).replace("\x00", "")
    return "\n".join(("\\" + ln) if re.match(r"^\s{0,3}#{1,6}(\s|$)", ln) else ln for ln in text.splitlines())


def _strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(value)]
    try:
        return [x if isinstance(x, str) else str(x) for x in value]
    except TypeError:
        return [str(value)]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == value and value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return False


def _dicts(value: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in value or []:
        out.append(item if isinstance(item, dict) else {"name": str(item)})
    return out


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


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
        """Render a structured Markdown document for the specified artifact_type.

        An unknown ``artifact_type`` is rendered as a walkthrough and ``warnings`` says so.
        """
        requested = _line(artifact_type).lower()
        atype = requested
        warnings: List[str] = []
        if atype not in cls.SUPPORTED_TYPES:
            warnings.append(
                f"Unknown artifact_type {requested!r}; rendered as 'walkthrough'. "
                f"Supported: {', '.join(sorted(cls.SUPPORTED_TYPES))}."
            )
            atype = "walkthrough"

        entries = _strings(items)
        safe_title = _line(title, "Untitled")
        lines: List[str] = [
            f"# {safe_title}",
            "",
            f"- Type: `{atype}`",
            f"- Generated: `{_now()}`",
            "",
        ]

        if atype == "task_list":
            lines.append("## Checklist")
            for item in entries:
                lines.append(f"- [ ] {_line(item)}")
        elif atype == "implementation_plan":
            lines.append("## Implementation Plan")
            for idx, item in enumerate(entries, start=1):
                lines.append(f"{idx}. {_line(item)}")
        elif atype == "progress_report":
            lines.append("## Progress Report")
            for item in entries:
                lines.append(f"- {_line(item)}")
        else:
            lines.append("## Walkthrough")
            for idx, item in enumerate(entries, start=1):
                lines.append(f"### Step {idx}")
                lines.append(_body(item))
                lines.append("")

        markdown_text = "\n".join(lines).strip()
        result: Dict[str, Any] = {
            "title": title,
            "artifact_type": atype,
            "items_count": len(entries),
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            "markdown_content": markdown_text,
        }
        if warnings:
            result["warnings"] = warnings
        return result

    @staticmethod
    def generate_task_list(
        title: str,
        tasks: List[Dict[str, Any]],
        calculate_progress: bool = True,
    ) -> Dict[str, Any]:
        """Generate structured interactive task checklist with completion percentage."""
        rows = _dicts(tasks)
        total = len(rows)
        done = sum(1 for t in rows if _truthy(t.get("completed", False)))
        pct = round((done / total) * 100.0, 1) if total > 0 else 0.0

        md: List[str] = [
            f"# 📋 {_line(title, 'Untitled')}",
            f"**Progress:** {done}/{total} tasks ({pct}%)",
            "",
            "| Status | Task | Priority |",
            "|:---:|:---|:---:|",
        ]
        for t in rows:
            box = "[x]" if _truthy(t.get("completed", False)) else "[ ]"
            name = _cell(t.get("name"), "Unnamed task")
            pri = _cell(t.get("priority"), "normal").replace("`", "'")
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
        rows = _dicts(stages)
        md: List[str] = [
            f"# 🗺️ Implementation Roadmap: {_line(title, 'Untitled')}",
            f"**Stages Count:** {len(rows)} | **Generated:** {_now()}",
            "",
        ]
        for idx, stg in enumerate(rows, start=1):
            name = _line(stg.get("name"), f"Stage {idx}")
            desc = _body(stg.get("description")) or "No description provided."
            deps = [_line(d) for d in _strings(stg.get("dependencies")) if _line(d)]
            risks = _line(stg.get("risks"), "Low").replace("`", "'")
            md.extend([
                f"### {idx}. {name}",
                desc,
                f"- **Dependencies:** `{', '.join(deps).replace('`', chr(39)) if deps else 'None'}`",
                f"- **Risk Level:** `{risks}`",
                "",
            ])

        return {
            "title": title,
            "stages_count": len(rows),
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
        done = _strings(completed_items)
        pending = _strings(pending_items)
        blks = _strings(blockers)
        total = len(done) + len(pending)
        rate = round((len(done) / total) * 100.0, 1) if total > 0 else 0.0

        md: List[str] = [
            f"# 📊 Progress Report: {_line(title, 'Untitled')}",
            f"**Completion Rate:** {rate}% ({len(done)} completed, {len(pending)} pending)",
            f"**Timestamp:** `{_now()}`",
            "",
            "## ✅ Completed Milestones",
        ]
        for c in done:
            md.append(f"- {_line(c)}")

        md.extend(["", "## ⏳ Pending Tasks"])
        for p in pending:
            md.append(f"- {_line(p)}")

        if blks:
            md.extend(["", "## ⚠️ Active Blockers"])
            for b in blks:
                md.append(f"- **BLOCKER:** {_line(b)}")

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
        """Generate final solution walkthrough artifact with verification steps and expected outputs.

        The closing status reflects the steps: a step may carry ``passed`` (bool) to record its
        outcome. This method does not run anything, so steps without a ``passed`` value are reported
        as "not verified" -- never as complete.
        """
        steps = _dicts(verification_steps)
        md: List[str] = [
            f"# 🏁 Solution Walkthrough: {_line(title, 'Untitled')}",
            f"**Date:** `{_now()}`",
            "",
            "## 📝 Changes Overview",
            _body(changes_summary),
            "",
            "## 🔍 Verification & Demonstration",
        ]
        passed = failed = unknown = 0
        for idx, step in enumerate(steps, start=1):
            action = _line(step.get("action"), f"Step {idx}")
            cmd = step.get("command")
            expected = _line(step.get("expected"), "Success")
            md.append(f"### Step {idx}: {action}")
            if cmd:
                cmd_text = str(cmd).replace("\x00", "")
                fence = _fence(cmd_text)
                md.extend([f"{fence}bash", cmd_text, fence])
            md.extend([f"**Expected Result:** {expected}", ""])
            if "passed" in step:
                if _truthy(step["passed"]):
                    passed += 1
                else:
                    failed += 1
            else:
                unknown += 1

        if not steps:
            status = "No verification steps were provided - not verified"
        elif failed:
            status = f"{failed} of {len(steps)} verification step(s) FAILED"
        elif unknown:
            status = f"Not verified - {unknown} of {len(steps)} step(s) have no recorded result"
        else:
            status = f"All {passed} verification step(s) reported passing (as supplied by the caller)"
        md.append(f"## Verification Status: {status}")
        return {
            "title": title,
            "verification_steps_count": len(steps),
            "verification_passed": passed,
            "verification_failed": failed,
            "verification_unrecorded": unknown,
            "verified": bool(steps) and not failed and not unknown,
            "markdown_content": "\n".join(md).strip(),
        }

    @staticmethod
    def save_artifact_to_disk(
        markdown_content: str,
        file_path: str,
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Write rendered artifact markdown to ``file_path``, creating parent directories.

        Only ``.md``, ``.markdown`` and ``.txt`` targets are written, at most 5 MB, and (with
        ``base_dir``) only inside that directory. The write is atomic (temp file + rename), so a
        failure never leaves a half-written artifact.
        """
        if not isinstance(markdown_content, str):
            return {"saved": False, "error": "markdown_content must be a string.", "file_path": str(file_path)}
        data = markdown_content.encode("utf-8", "replace")
        if len(data) > _MAX_ARTIFACT_BYTES:
            return {"saved": False, "error": f"Artifact is too large to save ({len(data)} bytes; limit {_MAX_ARTIFACT_BYTES}).",
                    "file_path": str(file_path)}
        try:
            path = Path(file_path).expanduser()
            resolved = path.resolve()
        except (OSError, RuntimeError, ValueError, TypeError):
            return {"saved": False, "error": "Invalid path.", "file_path": str(file_path)}
        if resolved.suffix.lower() not in _SAVE_SUFFIXES:
            return {"saved": False, "file_path": str(file_path),
                    "error": f"Only {', '.join(sorted(_SAVE_SUFFIXES))} files can be written; got {resolved.suffix or 'no extension'!r}."}
        if base_dir is not None:
            try:
                resolved.relative_to(Path(base_dir).resolve())
            except ValueError:
                return {"saved": False, "error": f"Path is outside the allowed directory: {file_path}",
                        "file_path": str(file_path)}
        if resolved.is_dir():
            return {"saved": False, "error": "Path is a directory.", "file_path": str(file_path)}
        tmp_name = None
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".artifact-", dir=str(resolved.parent))
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, resolved)
            tmp_name = None
            return {"saved": True, "file_path": str(resolved), "bytes_written": len(data)}
        except OSError as exc:
            return {"saved": False, "error": exc.strerror or type(exc).__name__, "file_path": str(file_path)}
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
