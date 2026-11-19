from pathlib import Path
from typing import Any, Dict, List, Optional


class ProjectConventionsLoader:
    """Loader for discovering and reading project convention, agent instruction, and config files."""

    DEFAULT_CONVENTION_FILES: List[str] = [
        "AGENTS.md",
        "CLAUDE.md",
        "AURA.md",
        "RULES.md",
        "CONTRIBUTING.md",
        "README.md",
        ".editorconfig",
        "pyproject.toml",
        "ruff.toml",
        ".flake8",
    ]

    @classmethod
    def load_conventions(
        cls,
        workspace_path: str,
        candidate_files: Optional[List[str]] = None,
        max_chars_per_file: int = 4000,
    ) -> Dict[str, Any]:
        """Read convention and configuration files present at workspace root."""
        root = Path(workspace_path)
        if not root.exists() or not root.is_dir():
            return {
                "workspace": workspace_path,
                "exists": False,
                "rules_loaded": False,
                "error": f"Workspace directory not found: {workspace_path}",
                "conventions": {},
            }

        targets = candidate_files if candidate_files is not None else cls.DEFAULT_CONVENTION_FILES
        conventions: Dict[str, str] = {}
        errors: Dict[str, str] = {}

        for filename in targets:
            fpath = root / filename
            if fpath.exists() and fpath.is_file():
                try:
                    raw = fpath.read_text(encoding="utf-8", errors="ignore")
                    conventions[filename] = raw[:max_chars_per_file]
                except OSError as exc:
                    errors[filename] = str(exc)

        return {
            "workspace": str(root),
            "exists": True,
            "rules_loaded": len(conventions) > 0,
            "discovered_files": list(conventions.keys()),
            "conventions": conventions,
            "errors": errors,
        }