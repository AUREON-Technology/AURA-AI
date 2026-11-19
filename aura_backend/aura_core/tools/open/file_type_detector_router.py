from pathlib import Path
from typing import Any, Dict, Optional


class FileTypeDetectorRouter:
    """Detector for identifying file types by magic bytes and content headers and routing parsers."""

    @staticmethod
    def detect_and_route(
        file_path: Optional[str] = None,
        header_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Inspect header bytes or file prefix to determine MIME type and recommended parser."""
        data = bytes(header_bytes) if header_bytes is not None else b""
        if not data and file_path:
            p = Path(file_path)
            if p.exists() and p.is_file():
                try:
                    with p.open("rb") as f:
                        data = f.read(512)
                except OSError:
                    data = b""

        mime_type = "application/octet-stream"
        recommended_parser = "binary_file_inspector"
        confidence = 0.4

        if data.startswith(b"%PDF-"):
            mime_type = "application/pdf"
            recommended_parser = "document_parser"
            confidence = 0.98
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime_type = "image/png"
            recommended_parser = "ui_visual_verifier"
            confidence = 0.98
        elif data.startswith(b"PK\x03\x04"):
            mime_type = "application/zip"
            recommended_parser = "archive_extractor"
            confidence = 0.95
        elif data.startswith(b"SQLite format 3\x00"):
            mime_type = "application/x-sqlite3"
            recommended_parser = "database_intelligence_tool"
            confidence = 0.99
        else:
            stripped = data.lstrip()
            if stripped.startswith((b"{", b"[")):
                mime_type = "application/json"
                recommended_parser = "structured_data_parser"
                confidence = 0.85
            elif b"def " in data or b"class " in data or b"import " in data:
                mime_type = "text/x-python"
                recommended_parser = "ast_code_analyzer"
                confidence = 0.85

        return {
            "file_path": file_path,
            "bytes_inspected": len(data),
            "mime_type": mime_type,
            "recommended_parser": recommended_parser,
            "confidence": confidence,
        }