"""Bundle test output, command exit codes and artifact hashes into a fingerprinted evidence dossier.

Honesty notes. The digest is a plain SHA-256 over the dossier's canonical JSON. It detects an
*accidental* change to the recorded evidence; it is not a signature (anyone who can edit the dossier
can recompute it), so nothing here claims non-repudiation. ``all_passed`` is true only when there is
positive evidence of success (an explicit zero exit code, or test output that reports passing tests)
and no evidence of failure; an empty dossier is ``INCONCLUSIVE``, not a pass.

Secrets: command lines and captured output are scrubbed of obvious credentials (``KEY=value``,
``Bearer ...``, well-known token shapes) before they are stored, because a dossier is meant to be
shared. File artifacts are hashed by streaming the whole file; a PDF is additionally opened through
``_input`` (every page) and its page count and coverage are recorded.
"""

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ._input import SourceReadError, is_pdf_bytes, read_source_file

_PREVIEW_CHARS = 500

_SECRET_RES = [
    (re.compile(r"(?i)\b([A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|credential)[A-Za-z0-9_.-]*)\s*([=:])\s*(?:\"[^\"]*\"|'[^']*'|\S+)"), r"\1\2***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer ***"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "gh*_***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"), "[private key removed]"),
]
_PASS_RE = re.compile(r"(?im)(\b[1-9]\d*\s+passed\b|^\s*OK\s*$|\ball tests passed\b)")
_FAIL_RE = re.compile(
    r"(?im)(\b[1-9]\d*\s+(?:failed|errors?)\b|^FAILED\b|^ERROR\b|Traceback \(most recent call last\)|\bAssertionError\b|^FAIL:)"
)


def _redact(text: Any) -> str:
    out = text if isinstance(text, str) else ("" if text is None else str(text))
    for rx, repl in _SECRET_RES:
        out = rx.sub(repl, out)
    return out


def _md(text: Any) -> str:
    """One line, with the characters that would break an inline-code span or a heading neutralised."""
    return " ".join(str(text).split()).replace("`", "'")


def _exit_code(value: Any) -> Optional[int]:
    """An explicit integer exit code, or ``None`` when it is missing or not an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    return None


def _file_info(path: Path) -> Dict[str, Any]:
    """Coverage of a file for the dossier. A PDF is read in full; anything else is just hashed."""
    try:
        with path.open("rb") as handle:
            head = handle.read(1100)
    except OSError:
        return {}
    if not (is_pdf_bytes(head) or path.suffix.lower() == ".pdf"):
        return {}
    try:
        read = read_source_file(path)
    except SourceReadError as exc:
        return {"kind": "pdf", "complete": False, "error": str(exc)}
    return read.describe()


class VerificationEvidenceCollector:
    """Collector for bundling test outputs, command exit codes, and artifact paths with an integrity digest."""

    @staticmethod
    def _compute_digest(dossier: Dict[str, Any]) -> str:
        """Deterministic SHA-256 digest over canonical JSON."""
        clean = {k: v for k, v in dossier.items() if k != "digest"}
        serialized = json.dumps(clean, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    @staticmethod
    def _verdict(commands: List[Any], tests: List[Any]) -> Dict[str, Any]:
        """``PASSED`` / ``FAILED`` / ``INCONCLUSIVE`` plus the reasons, from the recorded evidence."""
        failures: List[str] = []
        positives = 0
        for i, c in enumerate(commands, start=1):
            if not isinstance(c, dict):
                failures.append(f"command_results[{i}] is not an object")
                continue
            code = _exit_code(c.get("exit_code"))
            if code is None:
                failures.append(f"command {i} ({_md(c.get('command', '?'))[:60]}) has no valid exit_code")
            elif code != 0:
                failures.append(f"command {i} ({_md(c.get('command', '?'))[:60]}) exited with {code}")
            else:
                positives += 1
        for i, t in enumerate(tests, start=1):
            text = t if isinstance(t, str) else json.dumps(t, default=str)
            if _FAIL_RE.search(text):
                failures.append(f"test output {i} reports failures or errors")
            elif _PASS_RE.search(text):
                positives += 1
        if failures:
            return {"verdict": "FAILED", "reasons": failures}
        if positives == 0:
            return {
                "verdict": "INCONCLUSIVE",
                "reasons": ["No command with an explicit exit code 0 and no test output reporting passing tests."],
            }
        return {"verdict": "PASSED", "reasons": []}

    @classmethod
    def collect(
        cls,
        task_id: str,
        test_outputs: Optional[List[str]] = None,
        command_results: Optional[List[Dict[str, Any]]] = None,
        artifacts_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Build a deterministic verification dossier and SHA-256 digest.

        ``all_passed`` is true only when ``verdict`` is ``PASSED``: at least one explicit success and
        no failure. A missing or non-integer ``exit_code`` counts as a failure, not as success.
        """
        def as_list(v: Any) -> List[Any]:
            if v is None:
                return []
            return [v] if isinstance(v, (str, dict)) else list(v)

        tests = [_redact(t) if isinstance(t, str) else t for t in as_list(test_outputs)]
        cmds: List[Any] = []
        for c in as_list(command_results):
            if isinstance(c, dict):
                c = dict(c)
                for key in ("command", "stdout_preview", "stderr_preview", "stdout", "stderr"):
                    if key in c:
                        c[key] = _redact(c[key])
            cmds.append(c)
        arts = [str(a) for a in as_list(artifacts_paths)]

        dossier = {
            "task_id": task_id,
            "timestamp": time.time(),
            "test_outputs": tests,
            "command_results": cmds,
            "artifacts_paths": arts,
        }

        digest = cls._compute_digest(dossier)
        verdict = cls._verdict(cmds, tests)
        out: Dict[str, Any] = {
            "task_id": task_id,
            "all_passed": verdict["verdict"] == "PASSED",
            "verdict": verdict["verdict"],
            "verdict_reasons": verdict["reasons"],
            "evidence_count": len(tests) + len(cmds) + len(arts),
            "evidence_digest": digest,
            "dossier": dossier,
        }
        if arts:
            out["warnings"] = [
                "artifacts_paths are listed as given and were not checked; use record_file_artifact to hash a file."
            ]
        return out

    @classmethod
    def verify_dossier_integrity(
        cls,
        dossier: Dict[str, Any],
        expected_digest: str,
    ) -> Dict[str, Any]:
        """Check that a dossier still hashes to ``expected_digest`` (detects accidental change only)."""
        if not isinstance(dossier, dict) or not isinstance(expected_digest, str) or not expected_digest.strip():
            return {
                "valid": False,
                "expected_digest": expected_digest if isinstance(expected_digest, str) else None,
                "actual_digest": None,
                "status": "INVALID_INPUT",
                "error": "dossier must be an object and expected_digest a non-empty string.",
            }
        actual_digest = cls._compute_digest(dossier)
        matches = actual_digest.lower() == expected_digest.strip().lower()
        return {
            "valid": matches,
            "expected_digest": expected_digest,
            "actual_digest": actual_digest,
            "status": "VERIFIED_UNTOUCHED" if matches else "INTEGRITY_COMPROMISED",
            "note": "An unkeyed SHA-256 match shows the dossier equals what was fingerprinted; it is not a signature.",
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
        """Append a command execution outcome to an existing dossier and update its digest.

        Keeps the first and last 500 characters of each stream (a test run's verdict is at the
        *end*) and the SHA-256 of the full stream. An ``exit_code`` that is not an integer is
        refused rather than recorded as success.
        """
        if not isinstance(dossier, dict):
            return {"success": False, "error": "dossier must be an object."}
        code = _exit_code(exit_code)
        if code is None:
            return {"success": False, "error": f"exit_code must be an integer, got {exit_code!r}."}
        out_s = "" if stdout is None else str(stdout)
        err_s = "" if stderr is None else str(stderr)
        d = dict(dossier)
        cmds = list(d.get("command_results", []) or [])
        entry = {
            "command": _redact(command),
            "exit_code": code,
            "stdout_preview": _redact(out_s[:_PREVIEW_CHARS]),
            "stderr_preview": _redact(err_s[:_PREVIEW_CHARS]),
            "stdout_tail": _redact(out_s[-_PREVIEW_CHARS:]) if len(out_s) > _PREVIEW_CHARS else "",
            "stderr_tail": _redact(err_s[-_PREVIEW_CHARS:]) if len(err_s) > _PREVIEW_CHARS else "",
            "stdout_truncated": len(out_s) > _PREVIEW_CHARS,
            "stderr_truncated": len(err_s) > _PREVIEW_CHARS,
            "stdout_sha256": hashlib.sha256(out_s.encode("utf-8", "replace")).hexdigest(),
            "stderr_sha256": hashlib.sha256(err_s.encode("utf-8", "replace")).hexdigest(),
            "recorded_at": time.time(),
        }
        cmds.append(entry)
        d["command_results"] = cmds
        new_digest = cls._compute_digest(d)
        return {
            "success": True,
            "updated_dossier": d,
            "new_digest": new_digest,
            "command_entry": entry,
        }

    @classmethod
    def record_file_artifact(
        cls,
        dossier: Dict[str, Any],
        file_path: str,
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Hash a file on disk (the whole file) and append it as evidence.

        With ``base_dir`` set, a path outside it is refused. A PDF is also opened through
        ``_input``; the result's ``input`` key says how many pages were readable (``complete`` is
        false when a page is a scan that needs OCR, or the PDF cannot be parsed). The hash is of the
        file's bytes either way.
        """
        if not isinstance(dossier, dict):
            return {"success": False, "error": "dossier must be an object."}
        try:
            p = Path(file_path).expanduser()
            resolved = p.resolve()
        except (OSError, RuntimeError, ValueError, TypeError):
            return {"success": False, "error": f"Invalid path: {file_path!r}"}
        if base_dir is not None:
            try:
                resolved.relative_to(Path(base_dir).resolve())
            except ValueError:
                return {"success": False, "error": f"Path is outside the allowed directory: {file_path}"}
        if not resolved.exists() or not resolved.is_file():
            return {"success": False, "error": f"File does not exist: {file_path}"}

        hasher = hashlib.sha256()
        try:
            with resolved.open("rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            size = resolved.stat().st_size
        except OSError as exc:
            return {"success": False, "error": f"Failed to read file: {exc.strerror or type(exc).__name__}"}
        file_sha256 = hasher.hexdigest()
        info = _file_info(resolved)

        d = dict(dossier)
        arts = list(d.get("artifacts_paths", []) or [])
        art_entry = f"{file_path} (sha256:{file_sha256[:16]})"
        if art_entry not in arts:
            arts.append(art_entry)
        d["artifacts_paths"] = arts
        new_digest = cls._compute_digest(d)

        result: Dict[str, Any] = {
            "success": True,
            "file_path": str(resolved),
            "file_sha256": file_sha256,
            "file_size_bytes": size,
            "updated_dossier": d,
            "new_digest": new_digest,
        }
        if info:
            result["input"] = info
            if not info.get("complete", False):
                result["warnings"] = [
                    info.get("error")
                    or f"PDF page(s) {', '.join(map(str, info.get('unreadable_pages', [])))} are scans that were not read (OCR required)."
                ]
        return result

    @classmethod
    def generate_audit_certificate(cls, dossier: Dict[str, Any]) -> str:
        """Render a Markdown evidence summary with the dossier's SHA-256 fingerprint and a verdict."""
        if not isinstance(dossier, dict):
            return "# Verification Audit Certificate\n\n*Invalid dossier: expected an object.*"
        digest = cls._compute_digest(dossier)
        task_id = _md(dossier.get("task_id", "unknown"))
        cmds = list(dossier.get("command_results") or [])
        tests = dossier.get("test_outputs") or []
        arts = dossier.get("artifacts_paths") or []
        verdict = cls._verdict(cmds, tests)
        ts = dossier.get("timestamp")
        try:
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(ts))) + " UTC"
        except (TypeError, ValueError, OverflowError, OSError):
            when = "unknown"
        hashed = sum(1 for a in arts if "(sha256:" in str(a))

        cert = [
            "# 🛡️ Verification Audit Certificate",
            f"**Task ID:** `{task_id}`",
            f"**Verdict:** `{verdict['verdict']}`",
            f"**Evidence fingerprint (SHA-256):** `{digest}`",
            f"**Timestamp:** `{when}`",
            "",
            "## 📋 Evidence Inventory",
            f"- **Executed Commands:** {len(cmds)} recorded",
            f"- **Test Suite Logs:** {len(tests)} output blocks",
            f"- **File Artifacts:** {len(arts)} listed, {hashed} hashed",
            "",
            "## 🔍 Commands Audit",
        ]
        if cmds:
            for c in cmds:
                if not isinstance(c, dict):
                    cert.append("- ❔ `(malformed entry)`")
                    continue
                code = _exit_code(c.get("exit_code"))
                icon = "❔" if code is None else ("✅" if code == 0 else "❌")
                shown = "missing" if code is None else code
                cert.append(f"- {icon} `{_md(c.get('command'))}` (exit code: {shown})")
        else:
            cert.append("- *No terminal commands recorded.*")
        for reason in verdict["reasons"]:
            cert.append(f"- ⚠️ {_md(reason)}")

        cert.extend([
            "",
            "## 📦 File Artifacts",
        ])
        if arts:
            for a in arts:
                cert.append(f"- `📄 {_md(a)}`")
        else:
            cert.append("- *No files attached.*")

        cert.extend([
            "",
            "---",
            "*The fingerprint detects accidental changes to this evidence. It is an unkeyed hash, not a "
            "signature: it does not prove who produced the evidence or that it was not regenerated.*",
        ])
        return "\n".join(cert)
