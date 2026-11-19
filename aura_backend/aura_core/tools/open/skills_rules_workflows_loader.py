from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillsRulesWorkflowsLoader:
    """Loader for discovering skills, rules, and workflow markdown definitions in a workspace."""

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