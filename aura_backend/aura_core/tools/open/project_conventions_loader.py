"""Discover, read and check a project's convention files (AGENTS.md, .editorconfig, ...).

Hardening notes (review of 2026-11):

* ``load_conventions(candidate_files=...)`` joined caller-supplied names onto the workspace without
  checking where they landed, so ``["../../etc/passwd"]`` or an absolute path read any file the
  gateway could. Every candidate must now resolve **inside** the workspace (symlinks included) or it
  is reported under ``errors``. ``discover_convention_files(extra_patterns=...)`` is confined the same way.
* Files are read through ``_input`` (UTF-8 with BOM, Latin-1 fallback instead of silently *deleting*
  undecodable bytes, 10 MB cap). A **PDF** among the candidates is read in full, every page; its
  coverage is under ``input[<file>]`` and any scanned page is named in ``warnings``.
* Truncation to ``max_chars_per_file`` is no longer silent: ``truncated_files`` lists each cut file
  with the characters kept and the total, and ``warnings`` says so. A PDF is *not* cut by default
  (``max_pdf_chars=None``): a rule on page 40 is as binding as one on page 1.
* ``parse_editorconfig`` took ``Path(content).exists()`` on the raw text, which raised
  ``OSError: File name too long`` for any editorconfig over ~255 characters. Text containing a
  newline is content; only a single-line string is tried as a path.
* ``validate_file_against_conventions`` no longer raises ``ValueError`` on ``max_line_length = off``
  (valid editorconfig) and no longer counts form-feed or U+2028 as line breaks.
* ``extract_agent_rules`` ignores ``#`` lines inside code fences, is linear in the input, and accepts
  bytes (a PDF's bytes are read in full); ``extract_agent_rules_file`` reads a path.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._input import SourceReadError, read_source_bytes, read_source_file
from ._time_budget import DEFAULT_BUDGET_S, Deadline

_SKIP_DIRS = (".git", "venv", ".venv", "node_modules", "__pycache__")


def _split_lines(text: str) -> List[str]:
    """Lines as an editor counts them: ``\\n``, ``\\r\\n`` and ``\\r`` only (not form feed, U+2028, ...)."""
    parts = re.split(r"\r\n|\r|\n", text)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


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
        max_chars_per_file: Optional[int] = 4000,
        max_pdf_chars: Optional[int] = None,
        time_budget_s: float = DEFAULT_BUDGET_S,
    ) -> Dict[str, Any]:
        """Read convention and configuration files present at workspace root.

        Only files inside the workspace are read. ``max_chars_per_file=None`` disables the cut for
        text files; ``max_pdf_chars`` (default none) is the separate cut for PDFs. Cut files are
        listed under ``truncated_files``. ``time_budget_s`` (default 300, at most 600) is checked between
        files; files not reached are named under ``errors`` and ``timed_out`` is true.
        """
        root = Path(workspace_path) if isinstance(workspace_path, (str, Path)) and str(workspace_path) else None
        try:
            ok = root is not None and root.exists() and root.is_dir()
        except (OSError, ValueError):
            ok = False
        if root is None or not ok:
            return {
                "workspace": str(workspace_path),
                "exists": False,
                "rules_loaded": False,
                "error": f"Workspace directory not found: {workspace_path}",
                "conventions": {},
            }

        if candidate_files is None:
            targets = list(cls.DEFAULT_CONVENTION_FILES)
        elif isinstance(candidate_files, str):
            targets = [candidate_files]
        else:
            targets = [t for t in candidate_files if isinstance(t, str)]
        conventions: Dict[str, str] = {}
        errors: Dict[str, str] = {}
        inputs: Dict[str, Any] = {}
        truncated: Dict[str, Dict[str, int]] = {}
        warnings: List[str] = []
        deadline = Deadline(time_budget_s)
        timed_out = False

        for filename in targets:
            if deadline.expired():
                timed_out = True
                errors[str(filename)] = "Not read: time budget used up."
                continue
            if not filename or "\x00" in filename:
                errors[str(filename)] = "Invalid file name."
                continue
            fpath = root / filename
            try:
                if not fpath.exists() or not fpath.is_file():
                    continue
            except (OSError, ValueError):
                continue
            if not _within(root, fpath):
                errors[filename] = "Path is outside the workspace."
                continue
            try:
                read = read_source_file(fpath, base_dir=root)
            except SourceReadError as exc:
                errors[filename] = str(exc)
                continue

            limit = max_pdf_chars if read.kind == "pdf" else max_chars_per_file
            text = read.text
            if isinstance(limit, int) and limit >= 0 and len(text) > limit:
                truncated[filename] = {"chars_returned": limit, "chars_total": len(text)}
                warnings.append(
                    f"{filename}: returned only the first {limit} of {len(text)} characters; "
                    "raise max_chars_per_file (or pass None) to read all of it."
                )
                text = text[:limit]
            conventions[filename] = text
            if read.kind == "pdf":
                inputs[filename] = read.describe()
                warnings.extend(f"{filename}: {w}" for w in read.warnings())

        return {
            "workspace": str(root),
            "exists": True,
            "rules_loaded": len(conventions) > 0,
            "discovered_files": list(conventions.keys()),
            "conventions": conventions,
            "errors": errors,
            "truncated_files": truncated,
            "input": inputs,
            "timed_out": timed_out,
            "warnings": warnings + ([deadline.message("unread files are listed under errors.")] if timed_out else []),
        }

    @classmethod
    def discover_convention_files(
        cls,
        workspace_path: str,
        search_subdirs: bool = False,
        extra_patterns: Optional[List[str]] = None,
        max_depth: int = 8,
    ) -> List[Dict[str, Any]]:
        """Discover all convention, rule, and style config files across the workspace.

        With ``search_subdirs`` the walk goes at most ``max_depth`` directories below the root
        (symlinked directories are not followed).
        """
        try:
            root = Path(workspace_path)
            if not root.exists() or not root.is_dir():
                return []
        except (OSError, ValueError, TypeError):
            return []

        results: List[Dict[str, Any]] = []
        known_names = set(cls.DEFAULT_CONVENTION_FILES)
        if extra_patterns:
            known_names.update(p for p in extra_patterns if isinstance(p, str) and p and "\x00" not in p)

        if search_subdirs:
            depth_cap = max_depth if isinstance(max_depth, int) and max_depth >= 0 else 8
            for current_root, dirs, files in os.walk(root):
                depth = len(Path(current_root).relative_to(root).parts)
                dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS) if depth < depth_cap else []
                for f in sorted(files):
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
                try:
                    if p.is_file() and _within(root, p):
                        st = p.stat()
                        results.append({
                            "filename": f,
                            "relative_path": f,
                            "size_bytes": st.st_size,
                            "category": cls._categorize_filename(f),
                        })
                except (OSError, ValueError):
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
    def parse_editorconfig(
        editorconfig_path_or_content: str,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Parse .editorconfig syntax into section glob mapping to formatting rules.

        The argument is the file's *content*, or a single-line path to read it from. Returns ``{}``
        for anything unusable.
        """
        raw_text = editorconfig_path_or_content
        if not isinstance(raw_text, str):
            return {}
        if "\n" not in raw_text and "\x00" not in raw_text and raw_text.strip():
            try:
                p = Path(raw_text)
                is_file = p.is_file()
            except (OSError, ValueError):
                is_file = False
            if is_file:
                try:
                    raw_text = read_source_file(p, base_dir=base_dir).text
                except SourceReadError:
                    return {}

        sections: Dict[str, Dict[str, str]] = {}
        current_section = "*"

        for line in _split_lines(raw_text):
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
    def extract_agent_rules(markdown_content: Union[str, bytes]) -> Dict[str, Any]:
        """Extract explicit DO, DON'T, MUST, NEVER constraints and checklist commands from markdown rules.

        ``markdown_content`` may be bytes; a PDF's bytes are read in full (every page) first.
        """
        info: Optional[Dict[str, Any]] = None
        if isinstance(markdown_content, (bytes, bytearray)):
            try:
                read = read_source_bytes(bytes(markdown_content))
            except SourceReadError as exc:
                return {"error": str(exc), "section_count": 0, "sections": [], "must_do_directives": [],
                        "forbidden_directives": [], "common_commands": []}
            markdown_content = read.text
            info = {"input": read.describe(), "warnings": read.warnings()}
        if not isinstance(markdown_content, str):
            return {"error": "markdown_content must be text or bytes", "section_count": 0, "sections": [],
                    "must_do_directives": [], "forbidden_directives": [], "common_commands": []}

        dos: Dict[str, None] = {}
        donts: Dict[str, None] = {}
        commands: Dict[str, None] = {}
        sections: List[str] = []
        in_fence = False

        for line in _split_lines(markdown_content):
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if stripped.startswith("#"):
                sections.append(stripped.lstrip("#").strip())
                continue

            # Command extraction (e.g. `pytest ...` or $ command)
            cmd_match = re.search(r"`([^`]+)`", stripped)
            if cmd_match and any(tok in cmd_match.group(1) for tok in ("pytest", "git", "pip", "npm", "python", "make")):
                if len(commands) < 20:
                    commands.setdefault(cmd_match.group(1), None)

            negated = re.search(r"\b(never|don't|not)\b", stripped, re.IGNORECASE)
            # DO / MUST constraints
            if len(dos) < 30 and re.search(r"\b(must|always|ensure|do\b|required)\b", stripped, re.IGNORECASE):
                cleaned = re.sub(r"^[-*0-9.]+\s*", "", stripped)
                if cleaned and not negated:
                    dos.setdefault(cleaned, None)

            # DON'T / NEVER constraints
            if len(donts) < 30 and re.search(r"\b(never|don't|do not|must not|forbidden|avoid)\b", stripped, re.IGNORECASE):
                cleaned = re.sub(r"^[-*0-9.]+\s*", "", stripped)
                if cleaned:
                    donts.setdefault(cleaned, None)

        result: Dict[str, Any] = {
            "section_count": len(sections),
            "sections": sections,
            "must_do_directives": list(dos)[:30],
            "forbidden_directives": list(donts)[:30],
            "common_commands": list(commands)[:20],
        }
        if info:
            result["input"] = info["input"]
            if info["warnings"]:
                result["warnings"] = info["warnings"]
        return result

    @classmethod
    def extract_agent_rules_file(cls, file_path: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """:meth:`extract_agent_rules` on a file. A PDF is read in full (all pages) first."""
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"error": str(exc), "section_count": 0, "sections": [], "must_do_directives": [],
                    "forbidden_directives": [], "common_commands": []}
        result = cls.extract_agent_rules(read.text)
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = read.warnings()
        return result

    @staticmethod
    def validate_file_against_conventions(
        file_path: str,
        file_content: str,
        editorconfig_rules: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Verify basic syntactic conventions (indentation style, trailing whitespace, final newline).

        ``max_line_length = off`` (or any non-number) disables the length check. Violations are capped
        at 25 in ``violations``; ``violations_count`` is the true total.
        """
        if isinstance(file_content, (bytes, bytearray)):
            data = bytes(file_content)
            try:
                read = read_source_bytes(data)
            except SourceReadError as exc:
                return {"file": file_path, "compliant": False, "error": str(exc), "violations_count": 0, "violations": []}
            if read.kind == "pdf":
                return {
                    "file": file_path,
                    "compliant": False,
                    "error": (
                        f"The PDF's {read.pages} page(s) were read in full, but whitespace and indentation "
                        "conventions cannot be checked on text extracted from a PDF."
                    ),
                    "input": read.describe(),
                    "violations_count": 0,
                    "violations": [],
                }
            file_content = read.text
        if not isinstance(file_content, str):
            return {"file": file_path, "compliant": False, "error": "file_content must be text or bytes",
                    "violations_count": 0, "violations": []}

        rules = {str(k): str(v).strip().lower() for k, v in (editorconfig_rules or {}).items()} or {
            "indent_style": "space",
            "indent_size": "4",
            "insert_final_newline": "true",
            "trim_trailing_whitespace": "true",
            "max_line_length": "120",
        }

        violations: List[str] = []
        lines = _split_lines(file_content)

        # Check final newline
        if rules.get("insert_final_newline") == "true":
            if file_content and not file_content.endswith(("\n", "\r\n", "\r")):
                violations.append("Missing final newline at end of file")

        expected_indent = rules.get("indent_style", "space")
        try:
            max_len: Optional[int] = int(rules.get("max_line_length", "120"))
        except ValueError:
            max_len = None  # "off", "unset", ...

        for idx, stripped_line in enumerate(lines, start=1):
            # Line length check
            if max_len is not None and len(stripped_line) > max_len:
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
            "violations_truncated": len(violations) > 25,
        }
