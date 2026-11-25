"""``scan_security``: Semgrep, Gitleaks and Bandit run in the engineer's sandbox (ADR-050).

``scan_code_security`` is a pattern scanner inside the gateway (no process, no network). Deeper tools
exist -- Semgrep for data-flow-aware rules in many languages, Gitleaks for secrets and git history,
Bandit for Python -- and the gateway must not run subprocesses, so they run where ``run_command`` and
``lint_code`` already do: the user's paired desktop or the server sandbox, through the same
``SandboxRunner``. This module is their parsers and the report around them. Same contract as
``lint_code``: one status per scanner (``ok`` / ``findings`` / ``not_installed`` / ``timeout`` /
``failed``), and ``clean`` only when every requested scanner actually ran and found nothing -- a scanner
that is missing, crashed, timed out or printed something unreadable is **never** a clean result.

Boundaries:

* **Rules are the image's, not the repository's.** Semgrep runs with ``--config`` pointing at the rule
  set the sandbox image bundles (``/opt/aura/semgrep-rules``, pinned in ``deploy/sandbox/Dockerfile``),
  never ``--config auto`` (network, telemetry) and never a rule file from the scanned workspace (a
  repository must not choose its own pass/fail). Metrics and version checks are off.
* **No secret is ever returned.** Gitleaks runs with ``--redact`` and only ``RuleID``, ``Description``,
  file, lines and ``Fingerprint`` are read from its report; ``Secret``/``Match``/``Commit author`` fields
  are never parsed. Semgrep/Bandit snippets are masked with the shared credential catalogue.
* **Read-only.** Nothing is fixed, written or uploaded; argv is built from validated parts, no shell.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from ..domain.secret_catalog import find_secrets, mask

__all__ = ["SCANNERS", "SEMGREP_RULES_DIR", "SEMGREP_RULE_FAMILIES", "parse_semgrep", "parse_bandit",
           "parse_gitleaks", "scanner_args", "normalise_semgrep_id"]

SCANNERS = ("semgrep", "gitleaks", "bandit")
#: Where the sandbox image bundles the pinned Semgrep rule set.
SEMGREP_RULES_DIR = "/opt/aura/semgrep-rules"
#: The rule families (top-level directories of the rules repository) that are scanned.
SEMGREP_RULE_FAMILIES = (
    "python", "javascript", "typescript", "go", "java", "kotlin", "ruby", "php", "csharp", "bash",
    "dockerfile", "terraform", "yaml", "generic", "c", "rust", "scala", "swift", "html", "json",
)
_SEVERITY = {"ERROR": "error", "WARNING": "warning", "INFO": "info", "HIGH": "error", "MEDIUM": "warning",
             "LOW": "info", "CRITICAL": "error"}
_BANDIT_EXCLUDES = ".git,.venv,venv,node_modules,__pycache__,site-packages,dist,build"
_SNIPPET = 240


def _short(text: Any, limit: int = 500) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe(text: Any, limit: int = _SNIPPET) -> str:
    """A line of the scanned code with every credential in it masked."""
    value = str(text or "").strip()
    return _short(mask(value, find_secrets(value)), limit)


def normalise_semgrep_id(check_id: str) -> str:
    """Semgrep names a rule by the dotted path of its file; drop the directory the rule set sits in."""
    parts = str(check_id).split(".")
    for marker in ("semgrep-rules", "rules"):
        if marker in parts:
            return ".".join(parts[len(parts) - 1 - parts[::-1].index(marker) + 1:]) or check_id
    return ".".join(parts[-4:])


def parse_semgrep(stdout: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``semgrep --json`` -> ``(findings, errors)``. Raises ``ValueError`` on anything else."""
    data = json.loads(stdout or "{}")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("semgrep did not print a results object")
    out: list[dict[str, Any]] = []
    for item in data["results"]:
        if not isinstance(item, dict):
            raise ValueError("semgrep printed a result that is not an object")
        extra = item.get("extra") or {}
        meta = extra.get("metadata") or {}
        cwe = meta.get("cwe")
        out.append({
            "scanner": "semgrep", "file": item.get("path"), "line": (item.get("start") or {}).get("line"),
            "column": (item.get("start") or {}).get("col"), "end_line": (item.get("end") or {}).get("line"),
            "code": normalise_semgrep_id(item.get("check_id") or "semgrep"),
            "severity": _SEVERITY.get(str(extra.get("severity")).upper(), "warning"),
            "message": _short(extra.get("message")),
            "cwe": [str(c)[:60] for c in (cwe if isinstance(cwe, list) else [cwe] if cwe else [])][:3],
            "confidence": meta.get("confidence"),
            "snippet": _safe(extra.get("lines")) if extra.get("lines") not in (None, "requires login") else None,
        })
    errors = [_short((e or {}).get("message") or e, 200) for e in (data.get("errors") or []) if e]
    return out, errors


def parse_bandit(stdout: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``bandit -f json`` -> ``(findings, errors)``."""
    data = json.loads(stdout or "{}")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("bandit did not print a results object")
    out: list[dict[str, Any]] = []
    for item in data["results"]:
        if not isinstance(item, dict):
            raise ValueError("bandit printed a result that is not an object")
        cwe = (item.get("issue_cwe") or {}).get("id")
        name = str(item.get("filename") or "")
        out.append({
            "scanner": "bandit", "file": name[2:] if name.startswith("./") else name,
            "line": item.get("line_number"), "column": item.get("col_offset"),
            "code": item.get("test_id"), "rule_name": item.get("test_name"),
            "severity": _SEVERITY.get(str(item.get("issue_severity")).upper(), "warning"),
            "message": _short(item.get("issue_text")),
            "cwe": [f"CWE-{cwe}"] if cwe else [], "confidence": str(item.get("issue_confidence") or "").lower() or None,
            "snippet": _safe(item.get("code").splitlines()[0] if item.get("code") else ""),
        })
    errors = [_short(f"{(e or {}).get('filename')}: {(e or {}).get('reason')}", 200) for e in (data.get("errors") or [])]
    return out, errors


def parse_gitleaks(stdout: str) -> list[dict[str, Any]]:
    """``gitleaks --report-format json`` -> findings. Only the rule, place and fingerprint are read:
    ``Secret``, ``Match``, author and message are never parsed, whatever the report holds."""
    data = json.loads(stdout or "[]")
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError("gitleaks did not print a JSON array")
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("gitleaks printed a finding that is not an object")
        commit = str(item.get("Commit") or "")
        out.append({
            "scanner": "gitleaks", "file": item.get("File"), "line": item.get("StartLine"),
            "end_line": item.get("EndLine"), "code": item.get("RuleID"), "severity": "error",
            "message": _short(item.get("Description") or "a secret was detected"),
            "fingerprint": item.get("Fingerprint"), "commit": commit[:12] or None, "snippet": None,
        })
    return out


def scanner_args(scanner: str, paths: Sequence[str], *, history: bool = False) -> list[list[str]]:
    """argv (after the program) for each run ``scanner`` needs: one, except Gitleaks (one per path)."""
    if scanner == "semgrep":
        configs = [arg for fam in SEMGREP_RULE_FAMILIES for arg in ("--config", f"{SEMGREP_RULES_DIR}/{fam}")]
        return [["scan", *configs, "--json", "--quiet", "--metrics=off", "--disable-version-check",
                 "--no-git-ignore", "--x-ignore-semgrepignore-files", "--timeout", "30", "--exclude", "node_modules", "--exclude", ".venv",
                 "--exclude", ".git", *paths]]
    if scanner == "bandit":
        return [["-r", *paths, "-f", "json", "-q", "-x", _BANDIT_EXCLUDES]]
    if scanner == "gitleaks":
        base = ["detect", "--report-format", "json", "--report-path", "-", "--redact", "--no-banner",
                "--exit-code", "0", "--log-level", "error"]
        return [[*base, *([] if history else ["--no-git"]), "--source", path] for path in paths]
    raise ValueError(scanner)
