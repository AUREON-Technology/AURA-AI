"""Binary file inspection: size, digests, header signature, printable strings and entropy.

Hardening notes (review of 2026-11):

* Every method returns a result (dict / list) for every input: a directory, a file without read
  permission, a path that is not a string, an unknown hash algorithm -- none raises.
* A **PDF is identified as a PDF** and, because it is a document and not an opaque blob, ``inspect``
  also reads it in full (every page, through ``_input``) and reports ``pages``, ``readable_pages``,
  ``unreadable_pages`` (scans that need OCR) and ``blank_pages`` under ``input`` / ``pdf``.
  :meth:`extract_printable_strings` returns the *text lines of the whole PDF* for a PDF, not the
  byte-soup of its compressed streams.
* ``MZ`` alone is not a Windows executable: a text file that begins with "MZ" used to be reported as
  PE. The PE header offset is now checked.
* Hashing, strings and entropy stream the file in chunks and refuse (rather than partially read) a
  file above ``max_bytes``; a hash or an entropy figure over "the first N bytes" would be wrong.
"""

from __future__ import annotations

import hashlib
import math
import mmap
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._input import SourceReadError, is_pdf_bytes, read_source_file

_CHUNK = 1024 * 1024
#: Files above this are refused by hashing / entropy rather than half-read. 2 GiB.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
#: ``extract_printable_strings`` scans at most this much of a non-PDF file (reported by the ``_report`` variant).
MAX_STRING_SCAN_BYTES = 256 * 1024 * 1024
_HEADER_BYTES = 1100  # enough for the 1 KiB a PDF header may hide in, and tar's "ustar" at offset 257

#: ``shake_*`` need an explicit digest length, so they cannot be used as plain digests here.
_UNSUPPORTED_ALGOS = {"shake_128", "shake_256"}


def _resolve(file_path: Any, base_dir: Any = None) -> "tuple[Optional[Path], Optional[str]]":
    """``(path, None)`` for a readable regular file, else ``(None, user-safe error)``."""
    if not isinstance(file_path, (str, Path)) or not str(file_path) or "\x00" in str(file_path):
        return None, f"Invalid path: {file_path!r}"
    try:
        path = Path(file_path).expanduser()
        if base_dir is not None:
            try:
                path.resolve().relative_to(Path(base_dir).resolve())
            except ValueError:
                return None, f"Path is outside the allowed directory: {file_path}"
        if not path.exists():
            return None, f"File not found: {file_path}"
        if not path.is_file():
            return None, f"Path is not a file: {file_path}"
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f"Invalid path ({type(exc).__name__}): {file_path}"
    return path, None


def _md5() -> Any:
    try:
        return hashlib.md5(usedforsecurity=False)
    except TypeError:  # pragma: no cover - very old OpenSSL builds
        return hashlib.md5()


class BinaryFileInspector:
    """Inspector for binary file size, multi-hash digests, header signatures, strings, and entropy."""

    @staticmethod
    def detect_format_signature(header: bytes) -> str:
        """Identify binary container or executable format from byte header signatures."""
        if not isinstance(header, (bytes, bytearray)):
            return "Generic Binary"
        header = bytes(header)
        if is_pdf_bytes(header):
            return "PDF Document"
        if header.startswith(b"MZ"):
            # "MZ" is also the first two letters of plenty of text; a real PE points at "PE\0\0".
            if len(header) >= 64:
                e_lfanew = int.from_bytes(header[60:64], "little")
                if e_lfanew < 0x10000 and (
                    e_lfanew + 4 > len(header) or header[e_lfanew : e_lfanew + 4] == b"PE\x00\x00"
                ):
                    return "Windows PE Executable / DLL"
            return "Generic Binary"
        if header.startswith(b"\x7fELF"):
            return "Linux ELF Binary"
        if header.startswith(b"\xca\xfe\xba\xbe"):
            # Same magic for Java class files (value = major version >= 45) and fat Mach-O (arch count).
            if len(header) >= 8 and int.from_bytes(header[4:8], "big") >= 45:
                return "Java Class File"
            return "macOS Mach-O Binary"
        if header.startswith((b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe")):
            return "macOS Mach-O Binary"
        if header.startswith(b"\x00asm"):
            return "WebAssembly (WASM)"
        if header.startswith(b"SQLite format 3\x00"):
            return "SQLite Database"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "PNG Image"
        if header.startswith(b"\xff\xd8\xff"):
            return "JPEG Image"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "GIF Image"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "WebP Image"
        if header.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            return "ZIP / JAR Archive"
        if header.startswith(b"\x1f\x8b\x08"):
            return "GZIP Compressed Archive"
        if header.startswith(b"7z\xbc\xaf\x27\x1c"):
            return "7-Zip Archive"
        if header.startswith(b"BZh"):
            return "BZIP2 Compressed Archive"
        if header.startswith(b"\xfd7zXZ\x00"):
            return "XZ Compressed Archive"
        if header.startswith(b"Rar!\x1a\x07"):
            return "RAR Archive"
        if header[257:262] == b"ustar":
            return "TAR Archive"
        if header.startswith(b"\x25\x21\x50\x53"):
            return "PostScript Document"
        if len(header) >= 12 and header[4:8] == b"ftyp":
            return "MP4 / ISO Media Container"
        return "Generic Binary"

    @classmethod
    def inspect(cls, file_path: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Inspect a file on disk and return metadata, hashes, and format.

        A PDF is additionally read in full: the result carries ``pages`` and an ``input`` coverage
        block (pages read, scanned pages that need OCR, blank pages) plus ``warnings``.
        """
        path, error = _resolve(file_path, base_dir)
        if path is None:
            return {"exists": False, "error": error}

        sha256 = hashlib.sha256()
        md5 = _md5()
        try:
            size_bytes = path.stat().st_size
            with path.open("rb") as handle:
                header = handle.read(_HEADER_BYTES)
                handle.seek(0)
                while chunk := handle.read(_CHUNK):
                    sha256.update(chunk)
                    md5.update(chunk)
        except OSError as exc:
            return {"exists": True, "error": f"Cannot read file: {exc.strerror or type(exc).__name__}"}

        fmt = cls.detect_format_signature(header)
        is_exec = any(kw in fmt for kw in ("Executable", "ELF", "Mach-O", "WASM"))

        result: Dict[str, Any] = {
            "exists": True,
            "file_name": path.name,
            "file_path": str(path.resolve()),
            "size_bytes": size_bytes,
            "size_kb": round(size_bytes / 1024.0, 2),
            "sha256": sha256.hexdigest(),
            "md5": md5.hexdigest(),
            "format_type": fmt,
            "is_executable": is_exec,
            "is_pdf": fmt == "PDF Document",
        }
        if fmt == "PDF Document":
            try:
                read = read_source_file(path)
            except SourceReadError as exc:
                result["pdf_error"] = str(exc)
                result["warnings"] = [f"The PDF could not be read: {exc}"]
            else:
                info = read.describe()
                result["pages"] = read.pages
                result["readable_pages"] = info["pages_read"]
                result["unreadable_pages"] = info["unreadable_pages"]
                result["blank_pages"] = info["blank_pages"]
                result["text_chars"] = len(read.text)
                result["input"] = info
                if read.warnings():
                    result["warnings"] = read.warnings()
        return result

    @staticmethod
    def compute_multiple_hashes(
        file_path: str,
        algorithms: Optional[List[str]] = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute cryptographic hashes in parallel streams without loading large files to memory.

        Unknown or unusable algorithm names are listed under ``unsupported_algorithms`` instead of
        being dropped silently.
        """
        path, error = _resolve(file_path, base_dir)
        if path is None:
            return {"error": error}

        if isinstance(algorithms, str):
            algorithms = [algorithms]
        algos = [a for a in algorithms] if algorithms else ["md5", "sha1", "sha256", "sha512"]
        hashers: Dict[str, Any] = {}
        unsupported: List[str] = []
        for a in algos:
            if not isinstance(a, str) or a.lower() in _UNSUPPORTED_ALGOS:
                unsupported.append(str(a))
                continue
            try:
                hashers[a] = hashlib.new(a, usedforsecurity=False)
            except (ValueError, TypeError):
                unsupported.append(a)

        try:
            size = path.stat().st_size
            if size > max_bytes:
                return {"error": f"File is too large to hash ({size} bytes; limit {max_bytes}): {file_path}"}
            if hashers:
                with path.open("rb") as handle:
                    while chunk := handle.read(_CHUNK):
                        for h in hashers.values():
                            h.update(chunk)
        except OSError as exc:
            return {"error": f"Cannot read file: {exc.strerror or type(exc).__name__}"}

        result: Dict[str, Any] = {
            "file_path": str(path),
            "size_bytes": size,
            "digests": {k: v.hexdigest() for k, v in hashers.items()},
            "unsupported_algorithms": unsupported,
        }
        if not hashers:
            result["error"] = f"No supported hash algorithm requested: {unsupported}"
        return result

    @classmethod
    def extract_printable_strings_report(
        cls,
        file_path: str,
        min_length: int = 4,
        max_count: int = 100,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Like :meth:`extract_printable_strings`, but says what it scanned and whether it stopped early.

        For a PDF the strings are the text lines of *every page* (``input`` carries the coverage).
        For any other file the whole file is scanned, up to ``MAX_STRING_SCAN_BYTES``.
        """
        path, error = _resolve(file_path, base_dir)
        if path is None:
            return {"strings": [], "error": error}
        min_length = min(min_length, 4096) if isinstance(min_length, int) and min_length > 0 else 4
        max_count = max_count if isinstance(max_count, int) and max_count > 0 else 100

        try:
            with path.open("rb") as handle:
                head = handle.read(_HEADER_BYTES)
        except OSError as exc:
            return {"strings": [], "error": f"Cannot read file: {exc.strerror or type(exc).__name__}"}

        if is_pdf_bytes(head):
            try:
                read = read_source_file(path)
            except SourceReadError as exc:
                return {"strings": [], "error": str(exc)}
            lines = [ln.strip() for ln in read.text.splitlines() if len(ln.strip()) >= min_length]
            result: Dict[str, Any] = {
                "strings": lines[:max_count],
                "total_found": len(lines),
                "truncated": len(lines) > max_count,
                "input": read.describe(),
            }
            if read.warnings():
                result["warnings"] = read.warnings()
            return result

        pattern = re.compile(rb"[\x20-\x7e]{%d,}" % min_length)
        strings: List[str] = []
        truncated = False
        try:
            size = path.stat().st_size
            end = min(size, MAX_STRING_SCAN_BYTES)
            if size > 0:
                with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
                    for match in pattern.finditer(view, 0, end):
                        strings.append(match.group().decode("ascii"))
                        if len(strings) >= max_count:
                            truncated = True
                            break
            if end < size:
                truncated = True
        except (OSError, ValueError) as exc:
            return {"strings": strings, "error": f"Cannot read file: {getattr(exc, 'strerror', None) or type(exc).__name__}"}
        scanned = end

        strings = strings[:max_count]
        return {
            "strings": strings,
            "total_found": None if truncated else len(strings),  # unknown once the scan stopped early
            "truncated": truncated,
            "bytes_scanned": scanned,
        }

    @classmethod
    def extract_printable_strings(
        cls,
        file_path: str,
        min_length: int = 4,
        max_count: int = 100,
        base_dir: Optional[str] = None,
    ) -> List[str]:
        """Extract printable ASCII strings embedded inside a file (the text lines of a PDF).

        Returns an empty list when the path is unusable; use :meth:`extract_printable_strings_report`
        to tell "no strings" from "could not read" and to see whether the list was cut at ``max_count``.
        """
        return list(cls.extract_printable_strings_report(file_path, min_length, max_count, base_dir)["strings"])

    @staticmethod
    def calculate_entropy(
        file_path: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculate Shannon entropy [0.0 - 8.0] to detect packed, compressed, or encrypted content.

        The whole file is measured; a file above ``max_bytes`` is refused rather than sampled.
        """
        path, error = _resolve(file_path, base_dir)
        if path is None:
            return {"error": error}

        freq: Counter = Counter()
        total_bytes = 0
        try:
            size = path.stat().st_size
            if size > max_bytes:
                return {"error": f"File is too large to measure ({size} bytes; limit {max_bytes}): {file_path}"}
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK):
                    total_bytes += len(chunk)
                    freq.update(chunk)
        except OSError as exc:
            return {"error": f"Cannot read file: {exc.strerror or type(exc).__name__}"}

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
