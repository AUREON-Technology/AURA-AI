import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProjectConventionsLoader:
    """Loader for discovering, parsing, and verifying project conventions and agent instructions."""

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

    CONVENTION_CATEGORIES: Dict[str, List[str]] = {
        "agent_instructions": ["AGENTS.md", "CLAUDE.md", "AURA.md", "RULES.md", ".cursorrules"],
        "developer_guide": ["CONTRIBUTING.md", "README.md", "DEVELOPMENT.md", "ARCHITECTURE.md"],
        "code_style": [".editorconfig", "ruff.toml", ".flake8", ".prettierrc", ".eslintrc.json"],
        "packaging": ["pyproject.toml", "setup.cfg", "package.json", "Cargo.toml", "go.mod"],
    }

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

    @classmethod
    def discover_convention_files(
        cls,
        workspace_path: str,
        search_subdirs: bool = False,
        extra_patterns: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Discover all convention, rule, and style config files across the workspace."""
        root = Path(workspace_path)
        if not root.exists() or not root.is_dir():
            return []

        results: List[Dict[str, Any]] = []
        known_names = set(cls.DEFAULT_CONVENTION_FILES)
        if extra_patterns:
            known_names.update(extra_patterns)

        if search_subdirs:
            # Look up to depth 3
            for current_root, dirs, files in os.walk(root):
                # skip git and venvs
                dirs[:] = [d for d in dirs if d not in (".git", "venv", ".venv", "node_modules", "__pycache__")]
                for f in files:
                    if f in known_names or f.endswith((".rules", "rules.md")):
                        full_p = Path(current_root) / f
                        try:
                            st = full_p.stat()
                            results.append({
                                "filename": f,
                                "relative_path": str(full_p.relative_to(root)).replace("\\", "/"),
                                "size_bytes": st.st_size,
                                "category": cls._categorize_filename(f),
                            })
                        except OSError:
                            pass
        else:
            for f in known_names:
                p = root / f
                if p.is_file():
                    try:
                        st = p.stat()
                        results.append({
                            "filename": f,
                            "relative_path": f,
                            "size_bytes": st.st_size,
                            "category": cls._categorize_filename(f),
                        })
                    except OSError:
                        pass

        return sorted(results, key=lambda x: x["relative_path"])

    @classmethod
    def _categorize_filename(cls, filename: str) -> str:
        for cat, names in cls.CONVENTION_CATEGORIES.items():
            if filename in names:
                return cat
        if filename.endswith(".md"):
            return "developer_guide"
        return "general_config"

    @staticmethod
    def parse_editorconfig(editorconfig_path_or_content: str) -> Dict[str, Dict[str, str]]:
        """Parse .editorconfig syntax into section glob mapping to formatting rules."""
        raw_text = editorconfig_path_or_content
        p = Path(editorconfig_path_or_content)
        if p.exists() and p.is_file():
            try:
                raw_text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return {}

        sections: Dict[str, Dict[str, str]] = {}
        current_section = "*"

        for line in raw_text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                if current_section not in sections:
                    sections[current_section] = {}
            elif "=" in line:
                key, val = line.split("=", 1)
                key = key.strip().lower()
                val = val.strip().lower()
                if current_section not in sections:
                    sections[current_section] = {}
                sections[current_section][key] = val

        return sections

    @staticmethod
    def extract_agent_rules(markdown_content: str) -> Dict[str, Any]:
        """Extract explicit DO, DON'T, MUST, NEVER constraints and checklist commands from markdown rules."""
        dos: List[str] = []
        donts: List[str] = []
        commands: List[str] = []
        sections: List[str] = []

        lines = markdown_content.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                sections.append(stripped.lstrip("#").strip())
                continue

            # Command extraction (e.g. `pytest ...` or $ command)
            cmd_match = re.search(r"`([^`]+)`", stripped)
            if cmd_match and any(tok in cmd_match.group(1) for tok in ("pytest", "git", "pip", "npm", "python", "make")):
                commands.append(cmd_match.group(1))

            # DO / MUST constraints
            if re.search(r"\b(must|always|ensure|do\b|required)\b", stripped, re.IGNORECASE):
                cleaned = re.sub(r"^[-*0-9.]+\s*", "", stripped)
                if cleaned and cleaned not in dos and not re.search(r"\b(never|don't|not)\b", stripped, re.IGNORECASE):
                    dos.append(cleaned)

            # DON'T / NEVER constraints
            if re.search(r"\b(never|don't|do not|must not|forbidden|avoid)\b", stripped, re.IGNORECASE):
                cleaned = re.sub(r"^[-*0-9.]+\s*", "", stripped)
                if cleaned and cleaned not in donts:
                    donts.append(cleaned)

        return {
            "section_count": len(sections),
            "sections": sections,
            "must_do_directives": dos[:30],
            "forbidden_directives": donts[:30],
            "common_commands": list(dict.fromkeys(commands))[:20],
        }

    @staticmethod
    def validate_file_against_conventions(
        file_path: str,
        file_content: str,
        editorconfig_rules: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Verify basic syntactic conventions (indentation style, trailing whitespace, final newline)."""
        rules = editorconfig_rules or {
            "indent_style": "space",
            "indent_size": "4",
            "insert_final_newline": "true",
            "trim_trailing_whitespace": "true",
            "max_line_length": "120",
        }

        violations: List[str] = []
        lines = file_content.splitlines(keepends=True)

        # Check final newline
        if rules.get("insert_final_newline") == "true":
            if file_content and not file_content.endswith(("\n", "\r\n")):
                violations.append("Missing final newline at end of file")

        expected_indent = rules.get("indent_style", "space")
        max_len = int(rules.get("max_line_length", "120"))

        for idx, line in enumerate(lines, start=1):
            stripped_line = line.rstrip("\r\n")
            
            # Line length check
            if len(stripped_line) > max_len:
                violations.append(f"Line {idx} exceeds max line length ({len(stripped_line)} > {max_len})")

            # Trailing whitespace check
            if rules.get("trim_trailing_whitespace") == "true":
                if stripped_line != stripped_line.rstrip(" \t"):
                    violations.append(f"Line {idx} has trailing whitespace")

            # Indentation check
            if stripped_line.startswith("\t") and expected_indent == "space":
                violations.append(f"Line {idx} uses tab indentation (expected spaces)")
            elif stripped_line.startswith("  ") and expected_indent == "tab":
                violations.append(f"Line {idx} uses space indentation (expected tabs)")

        return {
            "file": file_path,
            "total_lines": len(lines),
            "compliant": len(violations) == 0,
            "violations_count": len(violations),
            "violations": violations[:25],
        }
