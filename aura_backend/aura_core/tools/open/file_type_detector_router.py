"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. It opens paths and
``batch_detect_directory`` walks directories, on the gateway's own disk (KAN-34).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional


class FileTypeDetectorRouter:
    """Detector for identifying file types by magic bytes, extensions, and content, routing to suitable parsers."""

    @staticmethod
    def is_text_file(file_path_or_bytes: Any) -> bool:
        """Determine whether content is predominantly printable text rather than binary data."""
        if isinstance(file_path_or_bytes, (bytes, bytearray)):
            data = bytes(file_path_or_bytes[:1024])
        else:
            p = Path(str(file_path_or_bytes))
            if not p.exists() or not p.is_file():
                return False
            try:
                with p.open("rb") as f:
                    data = f.read(1024)
            except OSError:
                return False

        if not data:
            return True
        if b"\x00" in data:
            return False  # Null bytes strongly indicate binary

        text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
        non_text = data.translate(None, text_chars)
        return len(non_text) / len(data) < 0.20

    @staticmethod
    def detect_mime_from_extension(file_path: str) -> str:
        """Map file extension to standard MIME type."""
        ext = Path(file_path).suffix.lower()
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
        }
        if ext in custom_mappings:
            return custom_mappings[ext]
        guess, _ = mimetypes.guess_type(file_path)
        return guess or "application/octet-stream"

    @classmethod
    def detect_and_route(
        cls,
        file_path: Optional[str] = None,
        header_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Inspect header bytes or file prefix to determine MIME type and recommended parser."""
        data = bytes(header_bytes) if header_bytes is not None else b""
        p = Path(file_path) if file_path else None

        if not data and p and p.exists() and p.is_file():
            try:
                with p.open("rb") as f:
                    data = f.read(1024)
            except OSError:
                data = b""

        mime_type = "application/octet-stream"
        recommended_parser = "binary_file_inspector"
        confidence = 0.40

        # 1. Magic bytes signatures
        if data.startswith(b"%PDF-"):
            mime_type = "application/pdf"
            recommended_parser = "document_parser"
            confidence = 0.98
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime_type = "image/png"
            recommended_parser = "ui_visual_verifier"
            confidence = 0.98
        elif data.startswith((b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xdb")):
            mime_type = "image/jpeg"
            recommended_parser = "ui_visual_verifier"
            confidence = 0.98
        elif data.startswith(b"PK\x03\x04"):
            # Could be a zip or an office doc / jar
            if p and p.suffix.lower() in (".docx", ".xlsx", ".pptx"):
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
            if p and p.suffix.lower() == ".ipynb":
                mime_type = "application/x-ipynb+json"
                recommended_parser = "notebook_reader"
                confidence = 0.98
            elif stripped.startswith((b"{", b"[")):
                mime_type = "application/json"
                recommended_parser = "structured_data_parser"
                confidence = 0.88
            elif stripped.startswith(b"<?xml") or (stripped.startswith(b"<") and b">" in stripped):
                mime_type = "application/xml"
                recommended_parser = "structured_data_parser"
                confidence = 0.85
            elif b"def " in data or b"class " in data or b"import " in data:
                mime_type = "text/x-python"
                recommended_parser = "ast_code_analyzer"
                confidence = 0.90
            elif b"function " in data or b"const " in data or b"export " in data:
                mime_type = "text/javascript"
                recommended_parser = "multi_language_ast_analyzer"
                confidence = 0.80
            elif b"SELECT " in data.upper() or b"CREATE TABLE" in data.upper():
                mime_type = "application/x-sql"
                recommended_parser = "database_intelligence_tool"
                confidence = 0.85
            else:
                mime_type = cls.detect_mime_from_extension(str(p)) if p else "text/plain"
                recommended_parser = "source_content_explainer"
                confidence = 0.70
        else:
            if p:
                mime_type = cls.detect_mime_from_extension(str(p))

        return {
            "file_path": str(p) if p else None,
            "bytes_inspected": len(data),
            "mime_type": mime_type,
            "recommended_parser": recommended_parser,
            "confidence": round(confidence, 2),
            "is_text": cls.is_text_file(data),
        }

    @classmethod
    def batch_detect_directory(
        cls,
        directory_path: str,
        max_files: int = 200,
    ) -> Dict[str, Any]:
        """Scan a directory and categorize all files by their routed parsers."""
        root = Path(directory_path)
        if not root.exists() or not root.is_dir():
            return {"error": f"Directory not found: {directory_path}", "files": {}}

        parser_groups: Dict[str, List[str]] = {}
        total_scanned = 0

        for path in root.rglob("*"):
            if any(part in {".git", "__pycache__", "venv", ".venv", "node_modules"} for part in path.parts):
                continue
            if path.is_file():
                total_scanned += 1
                route = cls.detect_and_route(file_path=str(path))
                target = route["recommended_parser"]
                parser_groups.setdefault(target, []).append(str(path.relative_to(root)))
                if total_scanned >= max_files:
                    break

        return {
            "directory": str(root.resolve()),
            "total_scanned": total_scanned,
            "parser_breakdown": {k: len(v) for k, v in parser_groups.items()},
            "grouped_files": parser_groups,
        }
