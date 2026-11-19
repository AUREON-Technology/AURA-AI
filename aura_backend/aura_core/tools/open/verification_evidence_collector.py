import hashlib
import json
import time
from typing import Any, Dict, List, Optional


class VerificationEvidenceCollector:
    """Collector for bundling test outputs, command exit codes, and artifact paths with a digest."""

    @staticmethod
    def collect(
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

        serialized = json.dumps(dossier, sort_keys=True, default=str).encode("utf-8")
        digest = hashlib.sha256(serialized).hexdigest()
        all_cmds_ok = all(int(c.get("exit_code", 0)) == 0 for c in cmds)

        return {
            "task_id": task_id,
            "all_passed": all_cmds_ok,
            "evidence_count": len(tests) + len(cmds) + len(arts),
            "evidence_digest": digest,
            "dossier": dossier,
        }