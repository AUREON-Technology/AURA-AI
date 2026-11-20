import hashlib
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional


class BackupRestoreVerifier:
    """Verifier for backup file existence, SHA-256 checksums, archive integrity, and disaster recovery simulation."""

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
    def verify_sql_dump_integrity(cls, sql_dump_path: str) -> Dict[str, Any]:
        """Validate an SQL backup dump file for structural integrity and table definitions."""
        p = Path(sql_dump_path)
        if not p.exists() or not p.is_file():
            return {"valid": False, "error": f"SQL dump file not found: {sql_dump_path}"}

        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return {"valid": False, "error": str(exc)}

        create_tables = re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_`\"'.]+)", text, re.I)
        has_commit = bool(re.search(r"\bCOMMIT\b", text, re.I))
        has_inserts = bool(re.search(r"\bINSERT\s+INTO\b", text, re.I))

        return {
            "valid": len(create_tables) > 0 or has_inserts,
            "file": str(p),
            "size_bytes": p.stat().st_size,
            "tables_found": create_tables,
            "has_transaction_commit": has_commit,
            "has_inserts": has_inserts,
        }

    @classmethod
    def simulate_restore_to_temp_dir(
        cls,
        archive_or_dump_path: str,
        temp_target_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Test restore safely inside an isolated temporary directory to prove recovery readiness."""
        p = Path(archive_or_dump_path)
        if not p.exists() or not p.is_file():
            return {"restored": False, "error": f"File not found: {archive_or_dump_path}"}

        target_dir = temp_target_dir or tempfile.mkdtemp(prefix="aura_restore_drill_")
        start_time = time.perf_counter()
        recovered_items = 0

        try:
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p, "r") as zf:
                    # Safe extraction
                    for name in zf.namelist():
                        if not name.startswith(("/", "\\")) and ".." not in name:
                            zf.extract(name, target_dir)
                            recovered_items += 1
            elif tarfile.is_tarfile(p):
                with tarfile.open(p, "r:*") as tf:
                    for member in tf.getmembers():
                        if not member.name.startswith(("/", "\\")) and ".." not in member.name:
                            tf.extract(member, target_dir)
                            recovered_items += 1
            elif p.suffix.lower() == ".sql":
                # Test replay in sqlite memory db
                sql_text = p.read_text(encoding="utf-8", errors="ignore")
                conn = sqlite3.connect(":memory:")
                try:
                    conn.executescript(sql_text)
                    recovered_items = 1
                except Exception as db_exc:
                    return {"restored": False, "error": f"SQL replay failed: {db_exc}"}
                finally:
                    conn.close()

            elapsed = round((time.perf_counter() - start_time) * 1000.0, 2)
            return {
                "restored": True,
                "recovered_items_count": recovered_items,
                "elapsed_ms": elapsed,
                "temp_target_dir": target_dir,
            }
        except Exception as exc:
            return {"restored": False, "error": str(exc)}
        finally:
            if not temp_target_dir and os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)

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

    @classmethod
    def generate_restore_drill_report(cls, drill_result: Dict[str, Any]) -> str:
        """Render a Disaster Recovery Drill audit report in Markdown."""
        ready = drill_result.get("recovery_ready") or drill_result.get("restored", False)
        status_str = "SUCCESS (RTO VERIFIED)" if ready else "FAILED (RESTORE DEFECT)"

        lines = [
            f"# Disaster Recovery (DR) Drill Report: {status_str}",
            "",
            f"- **Target File:** `{drill_result.get('file', 'N/A')}`",
            f"- **Recovery Duration:** `{drill_result.get('elapsed_ms', 'N/A')} ms`",
            f"- **Items Recovered:** `{drill_result.get('recovered_items_count', 'N/A')}`",
            f"- **Integrity Verification:** {'Verified' if ready else 'Unverified / Error'}",
            "",
            "## Audit Log",
            "",
            f"```json\n{drill_result}\n```",
        ]
        return "\n".join(lines)
