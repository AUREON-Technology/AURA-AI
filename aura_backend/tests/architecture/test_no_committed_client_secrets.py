"""No OAuth client secret is committed to the client source.

Found live. A desktop sign-in failed with Google returning::

    HTTP 401  {"error": "invalid_client",
               "error_description": "The provided client secret is invalid."}

The cause was a real `GOCSPX-…` client secret sitting as the `defaultValue` of
`Config.googleDesktopClientSecret` in `aura_client/lib/core/config/config.dart` -- committed,
shipped in every desktop binary, and by then no longer valid at Google's token endpoint.

What makes it worth a fitness test rather than a one-line fix is that the declaration's own
doc comment, directly above the literal, said the opposite in detail:

    Unlike [googleDesktopClientId], this is NOT hardcoded here ... Injected only via
    `--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET=...` ... Empty by default: desktop login is
    refused with a clear error until a build actually injects it.

Every word of that was the correct design, and the code beneath it did the opposite. A reviewer
reading the comment would not look at the value; a reviewer reading the value would assume the
comment was right. That is not a mistake a careful reader catches, so it needs a check that does
not read prose.

The build scripts (`scripts/deploy_client_desktop.sh` / `.ps1`) already resolve the secret from
`deploy/.env`, which is gitignored, so the committed default was redundant as well as unsafe:
removing it restores exactly the behaviour the comment describes, including the clear
"not configured for this build" error in `desktop_oauth_client_io.dart`.

This scans the Dart client from the Python suite on purpose. That suite runs on every
`pytest` invocation here, and CLAUDE.md already establishes the pattern of checking the Flutter
client from `tests/architecture/` (`test_no_external_inference.py` does the same). A Dart test
would need a toolchain this environment often does not have.

Scope (audit F-38 / SEC-14). It began as `aura_client/**/*.dart` only, which left the host
daemon, the admin console, the client's own platform config (plist, json, xml, gradle) and every
script, workflow, compose file and backend module unscanned. It now reads every text source
under the app and support directories (`_ROOTS`, `_TEXT_SUFFIXES`), tests included: a fixture
that needs a credential-shaped string must be listed in `_ALLOWED` with the reason, which is a
reviewable decision rather than a silent hole. `.claude/` (local worktrees and audit notes) and
the build/dependency directories in `_SKIP_PARTS` are not source and are skipped.
"""

from __future__ import annotations

import functools
import os
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_CLIENT = _REPO / "aura_client"

#: Top-level directories that hold source, config, scripts or docs. Named rather than walking
#: the repository root so a Flutter SDK or virtualenv unpacked beside them (CLAUDE.md's own
#: instructions fetch Flutter into the working directory) is never read as repo content.
_ROOTS = (
    "aura_backend",
    "aura_client",
    "aura_admin",
    "aura_hostd",
    "deploy",
    "scripts",
    "infra",
    "docs",
    ".github",
)

#: Directories whose contents are not source. Matched against the path *relative to the repo
#: root*: an absolute path can contain `.claude` (a git worktree), which must not hide it.
_SKIP_PARTS = {
    "build",
    ".dart_tool",
    "ephemeral",
    ".git",
    "Pods",
    "node_modules",
    ".next",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "test-results",
    "playwright-report",
    ".gradle",
    ".claude",
}

#: Text files worth reading. Binaries (images, the untracked APK) are not.
_TEXT_SUFFIXES = {
    ".dart", ".py", ".sh", ".ps1", ".ts", ".tsx", ".js", ".mjs", ".cjs",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".template",
    ".example", ".env", ".plist", ".xml", ".xcconfig", ".gradle", ".kts", ".properties",
    ".tf", ".tfvars", ".hcl", ".md", ".txt", ".sql", ".swift", ".kt", ".html", ".rules",
    ".entitlements", ".lock",
}
_TEXT_NAMES = {"Dockerfile", "Caddyfile", "known_hosts"}
_MAX_BYTES = 5 * 1024 * 1024

#: (path relative to the repo root, label) pairs that are allowed to match, each with its reason.
#: A stale entry -- the file gone or no longer matching -- fails `test_the_allowlist_is_live`.
_ALLOWED: dict[tuple[str, str], str] = {
    ("aura_client/android/app/google-services.json", "Google API key"): (
        "The Firebase Android config's `current_key`. A Firebase API key identifies the project "
        "to Google's APIs and is compiled into every APK; it is not a secret, and Firebase "
        "documents it as safe to ship. Access is controlled by Firebase Security Rules, App "
        "Check and the key's API restrictions in Google Cloud (which should limit it to the "
        "com.auraai.aura_client package and its signing SHA-1 -- a console setting this test "
        "cannot see)."
    ),
}

#: Credential shapes that must never appear as a literal. Kept as patterns rather than values so
#: this file never itself carries a secret.
_SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"GOCSPX-[A-Za-z0-9_\-]{10,}", "Google OAuth client secret"),
    (r"AIza[A-Za-z0-9_\-]{30,}", "Google API key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\bsk-[A-Za-z0-9]{20,}", "vendor API key"),
    (r"\bghp_[A-Za-z0-9]{30,}", "GitHub personal access token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
)


def _dart_sources() -> list[Path]:
    files = [
        path
        for path in _CLIENT.rglob("*.dart")
        if not _SKIP_PARTS & set(path.relative_to(_REPO).parts) and path.is_file()
    ]
    assert files, f"no Dart sources found under {_CLIENT}; this check would pass vacuously"
    return files


def _is_text_source(path: Path) -> bool:
    return path.suffix in _TEXT_SUFFIXES or path.name in _TEXT_NAMES or path.name.startswith(".env")


@functools.lru_cache(maxsize=1)
def _scanned_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for root in _ROOTS:
        base = _REPO / root
        if not base.is_dir():
            continue
        # os.walk with pruning, not rglob: a developer's aura_admin/node_modules is ~10^5 files
        # that must not even be listed.
        for directory, subdirs, names in os.walk(base):
            subdirs[:] = [d for d in subdirs if d not in _SKIP_PARTS]
            for name in names:
                path = Path(directory) / name
                if _is_text_source(path) and path.is_file() and path.stat().st_size <= _MAX_BYTES:
                    files.append(path)
    return tuple(sorted(files))


@functools.lru_cache(maxsize=1)
def _texts() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(repo-relative posix path, lines) for every scanned file, read once for all patterns."""
    out = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        out.append((path.relative_to(_REPO).as_posix(), tuple(text.splitlines())))
    return tuple(out)


def _matches(pattern: str, label: str) -> list[str]:
    """`relative/path:line` for every match outside the allowlist. Never the matched value."""
    compiled = re.compile(pattern)
    offenders: list[str] = []
    for relative, lines in _texts():
        if (relative, label) in _ALLOWED:
            continue
        for number, line in enumerate(lines, start=1):
            if compiled.search(line):
                # The location, never the value: a failure message that quotes the credential
                # would republish it into CI logs, which is the problem rather than the report.
                offenders.append(f"{relative}:{number}")
    return offenders


def test_the_scan_covers_every_app_and_more_than_dart() -> None:
    """Guards against the scan quietly shrinking back to one directory or one file type."""
    scanned = [path.relative_to(_REPO) for path in _scanned_files()]
    roots = {p.parts[0] for p in scanned}
    assert {"aura_client", "aura_hostd", "aura_admin", "aura_backend", "deploy", ".github"} <= roots
    suffixes = {p.suffix for p in scanned}
    assert {".dart", ".py", ".ts", ".yml", ".yaml", ".json", ".sh"} <= suffixes
    assert len(_dart_sources()) > 0


@pytest.mark.parametrize("pattern,label", _SECRET_PATTERNS, ids=[p[1] for p in _SECRET_PATTERNS])
def test_no_credential_literal_in_committed_source(pattern: str, label: str) -> None:
    offenders = _matches(pattern, label)

    assert not offenders, (
        f"a {label} appears as a literal at:\n  "
        + "\n  ".join(offenders)
        + "\n\nSecrets reach a build through --dart-define (see scripts/deploy_client_desktop.sh), "
        "environment variables or deploy/.env -- never through source. If this was committed, "
        "rotate it: it is in git history and in every artefact already built from it. If it is "
        "a deliberate, harmless fixture, add it to _ALLOWED with the reason."
    )


def test_the_allowlist_is_live() -> None:
    patterns = dict((label, pattern) for pattern, label in _SECRET_PATTERNS)
    for (relative, label), reason in _ALLOWED.items():
        assert reason.strip(), f"{relative}: an allowlist entry needs its reason"
        path = _REPO / relative
        assert path.is_file(), f"stale _ALLOWED entry, file is gone: {relative}"
        assert re.search(patterns[label], path.read_text(encoding="utf-8")), (
            f"stale _ALLOWED entry, {relative} no longer contains a {label}: remove it"
        )


class TestTheDesktopSecretStaysInjectedOnly:
    """The specific declaration that regressed, pinned against its own documentation."""

    @property
    def _config(self) -> str:
        return (_CLIENT / "lib" / "core" / "config" / "config.dart").read_text(encoding="utf-8")

    def test_the_client_secret_defaults_to_empty(self) -> None:
        match = re.search(
            r"googleDesktopClientSecret\s*=\s*String\.fromEnvironment\(\s*"
            r"'GOOGLE_DESKTOP_CLIENT_SECRET',\s*defaultValue:\s*'([^']*)'",
            self._config,
        )
        assert match, (
            "could not find googleDesktopClientSecret's fromEnvironment declaration -- if it was "
            "restructured, restate this check against the new shape rather than deleting it"
        )
        assert match.group(1) == "", (
            "googleDesktopClientSecret has a non-empty default, so a secret is compiled into "
            "every build. It must arrive via --dart-define only; an empty default is what makes "
            "desktop_oauth_client_io.dart refuse with a clear message instead of letting Google "
            "answer 401 invalid_client."
        )

    def test_the_client_id_may_still_be_hardcoded(self) -> None:
        """The deliberate asymmetry, asserted so nobody "fixes" it by emptying both.

        An OAuth client id is a public identifier -- the backend hardcodes the matching audience
        in `config.py::google_desktop_client_id` for the same reason. Only the secret is secret.
        """
        match = re.search(
            r"googleDesktopClientId\s*=\s*String\.fromEnvironment\(\s*"
            r"'GOOGLE_DESKTOP_CLIENT_ID',\s*defaultValue:\s*'([^']*)'",
            self._config,
        )
        assert match and match.group(1).endswith(".apps.googleusercontent.com"), (
            "the desktop client id should keep its hardcoded public default"
        )
