"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Every method opens a
filesystem path, which on the gateway is the gateway's own disk (KAN-34).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import hashlib
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


class BinaryFileInspector:
    """Inspector for binary file size, multi-hash digests, header signatures, strings, and entropy."""

    @staticmethod
    def detect_format_signature(header: bytes) -> str:
        """Identify binary container or executable format from byte header signatures."""
        if header.startswith(b"MZ"):
            return "Windows PE Executable / DLL"
        elif header.startswith(b"\x7fELF"):
            return "Linux ELF Binary"
        elif header.startswith((b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf")):
            return "macOS Mach-O Binary"
        elif header.startswith(b"\x00asm"):
            return "WebAssembly (WASM)"
        elif header.startswith(b"%PDF-"):
            return "PDF Document"
        elif header.startswith(b"SQLite format 3\x00"):
            return "SQLite Database"
        elif header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "PNG Image"
        elif header.startswith((b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xdb")):
            return "JPEG Image"
        elif header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
            return "GIF Image"
        elif header.startswith(b"PK\x03\x04"):
            return "ZIP / JAR Archive"
        elif header.startswith(b"\x1f\x8b\x08"):
            return "GZIP Compressed Archive"
        elif header.startswith(b"7z\xbc\xaf\x27\x1c"):
            return "7-Zip Archive"
        elif header.startswith(b"\x25\x21\x50\x53"):
            return "PostScript Document"
        elif len(header) >= 12 and header[4:8] == b"ftyp":
            return "MP4 / ISO Media Container"
        return "Generic Binary"

    @classmethod
    def inspect(cls, file_path: str) -> Dict[str, Any]:
        """Inspect a binary file on disk and return metadata, hashes, and format."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {"exists": False, "error": f"Binary file not found: {file_path}"}

        size_bytes = path.stat().st_size
        sha256 = hashlib.sha256()
        md5 = hashlib.md5()
        header = b""

        with path.open("rb") as f:
            header = f.read(256)
            f.seek(0)
            while chunk := f.read(65536):
                sha256.update(chunk)
                md5.update(chunk)

        fmt = cls.detect_format_signature(header)
        is_exec = any(kw in fmt for kw in ("Executable", "ELF", "Mach-O", "WASM"))

        return {
            "exists": True,
            "file_name": path.name,
            "file_path": str(path.resolve()),
            "size_bytes": size_bytes,
            "size_kb": round(size_bytes / 1024.0, 2),
            "sha256": sha256.hexdigest(),
            "md5": md5.hexdigest(),
            "format_type": fmt,
            "is_executable": is_exec,
        }

    @staticmethod
    def compute_multiple_hashes(
        file_path: str,
        algorithms: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compute cryptographic hashes in parallel streams without loading large files to memory."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {"error": f"File not found: {file_path}"}

        algos = algorithms or ["md5", "sha1", "sha256", "sha512"]
        hashers = {}
        for a in algos:
            try:
                hashers[a] = hashlib.new(a)
            except ValueError:
                pass

        with path.open("rb") as f:
            while chunk := f.read(65536):
                for h in hashers.values():
                    h.update(chunk)

        return {
            "file_path": str(path),
            "size_bytes": path.stat().st_size,
            "digests": {k: v.hexdigest() for k, v in hashers.items()},
        }

    @staticmethod
    def extract_printable_strings(
        file_path: str,
        min_length: int = 4,
        max_count: int = 100,
    ) -> List[str]:
        """Extract ASCII and UTF-8 strings embedded inside a binary file."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return []

        strings: List[str] = []
        current: List[str] = []

        with path.open("rb") as f:
            data = f.read(524288)  # Sample first 512 KB
            for b in data:
                # Printable ASCII range 32 to 126
                if 32 <= b <= 126:
                    current.append(chr(b))
                else:
                    if len(current) >= min_length:
                        strings.append("".join(current))
                        if len(strings) >= max_count:
                            return strings
                    current = []

        if len(current) >= min_length and len(strings) < max_count:
            strings.append("".join(current))
        return strings

    @staticmethod
    def calculate_entropy(file_path: str) -> Dict[str, Any]:
        """Calculate Shannon entropy [0.0 - 8.0] to detect packed, compressed, or encrypted content."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {"error": f"File not found: {file_path}"}

        freq: Dict[int, int] = {}
        total_bytes = 0

        with path.open("rb") as f:
            while chunk := f.read(65536):
                total_bytes += len(chunk)
                for b in chunk:
                    freq[b] = freq.get(b, 0) + 1

        if total_bytes == 0:
            return {"entropy": 0.0, "total_bytes": 0, "assessment": "Empty File"}

        entropy = 0.0
        for count in freq.values():
            p = count / total_bytes
            entropy -= p * math.log2(p)

        entropy = round(entropy, 4)
        if entropy > 7.2:
            assessment = "High Entropy (Likely Compressed, Packed, or Encrypted)"
        elif entropy > 5.0:
            assessment = "Normal Code / Mixed Binary Content"
        else:
            assessment = "Low Entropy (Sparse or Text-heavy Content)"

        return {
            "entropy": entropy,
            "total_bytes": total_bytes,
            "assessment": assessment,
        }
