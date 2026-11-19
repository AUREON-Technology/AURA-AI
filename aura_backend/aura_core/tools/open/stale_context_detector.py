import hashlib
from pathlib import Path
from typing import Any, Dict, Optional


class StaleContextDetector:
    """Detector for checking whether a file on disk has changed since it was read or cached."""

    @staticmethod
    def compute_file_sha256(file_path: str) -> Optional[str]:
        """Compute SHA-256 hex digest of a file, or None if unavailable."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def inspect_staleness(
        cls,
        file_path: str,
        cached_hash: str,
        cached_mtime: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Compare current file content hash and mtime against cached values."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {
                "file_path": file_path,
                "exists": False,
                "is_stale": True,
                "error": f"File '{file_path}' does not exist on disk.",
                "action": "ABORT_OR_REFRESH",
            }

        current_mtime = path.stat().st_mtime
        current_hash = cls.compute_file_sha256(file_path)
        hash_changed = current_hash != cached_hash
        mtime_changed = cached_mtime is not None and abs(current_mtime - cached_mtime) > 1e-6
        is_stale = hash_changed or mtime_changed

        return {
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