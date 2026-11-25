"""Detect files that changed on disk since an agent read or cached them.

Every method takes an optional ``base_dir``: when set, a path that resolves outside it (``..``,
absolute paths, symlinks) is refused. Hashing streams the *whole* file, so a PDF's hash covers every
byte. :meth:`StaleContextDetector.compute_diff_from_cached` reads text; a PDF is read in full through
``_input`` (every page) and a scanned page that cannot be read is reported, never ignored.
"""

import difflib
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._input import SourceReadError, read_source_file

BaseDir = Union[str, Path, None]


def _resolve(file_path: Any, base_dir: BaseDir) -> Optional[Path]:
    """The resolved path, or ``None`` when it is invalid or outside ``base_dir``."""
    try:
        resolved = Path(file_path).expanduser().resolve()
        if base_dir is not None:
            resolved.relative_to(Path(base_dir).resolve())
        return resolved
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def _unified(a: str, b: str, fromfile: str, tofile: str, n: int = 3) -> str:
    out: List[str] = []
    for line in difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True), fromfile=fromfile, tofile=tofile, n=n
    ):
        out.append(line)
        if not line.endswith("\n"):  # last line without newline: keep the next diff line on its own row
            out.append("\n\\ No newline at end of file\n")
    return "".join(out)


class StaleContextDetector:
    """Detector for checking whether files on disk have drifted or changed since read/cached by an agent."""

    @staticmethod
    def compute_file_sha256(file_path: str, base_dir: BaseDir = None) -> Optional[str]:
        """SHA-256 hex digest of the whole file, or None if it is unavailable or outside ``base_dir``."""
        path = _resolve(file_path, base_dir)
        if path is None:
            return None
        try:
            if not path.exists() or not path.is_file():
                return None
            h = hashlib.sha256()
            with path.open("rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None

    @classmethod
    def record_snapshot(cls, file_path: str, base_dir: BaseDir = None) -> Dict[str, Any]:
        """Record file state snapshot (hash, mtime, size) at time of reading."""
        p = _resolve(file_path, base_dir)
        if p is None:
            return {"file_path": file_path, "exists": False, "hash": None, "mtime": None,
                    "error": "Invalid path, or outside the allowed directory."}
        try:
            if not p.exists() or not p.is_file():
                return {"file_path": file_path, "exists": False, "hash": None, "mtime": None}
            st = p.stat()
        except OSError:
            return {"file_path": file_path, "exists": False, "hash": None, "mtime": None}
        digest = cls.compute_file_sha256(str(p))
        if digest is None:
            return {"file_path": file_path, "exists": False, "hash": None, "mtime": None,
                    "error": "The file could not be read."}
        return {
            "file_path": str(p),
            "exists": True,
            "hash": digest,
            "mtime": st.st_mtime,
            "size_bytes": st.st_size,
        }

    @classmethod
    def inspect_staleness(
        cls,
        file_path: str,
        cached_hash: str,
        cached_mtime: Optional[float] = None,
        base_dir: BaseDir = None,
    ) -> Dict[str, Any]:
        """Compare current file content hash and mtime against cached values.

        Anything that cannot be confirmed fresh is reported stale: a missing file, an unreadable
        file, a path outside ``base_dir``, or a missing/invalid cached hash.
        """
        path = _resolve(file_path, base_dir)
        if path is None:
            return {
                "file_path": file_path, "exists": False, "is_stale": True,
                "error": f"Invalid path, or outside the allowed directory: {file_path!r}",
                "action": "ABORT_OR_REFRESH",
            }
        try:
            present = path.exists() and path.is_file()
            current_mtime = path.stat().st_mtime if present else None
        except OSError:
            present, current_mtime = False, None
        if not present:
            return {
                "file_path": file_path,
                "exists": False,
                "is_stale": True,
                "error": f"File '{file_path}' does not exist on disk.",
                "action": "ABORT_OR_REFRESH",
            }

        current_hash = cls.compute_file_sha256(str(path))
        if current_hash is None:
            return {
                "file_path": file_path, "exists": True, "is_stale": True,
                "error": f"File '{file_path}' could not be read.", "action": "ABORT_OR_REFRESH",
            }
        cached_norm = cached_hash.strip().lower() if isinstance(cached_hash, str) else ""
        hash_changed = current_hash != cached_norm
        try:
            mtime_changed = cached_mtime is not None and abs(current_mtime - float(cached_mtime)) > 1e-6
        except (TypeError, ValueError):
            mtime_changed = True  # an unusable cached mtime cannot confirm freshness
        is_stale = hash_changed or mtime_changed

        result: Dict[str, Any] = {
            "file_path": file_path,
            "exists": True,
            "is_stale": is_stale,
            "hash_changed": hash_changed,
            "mtime_changed": mtime_changed,
            "current_hash": current_hash,
            "cached_hash": cached_hash,
            "current_mtime": current_mtime,
            "cached_mtime": cached_mtime,
            "action": "ABORT_OR_REFRESH" if is_stale else "PROCEED",
        }
        if not cached_norm:
            result["warnings"] = ["No cached hash was supplied, so the file is treated as stale."]
        return result

    @classmethod
    def batch_verify_staleness(cls, snapshots: List[Dict[str, Any]], base_dir: BaseDir = None) -> Dict[str, Any]:
        """Verify staleness for a batch of tracked files before executing multi-file operations.

        An empty batch is not "all fresh": nothing was checked, so ``all_fresh`` is false.
        """
        if isinstance(snapshots, dict):
            snapshots = [snapshots]
        snaps = list(snapshots) if isinstance(snapshots, (list, tuple)) else []
        if not snaps:
            return {
                "all_fresh": False, "total_checked": 0, "fresh_count": 0, "stale_count": 0,
                "stale_files": [], "recommended_action": "ABORT_NO_SNAPSHOTS",
                "warnings": ["No snapshots were supplied, so nothing was checked."],
            }
        stale_files: List[Dict[str, Any]] = []
        verified_count = 0

        for snap in snaps:
            if not isinstance(snap, dict):
                stale_files.append({"file_path": None, "is_stale": True, "error": "Snapshot is not an object."})
                continue
            res = cls.inspect_staleness(
                snap.get("file_path", ""), snap.get("hash", ""), snap.get("mtime"), base_dir=base_dir
            )
            if res["is_stale"]:
                stale_files.append(res)
            else:
                verified_count += 1

        all_fresh = (len(stale_files) == 0)
        return {
            "all_fresh": all_fresh,
            "total_checked": len(snaps),
            "fresh_count": verified_count,
            "stale_count": len(stale_files),
            "stale_files": stale_files,
            "recommended_action": "PROCEED" if all_fresh else "ABORT_CONCURRENCY_CONFLICT",
        }

    @staticmethod
    def compute_diff_from_cached(file_path: str, cached_content: str, base_dir: BaseDir = None) -> Dict[str, Any]:
        """Compute unified diff showing external modifications made since cached read.

        The file is read completely. A PDF is read page by page (``cached_content`` should be its
        extracted text); if some pages are scans that cannot be read, ``complete`` is false and
        ``verified_unchanged`` is false even when the readable text matches.
        """
        if cached_content is None:
            cached_content = ""
        if not isinstance(cached_content, str):
            return {"file_path": file_path, "exists": True, "has_external_edits": True,
                    "error": "cached_content must be a string.", "unified_diff": ""}
        path = _resolve(file_path, base_dir)
        if path is None:
            return {"file_path": file_path, "exists": False, "has_external_edits": True,
                    "diff": "Invalid path, or outside the allowed directory.",
                    "error": "Invalid path, or outside the allowed directory."}
        try:
            present = path.exists() and path.is_file()
        except OSError:
            present = False
        if not present:
            return {"file_path": file_path, "exists": False, "diff": "File was deleted on disk."}

        try:
            read = read_source_file(path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"file_path": file_path, "exists": True, "has_external_edits": True,
                    "complete": False, "error": str(exc), "unified_diff": ""}

        diff = _unified(cached_content, read.text, f"cached/{path.name}", f"current/{path.name}")
        has_edits = len(diff) > 0
        result: Dict[str, Any] = {
            "file_path": file_path,
            "has_external_edits": has_edits,
            "unified_diff": diff,
            "complete": read.complete,
            "verified_unchanged": (not has_edits) and read.complete,
            "input": read.describe(),
        }
        if not read.complete:
            result["warnings"] = read.warnings() + [
                "The unread pages could have changed: this result does not prove the file is unchanged."
            ]
        return result
