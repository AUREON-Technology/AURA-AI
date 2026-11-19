import hashlib
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional


class BackupRestoreVerifier:
    """Verifier for backup file existence, SHA-256 checksums, and archive integrity."""

    @staticmethod
    def compute_sha256(file_path: str) -> Dict[str, Any]:
        """Compute the SHA-256 digest and byte size of a file on disk."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {"exists": False, "error": f"File not found: {file_path}"}

        h = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                size_bytes += len(chunk)
                h.update(chunk)

        return {
            "exists": True,
            "file": str(path),
            "size_bytes": size_bytes,
            "sha256": h.hexdigest(),
        }

    @classmethod
    def verify_checksum(cls, file_path: str, expected_sha256: str) -> Dict[str, Any]:
        """Verify a file's SHA-256 digest against an expected hex string."""
        info = cls.compute_sha256(file_path)
        if not info.get("exists"):
            return {"verified": False, "error": info.get("error")}

        actual = info["sha256"].lower()
        expected = expected_sha256.strip().lower()
        return {
            "verified": actual == expected,
            "file": info["file"],
            "actual_sha256": actual,
            "expected_sha256": expected,
        }

    @staticmethod
    def verify_archive_integrity(archive_path: str) -> Dict[str, Any]:
        """Verify that a .zip or .tar.* archive opens cleanly and list member count."""
        path = Path(archive_path)
        if not path.exists() or not path.is_file():
            return {"valid_archive": False, "error": f"Archive not found: {archive_path}"}

        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path, "r") as zf:
                    bad = zf.testzip()
                    if bad is not None:
                        return {"valid_archive": False, "error": f"Corrupt zip member: {bad}"}
                    return {
                        "valid_archive": True,
                        "format": "zip",
                        "entries_count": len(zf.namelist()),
                    }
            if tarfile.is_tarfile(path):
                with tarfile.open(path, "r:*") as tf:
                    members = tf.getmembers()
                    return {
                        "valid_archive": True,
                        "format": "tar",
                        "entries_count": len(members),
                    }
            return {"valid_archive": False, "error": "Unsupported archive format."}
        except Exception as exc:
            return {"valid_archive": False, "error": f"{type(exc).__name__}: {exc}"}

    @classmethod
    def verify_backup(
        cls,
        backup_path: str,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Combined backup verification check for existence, archive format, and optional checksum."""
        path = Path(backup_path)
        if not path.exists() or not path.is_file():
            return {"recovery_ready": False, "error": f"Backup file does not exist: {backup_path}"}

        checksum_res = cls.verify_checksum(backup_path, expected_sha256) if expected_sha256 else None
        archive_res = (
            cls.verify_archive_integrity(backup_path)
            if path.suffix.lower() in {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
            else None
        )

        ready = True
        if checksum_res is not None and not checksum_res.get("verified"):
            ready = False
        if archive_res is not None and not archive_res.get("valid_archive"):
            ready = False

        return {
            "recovery_ready": ready,
            "file": str(path),
            "size_bytes": path.stat().st_size,
            "checksum": checksum_res,
            "archive": archive_res,
        }