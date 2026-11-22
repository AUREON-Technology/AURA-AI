"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. A digest over
caller-supplied evidence proves nothing about whether the commands ran, a missing ``exit_code``
counts as 0, and ``record_file_artifact`` reads paths on the gateway.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import hashlib
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional


class VerificationEvidenceCollector:
    """Collector for bundling test outputs, command exit codes, and artifact paths with cryptographic integrity."""

    @staticmethod
    def _compute_digest(dossier: Dict[str, Any]) -> str:
        """Deterministic SHA-256 digest over canonical JSON."""
        clean = {k: v for k, v in dossier.items() if k != "digest"}
        serialized = json.dumps(clean, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    @classmethod
    def collect(
        cls,
        task_id: str,
        test_outputs: Optional[List[str]] = None,
        command_results: Optional[List[Dict[str, Any]]] = None,
        artifacts_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Build a deterministic verification dossier and SHA-256 digest."""
        cmds = list(command_results) if command_results else []
        tests = list(test_outputs) if test_outputs else []
        arts = list(artifacts_paths) if artifacts_paths else []

        dossier = {
            "task_id": task_id,
            "timestamp": time.time(),
            "test_outputs": tests,
            "command_results": cmds,
            "artifacts_paths": arts,
        }

        digest = cls._compute_digest(dossier)
        all_cmds_ok = all(int(c.get("exit_code", 0)) == 0 for c in cmds)

        return {
            "task_id": task_id,
            "all_passed": all_cmds_ok,
            "evidence_count": len(tests) + len(cmds) + len(arts),
            "evidence_digest": digest,
            "dossier": dossier,
        }

    @classmethod
    def verify_dossier_integrity(
        cls,
        dossier: Dict[str, Any],
        expected_digest: str,
    ) -> Dict[str, Any]:
        """Verify whether an evidence dossier has maintained cryptographic integrity without tampering."""
        actual_digest = cls._compute_digest(dossier)
        matches = (actual_digest.lower() == expected_digest.strip().lower())
        return {
            "valid": matches,
            "expected_digest": expected_digest,
            "actual_digest": actual_digest,
            "status": "VERIFIED_UNTOUCHED" if matches else "INTEGRITY_COMPROMISED",
        }

    @classmethod
    def record_command_evidence(
        cls,
        dossier: Dict[str, Any],
        command: str,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
    ) -> Dict[str, Any]:
        """Append a command execution outcome to an existing dossier and update its digest."""
        d = dict(dossier)
        cmds = list(d.get("command_results", []))
        entry = {
            "command": command,
            "exit_code": int(exit_code),
            "stdout_preview": stdout[:500] if stdout else "",
            "stderr_preview": stderr[:500] if stderr else "",
            "recorded_at": time.time(),
        }
        cmds.append(entry)
        d["command_results"] = cmds
        new_digest = cls._compute_digest(d)
        return {
            "updated_dossier": d,
            "new_digest": new_digest,
            "command_entry": entry,
        }

    @classmethod
    def record_file_artifact(
        cls,
        dossier: Dict[str, Any],
        file_path: str,
    ) -> Dict[str, Any]:
        """Compute file hash on disk and append as verified evidence."""
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return {"success": False, "error": f"File does not exist: {file_path}"}

        hasher = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        file_sha256 = hasher.hexdigest()

        d = dict(dossier)
        arts = list(d.get("artifacts_paths", []))
        art_entry = f"{file_path} (sha256:{file_sha256[:16]})"
        if art_entry not in arts:
            arts.append(art_entry)
        d["artifacts_paths"] = arts
        new_digest = cls._compute_digest(d)

        return {
            "success": True,
            "file_path": str(p),
            "file_sha256": file_sha256,
            "file_size_bytes": p.stat().st_size,
            "updated_dossier": d,
            "new_digest": new_digest,
        }

    @classmethod
    def generate_audit_certificate(cls, dossier: Dict[str, Any]) -> str:
        """Render a formal Markdown audit certificate proving task completion with evidence digests."""
        digest = cls._compute_digest(dossier)
        task_id = dossier.get("task_id", "unknown")
        cmds = dossier.get("command_results", [])
        tests = dossier.get("test_outputs", [])
        arts = dossier.get("artifacts_paths", [])

        cert = [
            f"# 🛡️ Verification Audit Certificate",
            f"**Task ID:** `{task_id}`",
            f"**Cryptographic Digest (SHA-256):** `{digest}`",
            f"**Timestamp:** `{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(dossier.get('timestamp', time.time())))} UTC`",
            "",
            "## 📋 Evidence Inventory",
            f"- **Executed Commands:** {len(cmds)} recorded",
            f"- **Test Suite Logs:** {len(tests)} output blocks",
            f"- **File Artifacts:** {len(arts)} verified files",
            "",
            "## 🔍 Commands Audit",
        ]
        if cmds:
            for c in cmds:
                code = c.get("exit_code", 0)
                icon = "✅" if code == 0 else "❌"
                cert.append(f"- {icon} `{c.get('command')}` (exit code: {code})")
        else:
            cert.append("- *No terminal commands recorded.*")

        cert.extend([
            "",
            "## 📦 File Artifacts",
        ])
        if arts:
            for a in arts:
                cert.append(f"- `📄 {a}`")
        else:
            cert.append("- *No files attached.*")

        cert.extend([
            "",
            "---",
            "*This certificate cryptographically seals the execution outcome and verifies non-repudiation.*",
        ])
        return "\n".join(cert)
