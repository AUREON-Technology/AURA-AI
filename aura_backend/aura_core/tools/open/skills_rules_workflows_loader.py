"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. ``load_resources``
reads workspace paths on the gateway; the parsing helpers are frontmatter regexes the model
replaces by reading the file.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    ) -> Dict[str, Any]:
        """Discover markdown skill/rule/workflow files and activate those matching context_tags."""
        root = Path(workspace_path)
        if not root.exists() or not root.is_dir():
            return {
                "workspace": workspace_path,
                "exists": False,
                "skills_found": 0,
                "active_skills": [],
                "all_skills": [],
            }

        rel_dirs = cls.DEFAULT_SEARCH_DIRS + (extra_dirs or [])
        tags_lower = [t.strip().lower() for t in (context_tags or []) if t.strip()]
        discovered: List[Dict[str, Any]] = []
        seen_paths = set()

        for rel_dir in rel_dirs:
            sdir = root / rel_dir
            if not sdir.exists() or not sdir.is_dir():
                continue
            for md_file in sorted(sdir.rglob("*.md")):
                resolved = str(md_file.resolve())
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)

                stem_lower = md_file.stem.lower()
                is_active = True if not tags_lower else any(tag in stem_lower for tag in tags_lower)
                discovered.append({
                    "name": md_file.stem,
                    "category": rel_dir,
                    "path": md_file.relative_to(root).as_posix(),
                    "active": is_active,
                })

        active = [item for item in discovered if item["active"]]
        return {
            "workspace": str(root),
            "exists": True,
            "skills_found": len(discovered),
            "active_count": len(active),
            "active_skills": active,
            "all_skills": discovered,
        }

    @staticmethod
    def parse_skill_yaml_frontmatter(file_content: str) -> Dict[str, Any]:
        """Parse frontmatter block between leading '---' fences."""
        stripped = file_content.strip()
        metadata: Dict[str, Any] = {}
        body = file_content

        if stripped.startswith("---"):
            parts = stripped.split("---", 2)
            if len(parts) >= 3:
                raw_frontmatter = parts[1].strip()
                body = parts[2].strip()

                for line in raw_frontmatter.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" in line:
                        key, val = line.split(":", 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if val.startswith("[") and val.endswith("]"):
                            items = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
                            metadata[key] = items
                        else:
                            metadata[key] = val

        return {
            "has_frontmatter": bool(metadata),
            "metadata": metadata,
            "body": body,
            "name": metadata.get("name", ""),
            "description": metadata.get("description", ""),
        }

    @classmethod
    def match_skills_for_task(
        cls,
        skills: List[Dict[str, Any]],
        task_description: str,
        threshold: float = 0.2,
    ) -> List[Dict[str, Any]]:
        """Rank skills based on token overlap with the current task description."""
        tokens = set(re.findall(r"\b[A-Za-z0-9_]{3,}\b", task_description.lower()))
        matches: List[Dict[str, Any]] = []

        for skill in skills:
            name = skill.get("name", "").lower()
            desc = skill.get("description", "").lower()
            tags = [str(t).lower() for t in skill.get("tags", [])]

            score = 0.0
            name_tokens = set(re.findall(r"\b[A-Za-z0-9_]{3,}\b", name))
            desc_tokens = set(re.findall(r"\b[A-Za-z0-9_]{3,}\b", desc))

            # Direct name token match has high weight
            if name_tokens:
                score += len(tokens & name_tokens) * 0.4
            if desc_tokens:
                score += len(tokens & desc_tokens) * 0.15
            for tag in tags:
                if tag in task_description.lower():
                    score += 0.3

            if score >= threshold:
                matched_item = dict(skill)
                matched_item["relevance_score"] = round(min(1.0, score), 2)
                matches.append(matched_item)

        matches.sort(key=lambda x: x["relevance_score"], reverse=True)
        return matches

    @staticmethod
    def load_workflow_steps(workflow_content: str) -> List[Dict[str, Any]]:
        """Parse structured steps, prerequisites, and assertions from workflow markdown text."""
        steps: List[Dict[str, Any]] = []
        current_step: Optional[Dict[str, Any]] = None

        for raw_line in workflow_content.splitlines():
            line = raw_line.strip()
            # Step header like ## Step 1: Initialize ... or 1. Run migrations
            m = re.match(r"^(?:#{1,3}\s+Step\s+(\d+)[:\s]+|(?:(\d+)\.\s+))(.*)$", line, re.I)
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
                if line.startswith(("`", "$")):
                    cmd = line.strip("`$ ")
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
    def validate_skill_structure(cls, skill_content: str) -> Dict[str, Any]:
        """Validate whether a SKILL.md definition complies with the required specification."""
        parsed = cls.parse_skill_yaml_frontmatter(skill_content)
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

        return {
            "valid": len(issues) == 0,
            "issues_count": len(issues),
            "issues": issues,
            "metadata": parsed.get("metadata", {}),
        }
