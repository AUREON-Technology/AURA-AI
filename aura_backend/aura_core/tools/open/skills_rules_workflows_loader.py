"""Discover, parse, match and validate skills, rules and workflows (markdown, and PDF).

Hardening notes (review of 2026-11):

* ``load_resources(extra_dirs=...)`` joined caller-supplied directories onto the workspace unchecked,
  so ``"../.."`` or an absolute path enumerated files anywhere. Each directory must now resolve inside
  the workspace. The walk skips ``.git`` / ``node_modules`` / virtualenvs, copes with symlink loops
  (``Path.resolve`` raises ``RuntimeError`` on one), and stops at ``max_files`` with ``truncated`` set.
* A skill, rule or workflow can be a **PDF**: ``.pdf`` files in those directories are listed
  (``"format": "pdf"``) and :meth:`read_resource` reads any listed file in full -- for a PDF every page,
  through ``_input`` -- returning its text, parsed frontmatter/body and an ``input`` coverage block.
  :meth:`validate_skill_file` validates a file the same way. Text arguments to the parse/validate
  methods may also be bytes (a PDF's bytes are read in full).
* Frontmatter is delimited by lines that are exactly ``---``; the old ``split("---", 2)`` broke on a
  ``---`` *inside* a value. Folded/literal blocks (``description: >`` / ``|``) and ``- item`` lists,
  which real SKILL.md files use, are parsed instead of being read as the single character ``>``.
* ``match_skills_for_task`` no longer crashes on ``None`` names, string ``tags`` or empty tags (an
  empty tag matched every task), and tokenises with ``\\w`` so non-Latin names and tasks can match.
* ``load_workflow_steps`` reads ``## Step 1`` with no trailing text, keeps the commands of a fenced
  block (it used to record the fence markers ```` ```bash ```` as commands and the commands as prose),
  and ignores numbered lines inside code fences.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._input import SourceReadError, read_source_bytes, read_source_file
from ._time_budget import DEFAULT_BUDGET_S, Deadline

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}
_TOKEN = re.compile(r"\w{3,}")
_FENCE = re.compile(r"^(```|~~~)")
_STEP = re.compile(r"^(?:#{1,3}\s+Step\s+(\d{1,6})[:\s]*|(?:(\d{1,6})\.\s+))(.*)$", re.I)


def _text_of(content: Any) -> "tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]":
    """``(text, input_info, error)`` for text or bytes (a PDF's bytes are read in full)."""
    if isinstance(content, (bytes, bytearray)):
        try:
            read = read_source_bytes(bytes(content))
        except SourceReadError as exc:
            return None, None, str(exc)
        return read.text, {"input": read.describe(), "warnings": read.warnings()}, None
    if content is None:
        return "", None, None
    if not isinstance(content, str):
        return None, None, f"content must be text or bytes, got {type(content).__name__}"
    return content, None, None


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


class SkillsRulesWorkflowsLoader:
    """Loader for discovering, parsing frontmatter, matching, and validating skills, rules, and workflows."""

    DEFAULT_SEARCH_DIRS: List[str] = [
        ".aura/skills",
        ".aura/rules",
        ".aura/workflows",
        "skills",
        "rules",
        "workflows",
    ]

    @classmethod
    def load_resources(
        cls,
        workspace_path: str,
        context_tags: Optional[List[str]] = None,
        extra_dirs: Optional[List[str]] = None,
        max_files: int = 5000,
        include_pdf: bool = True,
        time_budget_s: float = DEFAULT_BUDGET_S,
    ) -> Dict[str, Any]:
        """Discover markdown (and PDF) skill/rule/workflow files and activate those matching context_tags.

        Only directories inside the workspace are searched. ``truncated`` is true when ``max_files``
        was reached or ``time_budget_s`` (default 300, at most 600) ran out (``timed_out``). Use
        :meth:`read_resource` to read one in full.
        """
        try:
            root = Path(workspace_path)
            ok = root.exists() and root.is_dir()
        except (OSError, ValueError, TypeError):
            ok = False
        if not ok:
            return {
                "workspace": str(workspace_path),
                "exists": False,
                "skills_found": 0,
                "active_skills": [],
                "all_skills": [],
            }
        try:
            root_resolved = root.resolve()
        except (OSError, RuntimeError):
            root_resolved = root

        extras = [d for d in (extra_dirs or []) if isinstance(d, str) and d and "\x00" not in d]
        rel_dirs = cls.DEFAULT_SEARCH_DIRS + extras
        tags_lower = [t.strip().lower() for t in (context_tags or []) if isinstance(t, str) and t.strip()]
        suffixes = (".md", ".pdf") if include_pdf else (".md",)
        limit = max_files if isinstance(max_files, int) and max_files > 0 else 5000
        discovered: List[Dict[str, Any]] = []
        seen_paths = set()
        rejected: List[str] = []
        truncated = False
        timed_out = False
        deadline = Deadline(time_budget_s)

        for rel_dir in rel_dirs:
            sdir = root / rel_dir
            try:
                if not sdir.exists() or not sdir.is_dir():
                    continue
                sdir_resolved = sdir.resolve()
                sdir_resolved.relative_to(root_resolved)
            except ValueError:
                rejected.append(rel_dir)
                continue
            except (OSError, RuntimeError):
                continue

            for current, dirs, files in os.walk(sdir):
                dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
                for name in sorted(files):
                    if not name.lower().endswith(suffixes):
                        continue
                    md_file = Path(current) / name
                    try:
                        resolved_path = md_file.resolve()
                        resolved_path.relative_to(root_resolved)
                        resolved = str(resolved_path)
                    except (OSError, RuntimeError, ValueError):
                        continue  # a link that leaves the workspace, or a loop
                    if resolved in seen_paths:
                        continue
                    if len(discovered) >= limit:
                        truncated = True
                        break
                    if deadline.expired():
                        truncated = timed_out = True
                        break
                    seen_paths.add(resolved)

                    stem = md_file.stem
                    stem_lower = stem.lower()
                    is_active = True if not tags_lower else any(tag in stem_lower for tag in tags_lower)
                    discovered.append({
                        "name": stem,
                        "category": rel_dir,
                        "path": md_file.relative_to(root).as_posix(),
                        "active": is_active,
                        "format": "pdf" if name.lower().endswith(".pdf") else "markdown",
                    })
                if truncated:
                    break
            if truncated:
                break

        active = [item for item in discovered if item["active"]]
        result: Dict[str, Any] = {
            "workspace": str(root),
            "exists": True,
            "skills_found": len(discovered),
            "active_count": len(active),
            "active_skills": active,
            "all_skills": discovered,
            "truncated": truncated,
            "timed_out": timed_out,
        }
        warnings: List[str] = []
        if rejected:
            result["rejected_dirs"] = rejected
            warnings.append(f"Directories outside the workspace were not searched: {rejected}")
        if timed_out:
            warnings.append(deadline.message("the listing is incomplete."))
        elif truncated:
            warnings.append(f"Stopped after {limit} files; more exist.")
        if warnings:
            result["warnings"] = warnings
        return result

    @classmethod
    def read_resource(
        cls,
        workspace_path: str,
        relative_path: str,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read one skill/rule/workflow file in full and parse its frontmatter.

        A PDF is read page by page (all pages); ``input`` reports pages read and any scanned page that
        needs OCR. The path must stay inside the workspace (and inside ``base_dir`` when given).
        """
        if not isinstance(workspace_path, str) or not isinstance(relative_path, str) or not relative_path:
            return {"read": False, "error": "workspace_path and relative_path must be non-empty strings."}
        try:
            target = Path(workspace_path) / relative_path
            read = read_source_file(target, base_dir=workspace_path)
            if base_dir is not None:
                read_source_file(target, base_dir=base_dir)
        except SourceReadError as exc:
            return {"read": False, "path": relative_path, "error": str(exc)}
        parsed = cls.parse_skill_yaml_frontmatter(read.text)
        result: Dict[str, Any] = {
            "read": True,
            "path": relative_path,
            "text": read.text,
            "frontmatter": parsed["metadata"],
            "body": parsed["body"],
            "name": parsed["name"],
            "description": parsed["description"],
            "input": read.describe(),
        }
        if read.warnings():
            result["warnings"] = read.warnings()
        return result

    @staticmethod
    def parse_skill_yaml_frontmatter(file_content: Union[str, bytes]) -> Dict[str, Any]:
        """Parse frontmatter block between leading '---' fences (a line that is exactly ``---``).

        Supports ``key: value``, ``key: [a, b]``, ``key:`` followed by ``- item`` lines, and folded /
        literal blocks (``key: >``, ``key: |``).
        """
        text, info, error = _text_of(file_content)
        if text is None:
            return {"has_frontmatter": False, "metadata": {}, "body": "", "name": "", "description": "",
                    "error": error}

        metadata: Dict[str, Any] = {}
        body = text
        lines = text.lstrip("﻿").splitlines()
        first = next((i for i, ln in enumerate(lines) if ln.strip()), None)

        if first is not None and lines[first].strip() == "---":
            end = next((i for i in range(first + 1, len(lines)) if lines[i].strip() in ("---", "...")), None)
            if end is not None:
                body = "\n".join(lines[end + 1 :]).strip()
                block = lines[first + 1 : end]
                i = 0
                while i < len(block):
                    raw = block[i]
                    line = raw.strip()
                    i += 1
                    if not line or line.startswith("#") or ":" not in line:
                        continue
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    if val in (">", "|", ">-", "|-", ">+", "|+"):
                        folded = val.startswith(">")
                        parts: List[str] = []
                        while i < len(block) and (not block[i].strip() or block[i][:1] in (" ", "\t")):
                            parts.append(block[i].strip())
                            i += 1
                        metadata[key] = (" " if folded else "\n").join(p for p in parts if p or not folded).strip()
                    elif not val:
                        items: List[str] = []
                        while i < len(block) and (not block[i].strip() or block[i].lstrip().startswith("- ")):
                            if block[i].strip():
                                items.append(_unquote(block[i].lstrip()[2:]))
                            i += 1
                        metadata[key] = items if items else ""
                    elif val.startswith("[") and val.endswith("]"):
                        metadata[key] = [_unquote(x) for x in val[1:-1].split(",") if x.strip()]
                    else:
                        metadata[key] = _unquote(val)

        result = {
            "has_frontmatter": bool(metadata),
            "metadata": metadata,
            "body": body,
            "name": metadata.get("name", ""),
            "description": metadata.get("description", ""),
        }
        if info:
            result["input"] = info["input"]
            if info["warnings"]:
                result["warnings"] = info["warnings"]
        return result

    @classmethod
    def match_skills_for_task(
        cls,
        skills: List[Dict[str, Any]],
        task_description: str,
        threshold: float = 0.2,
    ) -> List[Dict[str, Any]]:
        """Rank skills based on token overlap with the current task description."""
        if not isinstance(task_description, str) or not isinstance(skills, list):
            return []
        task_lower = task_description.lower()
        tokens = set(_TOKEN.findall(task_lower))
        matches: List[Dict[str, Any]] = []

        for skill in skills:
            if not isinstance(skill, dict):
                continue
            name = str(skill.get("name") or "").lower()
            desc = str(skill.get("description") or "").lower()
            raw_tags = skill.get("tags") or []
            if isinstance(raw_tags, str):
                raw_tags = raw_tags.split(",")
            tags = [str(t).strip().lower() for t in raw_tags if str(t).strip()]

            score = 0.0
            name_tokens = set(_TOKEN.findall(name))
            desc_tokens = set(_TOKEN.findall(desc))

            # Direct name token match has high weight
            if name_tokens:
                score += len(tokens & name_tokens) * 0.4
            if desc_tokens:
                score += len(tokens & desc_tokens) * 0.15
            for tag in tags:
                if re.search(rf"(?<!\w){re.escape(tag)}(?!\w)", task_lower):
                    score += 0.3

            if score >= threshold:
                matched_item = dict(skill)
                matched_item["relevance_score"] = round(min(1.0, score), 2)
                matches.append(matched_item)

        matches.sort(key=lambda x: x["relevance_score"], reverse=True)
        return matches

    @staticmethod
    def load_workflow_steps(workflow_content: Union[str, bytes]) -> List[Dict[str, Any]]:
        """Parse structured steps, prerequisites, and assertions from workflow markdown text."""
        text, _info, _error = _text_of(workflow_content)
        if not text:
            return []
        steps: List[Dict[str, Any]] = []
        current_step: Optional[Dict[str, Any]] = None
        in_fence = False

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            # Step header like ## Step 1: Initialize ... or 1. Run migrations
            m = None if in_fence else _STEP.match(line)
            if m:
                if current_step:
                    steps.append(current_step)
                step_num = int(m.group(1) or m.group(2) or len(steps) + 1)
                title = (m.group(3) or "").strip()
                current_step = {
                    "step_number": step_num,
                    "title": title,
                    "commands": [],
                    "description": [],
                }
            elif current_step:
                if in_fence:
                    if line:
                        current_step["commands"].append(line.lstrip("$ ").strip())
                elif line.startswith(("`", "$")):
                    cmd = line.strip("`$ ")
                    if cmd:
                        current_step["commands"].append(cmd)
                elif line:
                    current_step["description"].append(line)

        if current_step:
            steps.append(current_step)

        # Merge descriptions
        for s in steps:
            s["description_text"] = "\n".join(s["description"])

        return steps

    @classmethod
    def validate_skill_structure(cls, skill_content: Union[str, bytes]) -> Dict[str, Any]:
        """Validate whether a SKILL.md definition complies with the required specification."""
        parsed = cls.parse_skill_yaml_frontmatter(skill_content)
        if parsed.get("error"):
            return {"valid": False, "issues_count": 1, "issues": [parsed["error"]], "metadata": {}}
        issues: List[str] = []

        if not parsed["has_frontmatter"]:
            issues.append("Missing YAML frontmatter (enclosed by '---')")
        else:
            meta = parsed["metadata"]
            if not meta.get("name"):
                issues.append("Frontmatter missing required 'name' field")
            if not meta.get("description"):
                issues.append("Frontmatter missing required 'description' field")

        if len(parsed["body"].split()) < 10:
            issues.append("Skill body is empty or too short (needs clear instructions)")

        result: Dict[str, Any] = {
            "valid": len(issues) == 0,
            "issues_count": len(issues),
            "issues": issues,
            "metadata": parsed.get("metadata", {}),
        }
        if "input" in parsed:
            result["input"] = parsed["input"]
        if parsed.get("warnings"):
            result["warnings"] = parsed["warnings"]
        return result

    @classmethod
    def validate_skill_file(cls, file_path: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """:meth:`validate_skill_structure` on a file. A PDF is read in full (all pages) first."""
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"valid": False, "issues_count": 1, "issues": [str(exc)], "metadata": {}}
        result = cls.validate_skill_structure(read.text)
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = read.warnings()
        return result
