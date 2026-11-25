"""A short, plain description and risk flags for a tool call that is waiting for the user's approval.

A confirmation card on a phone used to show the raw command, arguments and diff. This produces what the
person needs to decide in two seconds -- a one-line ``headline`` ("Run: npm test", "Edit src/app.py",
"Deploy deploy-backend.yml from main") and ``risk_flags`` ("deletes files", "touches a secret file",
"starts a deployment") -- and the raw detail stays one tap away.

Deterministic and pure: no model call, no I/O, nothing that can fail or be steered by the content it
describes. Flags are **advisory**: they never change whether a call needs confirmation (``ToolPolicy`` decides
that) and a call with no flags is not thereby safe. Credentials are masked with the shared catalogue so a
headline never repeats a secret.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..domain.secret_catalog import find_secrets, mask

__all__ = ["describe_pending_call"]

_HIGH, _MEDIUM, _LOW = "high", "medium", "low"
_MAX_FLAGS = 6

_SECRET_PATH = re.compile(r"(^|/)(\.env[\w.]*|.*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa).*|.*secret.*|.*credential.*)$", re.I)
_MIGRATION_PATH = re.compile(r"(^|/)(migrations?|alembic/versions)/|\.sql$", re.I)
_CI_PATH = re.compile(r"(^|/)\.github/workflows/|(^|/)(Dockerfile[\w.]*|docker-compose[\w.]*\.ya?ml)$", re.I)

#: (pattern, level, text) for shell commands.
_COMMAND_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+"), _HIGH, "deletes files recursively or by force"),
    (re.compile(r"\bgit\s+push\b[^\n]*(--force|-f\b|--force-with-lease)"), _HIGH, "force-pushes to a remote"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s)"), _HIGH, "throws away uncommitted work"),
    (re.compile(r"\b(drop|truncate)\s+(table|database|schema)\b", re.I), _HIGH, "drops database objects"),
    (re.compile(r"\b(mkfs|dd\s+if=|shred|wipefs)\b"), _HIGH, "can destroy disk contents"),
    (re.compile(r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"), _HIGH, "downloads a script and runs it unchecked"),
    (re.compile(r"\bchmod\s+(-\w+\s+)*(0?777|a\+rwx)\b"), _MEDIUM, "makes files world-writable"),
    (re.compile(r"(^|[;&|]\s*)sudo\b"), _MEDIUM, "runs with elevated privileges"),
    (re.compile(r"\bgit\s+push\b"), _MEDIUM, "pushes to a remote"),
    (re.compile(r"\b(pip3?|npm|yarn|pnpm|apt(-get)?|brew|cargo|go)\s+(install|add|get)\b"), _MEDIUM, "installs packages"),
    (re.compile(r"\b(curl|wget|ssh|scp|rsync|nc|ncat)\b"), _LOW, "uses the network"),
    (re.compile(r"\b(cat|less|head|tail|cp|mv)\s+[^\n;|&]*(\.env\b|id_rsa|\.pem\b|credentials)"), _MEDIUM,
     "reads or moves a secret file"),
)
_GITHUB_WRITES = {
    "github_commit_files": "writes a commit to a remote repository",
    "github_create_branch": "creates a branch in a remote repository",
    "github_create_draft_pull_request": "opens a pull request",
    "clone_repository": "downloads a repository",
}


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = mask(text, find_secrets(text))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _paths(arguments: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("path", "file", "destination", "repo_path"):
        if isinstance(arguments.get(key), str):
            out.append(arguments[key])
    for key in ("paths", "files"):
        value = arguments.get(key)
        if isinstance(value, Mapping):
            out += [str(k) for k in value]
        elif isinstance(value, (list, tuple)):
            out += [str(v.get("path")) if isinstance(v, Mapping) else str(v) for v in value]
    return [p for p in out if p][:50]


def _headline(tool: str, args: Mapping[str, Any], paths: list[str]) -> str:
    if tool == "run_command":
        first = next((ln for ln in str(args.get("command") or "").splitlines() if ln.strip()), "")
        return f"Run: {_clip(first, 100)}"
    if tool in ("write_file", "edit_file"):
        verb = "Write" if tool == "write_file" else "Edit"
        return f"{verb} {_clip(paths[0], 80)}" if paths else f"{verb} a file"
    if tool == "write_files":
        return f"Write {len(paths)} file(s)" + (f": {_clip(paths[0], 60)}" if paths else "")
    if tool == "git_commit":
        return f"Commit: {_clip(args.get('message'), 80)}"
    if tool == "git_checkout_branch":
        return f"Switch to branch {_clip(args.get('branch'), 60)}"
    if tool == "clone_repository":
        return f"Clone {_clip(args.get('repo'), 80)}"
    if tool == "github_commit_files":
        count = len(args.get("files") or {}) if isinstance(args.get("files"), Mapping) else 0
        return f"Commit {count} file(s) to {_clip(args.get('branch'), 50)}"
    if tool == "github_create_draft_pull_request":
        return f"Open draft pull request: {_clip(args.get('title'), 80)}"
    if tool == "github_create_branch":
        return f"Create branch {_clip(args.get('branch'), 60)}"
    if tool == "deploy_release":
        return f"Deploy {_clip(args.get('workflow'), 60)} from {_clip(args.get('ref') or 'main', 30)}"
    if tool == "rollback_release":
        return f"Roll back {_clip(args.get('workflow'), 60)} to {_clip(str(args.get('sha') or '')[:12], 12)}"
    return f"Run {tool}"


def _diff_size(diff_preview: Any) -> int:
    if not isinstance(diff_preview, str):
        return 0
    return sum(1 for ln in diff_preview.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---")))


def describe_pending_call(tool: str, arguments: Mapping[str, Any] | None, diff_preview: Any = None) -> dict[str, Any]:
    """``{"headline": str, "risk_flags": [{"code", "level", "text"}]}`` for one held call. Never raises."""
    args: Mapping[str, Any] = arguments if isinstance(arguments, Mapping) else {}
    try:
        paths = _paths(args)
        headline = _headline(str(tool), args, paths)
        flags: list[tuple[str, str, str]] = []
        if tool == "run_command":
            command = str(args.get("command") or "")
            for pattern, level, text in _COMMAND_RULES:
                if pattern.search(command):
                    flags.append(("command", level, text))
        for path in paths:
            if _SECRET_PATH.search(path):
                flags.append(("sensitive_file", _HIGH, f"touches a secret file ({_clip(path, 50)})"))
            if _MIGRATION_PATH.search(path):
                flags.append(("migration", _MEDIUM, f"changes the database schema ({_clip(path, 50)})"))
            if _CI_PATH.search(path):
                flags.append(("pipeline", _MEDIUM, f"changes build or deploy config ({_clip(path, 50)})"))
        if tool in ("deploy_release", "rollback_release"):
            flags.append(("deployment", _HIGH, "starts a deployment" if tool == "deploy_release" else "redeploys an older version"))
        if tool in _GITHUB_WRITES:
            flags.append(("remote_write", _MEDIUM if tool != "clone_repository" else _LOW, _GITHUB_WRITES[tool]))
        size = _diff_size(diff_preview)
        if size > 500:
            flags.append(("large_change", _MEDIUM, f"large change ({size} lines)"))
        rank = {_HIGH: 0, _MEDIUM: 1, _LOW: 2}
        seen: set[tuple[str, str]] = set()
        ordered = []
        for code, level, text in sorted(flags, key=lambda f: rank[f[1]]):
            if (code, text) not in seen:
                seen.add((code, text))
                ordered.append({"code": code, "level": level, "text": text})
        return {"headline": headline, "risk_flags": ordered[:_MAX_FLAGS]}
    except Exception:  # a description must never be the reason a confirmation fails to appear
        return {"headline": f"Run {_clip(tool, 60)}", "risk_flags": []}
