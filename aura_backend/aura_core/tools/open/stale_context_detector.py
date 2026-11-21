"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. It reads and hashes
filesystem paths on the gateway (KAN-34). The useful version -- has a file on the user's machine
changed since the agent read it -- would be an ``aura_hostd`` RPC; none is planned.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import difflib
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional


class StaleContextDetector:
    """Detector for checking whether files on disk have drifted or changed since read/cached by an agent."""

    @staticmethod
    def compute_file_sha256(file_path: str) -> Optional[str]:
        """Compute SHA-256 hex digest of a file, or None if unavailable."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def record_snapshot(cls, file_path: str) -> Dict[str, Any]:
        """Record file state snapshot (hash, mtime, size) at time of reading."""
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return {"file_path": file_path, "exists": False, "hash": None, "mtime": None}
        st = p.stat()
        return {
            "file_path": str(p.resolve()),
            "exists": True,
            "hash": cls.compute_file_sha256(file_path),
            "mtime": st.st_mtime,
            "size_bytes": st.st_size,
        }

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

    @classmethod
    def batch_verify_staleness(cls, snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Verify staleness for a batch of tracked files before executing multi-file operations."""
        stale_files: List[Dict[str, Any]] = []
        verified_count = 0

        for snap in snapshots:
            fpath = snap.get("file_path", "")
            cached_h = snap.get("hash", "")
            cached_mt = snap.get("mtime")
            res = cls.inspect_staleness(fpath, cached_h, cached_mt)
            if res["is_stale"]:
                stale_files.append(res)
            else:
                verified_count += 1

        all_fresh = (len(stale_files) == 0)
        return {
            "all_fresh": all_fresh,
            "total_checked": len(snapshots),
            "fresh_count": verified_count,
            "stale_count": len(stale_files),
            "stale_files": stale_files,
            "recommended_action": "PROCEED" if all_fresh else "ABORT_CONCURRENCY_CONFLICT",
        }

    @staticmethod
    def compute_diff_from_cached(file_path: str, cached_content: str) -> Dict[str, Any]:
        """Compute unified diff showing external modifications made since cached read."""
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return {"file_path": file_path, "exists": False, "diff": "File was deleted on disk."}

        current_content = p.read_text(encoding="utf-8", errors="ignore")
        diff = list(difflib.unified_diff(
            cached_content.splitlines(keepends=True),
            current_content.splitlines(keepends=True),
            fromfile=f"cached/{p.name}",
            tofile=f"current/{p.name}",
            n=3,
        ))
        return {
            "file_path": file_path,
            "has_external_edits": len(diff) > 0,
            "unified_diff": "".join(diff),
        }
