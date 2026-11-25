#!/usr/bin/env python3
"""Fail if git tracks (or is about to commit) a credential: private key, service-account JSON, token.

Why this exists: `aura_backend/credentials.json`, a Google Cloud service-account key, was committed
in 5b37323 (2026-06-04). `.gitignore` already named the file, but an ignore rule does nothing for a
file added with `git add -f` or before the rule existed, and the key stays readable in history after
the file is deleted. Google's scanner found it on public GitHub and disabled it. This checks content,
not just names, so it also catches a key pasted into a source file or a differently-named JSON.

    python3 scripts/check_secrets.py            # every tracked file (CI)
    python3 scripts/check_secrets.py --staged   # only what is staged (pre-commit hook)

Stdlib only, no network. A finding prints the file and line, never the secret itself. A value that
is safe to track goes in ALLOWLIST with the reason.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY"),
    "service-account JSON": re.compile(r'"private_key_id"\s*:\s*"[0-9a-f]{20,}"'),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{20,})\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Anthropic/OpenAI-style key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{32,}"),
    "Google OAuth client secret": re.compile(r"\bGOCSPX-[0-9A-Za-z_-]{20,}"),
    "Google AI Studio key": re.compile(r"\bAQ\.[0-9A-Za-z_-]{40,}"),
    # A signed token: Qdrant Cloud API keys are JWTs, as are most service tokens.
    "JSON Web Token": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}"),
}

# Tracked files whose *name* alone is a credential, whatever they contain. `.env` files count;
# their `.example` / `.sample` / `.template` forms do not (their contents are still scanned).
FORBIDDEN_NAMES = re.compile(
    r"(^|/)(credentials\.json|service[-_]?account[^/]*\.json|[^/]*firebase-adminsdk[^/]*\.json|"
    r"[^/]*\.pem|[^/]*\.p12|id_(?:rsa|ecdsa|ed25519)|"
    r"[^/]*\.env|\.env\.(?!(?:[^/]*\.)?(?:example|sample|template)$)[^/]*)$", re.IGNORECASE)

# (path, pattern name) -> why it may stay tracked. Every entry is a decision someone has to defend.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("aura_client/android/app/google-services.json", "Google API key"):
        "Firebase Android client key: it ships inside every APK by design. Protect it with "
        "Application restrictions + API restrictions in Google Cloud Console, not by hiding it.",
}

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2", ".ttf", ".otf",
                 ".pdf", ".zip", ".gz", ".jar", ".lock"}
SELF = {"scripts/check_secrets.py", "aura_backend/tests/unit/test_check_secrets.py"}


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


def candidate_files(repo: Path, staged: bool) -> list[str]:
    raw = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z") if staged \
        else _git(repo, "ls-files", "-z")
    return [p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p]


def read(repo: Path, path: str, staged: bool) -> str | None:
    try:
        data = _git(repo, "show", f":{path}") if staged else (repo / path).read_bytes()
    except (subprocess.CalledProcessError, OSError):
        return None
    return None if b"\0" in data[:8192] else data.decode("utf-8", "replace")


def scan(repo: Path, staged: bool = False) -> list[str]:
    findings: list[str] = []
    for path in candidate_files(repo, staged):
        if path in SELF or Path(path).suffix.lower() in SKIP_SUFFIXES:
            continue
        if FORBIDDEN_NAMES.search(path):
            findings.append(f"{path}: file name is a credential (never commit it)")
        text = read(repo, path, staged)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pat in PATTERNS.items():
                if pat.search(line) and (path, name) not in ALLOWLIST:
                    findings.append(f"{path}:{lineno}: {name}")
    return findings


def main(argv: list[str]) -> int:
    repo = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    findings = scan(repo, staged="--staged" in argv)
    if not findings:
        print("check_secrets: no credentials found")
        return 0
    print("check_secrets: possible credentials committed:", file=sys.stderr)
    for f in findings:
        print(f"  {f}", file=sys.stderr)
    print("\nDo not push this. Remove it, ROTATE the credential (deleting the file does not revoke it),\n"
          "and keep the value in an environment variable / Railway variable instead.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
