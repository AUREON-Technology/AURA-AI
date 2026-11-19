import hashlib
from pathlib import Path
from typing import Any, Dict


class BinaryFileInspector:
    """Inspector for binary file size, SHA-256 digest, and executable/container header signatures."""

    @staticmethod
    def inspect(file_path: str) -> Dict[str, Any]:
        """Inspect a binary file on disk and return metadata and SHA-256 checksum."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {"exists": False, "error": f"Binary file not found: {file_path}"}

        size_bytes = path.stat().st_size
        digest = hashlib.sha256()
        header = b""

        with path.open("rb") as f:
            header = f.read(256)
            f.seek(0)
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                digest.update(chunk)

        format_type = "Generic Binary"
        if header.startswith(b"MZ"):
            format_type = "Windows PE Executable / DLL"
        elif header.startswith(b"\x7fELF"):
            format_type = "Linux ELF Binary"
        elif header.startswith((b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")):
            format_type = "macOS Mach-O Binary"
        elif header.startswith(b"\x00asm"):
            format_type = "WebAssembly (WASM)"

        return {
            "exists": True,
            "file_name": path.name,
            "file_path": str(path),
            "size_bytes": size_bytes,
            "size_kb": round(size_bytes / 1024.0, 2),
            "sha256": digest.hexdigest(),
            "format_type": format_type,
        }