"""File-type detection and routing to the parser best suited to the content.

Hardening notes (review of 2026-11):

* A **PDF is a PDF**, not text: ``is_text_file`` used to say ``True`` for any PDF whose first KiB was
  ASCII. ``detect_and_route`` now identifies it (``application/pdf``) and, when given a path, reads it
  in full through ``_input`` and reports ``pages``, ``readable_pages``, ``unreadable_pages`` (scans
  that need OCR) and an ``input`` coverage block, plus ``warnings``. With only header bytes the pages
  cannot be inspected and the result says so.
* Never raises: ``None``, non-string paths, a directory, a file without read permission, a path with a
  NUL byte all give a result.
* Source-language guesses are anchored to line starts / the file extension rather than matching the
  words "import " or "def " anywhere (which called English prose "Python").
* ``batch_detect_directory`` prunes ignored directories while walking (it used to descend into
  ``node_modules`` first), judges "ignored" by the path *below* the root (a repository living under
  ``/work/build/`` was skipped entirely), does not follow symlinks, and says when it stopped at
  ``max_files``.
"""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._input import SourceReadError, is_pdf_bytes, read_source_file
from ._time_budget import DEFAULT_BUDGET_S, Deadline

_IGNORED_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules"}

_PY_RE = re.compile(rb"(?m)^[ \t]*(?:def\s+\w+\s*\(|class\s+\w+\s*[(:]|import\s+[\w.]+|from\s+[\w.]+\s+import\s)")
_JS_RE = re.compile(
    rb"(?m)^[ \t]*(?:export\s+|(?:async\s+)?function\s*\*?\s*\w*\s*\(|(?:const|let|var)\s+\w+\s*=|import\s.+\sfrom\s)"
)
_SQL_RE = re.compile(
    rb"(?mi)^[ \t]*(?:select\s.+\sfrom\s|create\s+(?:or\s+replace\s+)?table\s|insert\s+into\s|update\s+\w+\s+set\s|alter\s+table\s)"
)
_JSON_RE = re.compile(rb'^\s*(?:\{\s*(?:"|\})|\[\s*(?:["{\[\]\d-]|true\b|false\b|null\b))')

_EXT_ROUTES: Dict[str, "tuple[str, str]"] = {
    ".py": ("text/x-python", "ast_code_analyzer"),
    ".js": ("text/javascript", "multi_language_ast_analyzer"),
    ".mjs": ("text/javascript", "multi_language_ast_analyzer"),
    ".jsx": ("text/javascript", "multi_language_ast_analyzer"),
    ".ts": ("text/typescript", "multi_language_ast_analyzer"),
    ".tsx": ("text/tsx", "multi_language_ast_analyzer"),
    ".dart": ("text/x-dart", "multi_language_ast_analyzer"),
    ".go": ("text/x-go", "multi_language_ast_analyzer"),
    ".rs": ("text/rust", "multi_language_ast_analyzer"),
    ".sql": ("application/x-sql", "database_intelligence_tool"),
    ".json": ("application/json", "structured_data_parser"),
    ".yaml": ("application/x-yaml", "structured_data_parser"),
    ".yml": ("application/x-yaml", "structured_data_parser"),
    ".toml": ("application/toml", "structured_data_parser"),
    ".xml": ("application/xml", "structured_data_parser"),
}


def _as_path(value: Any) -> Optional[Path]:
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value and "\x00" not in value:
        try:
            return Path(value)
        except (OSError, ValueError):
            return None
    return None


def _inside(path: Path, base_dir: Any) -> bool:
    if base_dir is None:
        return True
    try:
        path.resolve().relative_to(Path(base_dir).resolve())
        return True
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


class FileTypeDetectorRouter:
    """Detector for identifying file types by magic bytes, extensions, and content, routing to suitable parsers."""

    @staticmethod
    def is_text_file(file_path_or_bytes: Any) -> bool:
        """Determine whether content is predominantly printable text rather than binary data.

        A PDF is never text, whatever its first KiB happens to contain.
        """
        if isinstance(file_path_or_bytes, (bytes, bytearray)):
            data = bytes(file_path_or_bytes[:1100])
        else:
            p = _as_path(file_path_or_bytes if isinstance(file_path_or_bytes, (str, Path)) else str(file_path_or_bytes))
            if p is None:
                return False
            try:
                if not p.exists() or not p.is_file():
                    return False
                with p.open("rb") as f:
                    data = f.read(1100)
            except (OSError, ValueError):
                return False

        if not data:
            return True
        if is_pdf_bytes(data):
            return False
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):  # UTF-16 BOM: text full of NULs
            return True
        data = data[:1024]
        if b"\x00" in data:
            return False  # Null bytes strongly indicate binary

        text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
        non_text = data.translate(None, text_chars)
        return len(non_text) / len(data) < 0.20

    @staticmethod
    def detect_mime_from_extension(file_path: str) -> str:
        """Map file extension to standard MIME type."""
        if not isinstance(file_path, (str, Path)) or not str(file_path):
            return "application/octet-stream"
        try:
            ext = Path(file_path).suffix.lower()
        except (OSError, ValueError):
            return "application/octet-stream"
        custom_mappings = {
            ".py": "text/x-python",
            ".ts": "text/typescript",
            ".tsx": "text/tsx",
            ".dart": "text/x-dart",
            ".rs": "text/rust",
            ".go": "text/x-go",
            ".toml": "application/toml",
            ".yaml": "application/x-yaml",
            ".yml": "application/x-yaml",
            ".md": "text/markdown",
            ".sql": "application/x-sql",
            ".ipynb": "application/x-ipynb+json",
            ".pdf": "application/pdf",
        }
        if ext in custom_mappings:
            return custom_mappings[ext]
        try:
            guess, _ = mimetypes.guess_type(str(file_path))
        except (ValueError, TypeError):
            guess = None
        return guess or "application/octet-stream"

    @classmethod
    def detect_and_route(
        cls,
        file_path: Optional[str] = None,
        header_bytes: Optional[bytes] = None,
        inspect_pdf: bool = True,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect header bytes or file prefix to determine MIME type and recommended parser.

        For a PDF with a ``file_path``, the whole document is read (``inspect_pdf=True``) and the
        result carries ``pages`` / ``readable_pages`` / ``unreadable_pages`` / ``input`` / ``warnings``.
        """
        if isinstance(header_bytes, str):
            header_bytes = header_bytes.encode("utf-8", errors="replace")
        data = bytes(header_bytes) if isinstance(header_bytes, (bytes, bytearray)) else b""
        p = _as_path(file_path) if file_path else None
        if file_path and p is None:
            return {
                "file_path": str(file_path),
                "bytes_inspected": 0,
                "mime_type": "application/octet-stream",
                "recommended_parser": "binary_file_inspector",
                "confidence": 0.0,
                "is_text": False,
                "error": f"Invalid path: {file_path!r}",
            }
        if p is not None and not _inside(p, base_dir):
            return {
                "file_path": str(p),
                "bytes_inspected": 0,
                "mime_type": "application/octet-stream",
                "recommended_parser": "binary_file_inspector",
                "confidence": 0.0,
                "is_text": False,
                "error": f"Path is outside the allowed directory: {file_path}",
            }

        read_error: Optional[str] = None
        if not data and p is not None:
            try:
                if p.is_file():
                    with p.open("rb") as f:
                        data = f.read(1100)
                elif p.exists():
                    read_error = f"Path is not a file: {file_path}"
                else:
                    read_error = f"File not found: {file_path}"
            except (OSError, ValueError) as exc:
                read_error = f"Cannot read file: {getattr(exc, 'strerror', None) or type(exc).__name__}"
                data = b""

        mime_type = "application/octet-stream"
        recommended_parser = "binary_file_inspector"
        confidence = 0.40
        suffix = p.suffix.lower() if p else ""
        is_pdf = is_pdf_bytes(data) if data else False

        # 1. Magic bytes signatures
        if is_pdf:
            mime_type = "application/pdf"
            recommended_parser = "document_parser"
            confidence = 0.98
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime_type = "image/png"
            recommended_parser = "ui_visual_verifier"
            confidence = 0.98
        elif data.startswith(b"\xff\xd8\xff"):
            mime_type = "image/jpeg"
            recommended_parser = "ui_visual_verifier"
            confidence = 0.98
        elif data.startswith(b"PK\x03\x04"):
            # Could be a zip or an office doc / jar
            if suffix in (".docx", ".xlsx", ".pptx"):
                mime_type = "application/vnd.openxmlformats"
                recommended_parser = "document_parser"
            else:
                mime_type = "application/zip"
                recommended_parser = "archive_extractor"
            confidence = 0.95
        elif data.startswith(b"SQLite format 3\x00"):
            mime_type = "application/x-sqlite3"
            recommended_parser = "database_intelligence_tool"
            confidence = 0.99
        elif data.startswith((b"GIF87a", b"GIF89a")):
            mime_type = "image/gif"
            recommended_parser = "ui_visual_verifier"
            confidence = 0.95
        elif data.startswith(b"\x1f\x8b\x08"):
            mime_type = "application/gzip"
            recommended_parser = "archive_extractor"
            confidence = 0.95

        # 2. Text / Source Inspection
        elif cls.is_text_file(data):
            stripped = data.lstrip()
            if suffix == ".ipynb":
                mime_type = "application/x-ipynb+json"
                recommended_parser = "notebook_reader"
                confidence = 0.98
            elif suffix in _EXT_ROUTES:
                mime_type, recommended_parser = _EXT_ROUTES[suffix]
                confidence = 0.92
            elif _JSON_RE.match(stripped):
                mime_type = "application/json"
                recommended_parser = "structured_data_parser"
                confidence = 0.88
            elif stripped.startswith(b"<?xml") or re.match(rb"<[A-Za-z!?/][^>]*>", stripped):
                mime_type = "application/xml"
                recommended_parser = "structured_data_parser"
                confidence = 0.85
            elif _PY_RE.search(data):
                mime_type = "text/x-python"
                recommended_parser = "ast_code_analyzer"
                confidence = 0.90
            elif _JS_RE.search(data):
                mime_type = "text/javascript"
                recommended_parser = "multi_language_ast_analyzer"
                confidence = 0.80
            elif _SQL_RE.search(data):
                mime_type = "application/x-sql"
                recommended_parser = "database_intelligence_tool"
                confidence = 0.85
            else:
                mime_type = cls.detect_mime_from_extension(str(p)) if p else "text/plain"
                if mime_type == "application/octet-stream":
                    mime_type = "text/plain"
                recommended_parser = "source_content_explainer"
                confidence = 0.70
        else:
            if p:
                mime_type = cls.detect_mime_from_extension(str(p))

        result: Dict[str, Any] = {
            "file_path": str(p) if p else None,
            "bytes_inspected": len(data),
            "mime_type": mime_type,
            "recommended_parser": recommended_parser,
            "confidence": round(confidence, 2),
            "is_text": cls.is_text_file(data),
        }
        if read_error:
            result["error"] = read_error

        if is_pdf:
            result["is_pdf"] = True
            if p is not None and p.is_file() and inspect_pdf and not header_bytes:
                try:
                    read = read_source_file(p, base_dir=base_dir)
                except SourceReadError as exc:
                    result["pdf_error"] = str(exc)
                    result["warnings"] = [f"The PDF could not be read: {exc}"]
                else:
                    info = read.describe()
                    result["pages"] = read.pages
                    result["readable_pages"] = info["pages_read"]
                    result["unreadable_pages"] = info["unreadable_pages"]
                    result["blank_pages"] = info["blank_pages"]
                    result["input"] = info
                    if read.warnings():
                        result["warnings"] = read.warnings()
            elif p is None or not p.is_file() or header_bytes:
                result["warnings"] = [
                    "Only header bytes were available; the PDF's pages were not inspected."
                ]
        return result

    @classmethod
    def batch_detect_directory(
        cls,
        directory_path: str,
        max_files: int = 200,
        inspect_pdfs: bool = False,
        base_dir: Optional[str] = None,
        time_budget_s: float = DEFAULT_BUDGET_S,
    ) -> Dict[str, Any]:
        """Scan a directory and categorize all files by their routed parsers.

        ``truncated`` is true when more than ``max_files`` files exist. PDFs are listed under
        ``pdf_files``; their pages are only counted when ``inspect_pdfs=True`` (reading a directory of
        large PDFs in full is slow), in which case ``pdf_coverage`` maps each to its ``input`` block.
        ``time_budget_s`` (default 300, at most 600) is checked between files; when it runs out the
        scan stops with ``timed_out`` and ``truncated`` set.
        """
        root = _as_path(directory_path)
        if root is None:
            return {"error": f"Invalid directory path: {directory_path!r}", "files": {}}
        if not _inside(root, base_dir):
            return {"error": f"Path is outside the allowed directory: {directory_path}", "files": {}}
        try:
            is_dir = root.is_dir()
        except OSError:
            is_dir = False
        if not is_dir:
            return {"error": f"Directory not found: {directory_path}", "files": {}}

        max_files = max_files if isinstance(max_files, int) and max_files > 0 else 200
        parser_groups: Dict[str, List[str]] = {}
        pdf_files: List[str] = []
        pdf_coverage: Dict[str, Any] = {}
        total_scanned = 0
        truncated = False
        timed_out = False
        deadline = Deadline(time_budget_s)

        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS)
            for name in sorted(names):
                full = Path(current) / name
                if total_scanned >= max_files:
                    truncated = True
                    break
                if deadline.expired():
                    truncated = timed_out = True
                    break
                if full.is_symlink() or not full.is_file():
                    continue
                total_scanned += 1
                rel = full.relative_to(root).as_posix()
                route = cls.detect_and_route(file_path=str(full), inspect_pdf=inspect_pdfs)
                parser_groups.setdefault(route["recommended_parser"], []).append(rel)
                if route.get("is_pdf"):
                    pdf_files.append(rel)
                    if "input" in route:
                        pdf_coverage[rel] = route["input"]
            if truncated:
                break

        result: Dict[str, Any] = {
            "directory": str(root.resolve()),
            "total_scanned": total_scanned,
            "truncated": truncated,
            "timed_out": timed_out,
            "parser_breakdown": {k: len(v) for k, v in parser_groups.items()},
            "grouped_files": parser_groups,
            "pdf_files": pdf_files,
            "pdf_coverage": pdf_coverage,
        }
        if timed_out:
            result["warnings"] = [deadline.message(f"{total_scanned} file(s) were classified; the rest were not.")]
        elif truncated:
            result["warnings"] = [f"Stopped after {max_files} files; the directory holds more."]
        if pdf_files and not inspect_pdfs:
            result.setdefault("warnings", []).append(
                f"{len(pdf_files)} PDF(s) were identified but their pages were not read; "
                "call detect_and_route(file_path) on one, or pass inspect_pdfs=True, for page coverage."
            )
        return result
