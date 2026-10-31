"""No credential is committed to source -- client first, now the whole repository.

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

This scans the client from the Python suite on purpose. That suite runs on every `pytest`
invocation here, and CLAUDE.md already establishes the pattern of checking the Flutter client
from `tests/architecture/` (`test_no_external_inference.py` does the same). A Dart test would
need a toolchain this environment often does not have.

F-38 / SEC-14 (audit 2026-10-26): the scan used to cover `aura_client/**/*.dart` only. It now
covers every *tracked* text file under the client (plist, json, xml, gradle as well as Dart), the
backend, the admin console, the host daemon, deploy/, the workflows, scripts/ and docs/, plus
the repository's top-level files. Tracked files only (`git ls-files`): a developer's gitignored
`deploy/.env` holds real secrets by design and must not fail their local run, and anything
committed is exactly what this guards against. Hits that are public by design are allowlisted
by (path, label) with the reason -- never by value, so this file carries no credential either.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_CLIENT = _REPO / "aura_client"

#: Tracked trees scanned, in addition to the repository's top-level files.
_ROOTS = (
    "aura_client", "aura_backend", "aura_admin", "aura_hostd", "deploy", ".github", "scripts",
    "docs", "Doc",
)

#: Directories whose contents are not hand-written source (generated, vendored or cached), even
#: when something under them is tracked.
_SKIP_PARTS = {
    "build", ".dart_tool", "ephemeral", ".git", "Pods", "node_modules", ".next", "__pycache__",
    ".venv", "venv", "dist", "coverage", ".gradle",
}

#: Anything larger is a binary or an archive, not hand-written source.
_MAX_BYTES = 2_000_000

#: Credential shapes that must never appear as a literal. Kept as patterns rather than values so
#: this file never itself carries a secret.
_SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"GOCSPX-[A-Za-z0-9_\-]{10,}", "Google OAuth client secret"),
    (r"AIza[A-Za-z0-9_\-]{30,}", "Google API key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\bsk-[A-Za-z0-9_\-]{20,}", "vendor API key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{30,}", "GitHub fine-grained token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bhf_[A-Za-z0-9]{30,}", "Hugging Face token"),
    (r"\bgsk_[A-Za-z0-9]{30,}", "Groq API key"),
    (r"\b[sr]k_live_[A-Za-z0-9]{20,}", "Stripe live key"),
)

#: (path relative to the repo, pattern label) -> why this literal is not a secret.
_ALLOWLIST: dict[tuple[str, str], str] = {
    ("aura_client/android/app/google-services.json", "Google API key"): (
        "F-38/SEC-14: the Firebase Android client config. Its api_key is a public project "
        "identifier compiled into every APK by the google-services Gradle plugin -- Firebase "
        "security rules and the key's Android-app restriction are the control, not secrecy. "
        "Keep that restriction set in the Google Cloud console."
    ),
}


def _tracked_files() -> list[Path]:
    if shutil.which("git") is None or not (_REPO / ".git").exists():
        pytest.skip("not a git checkout; this check scans tracked files only")
    listing = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z", "--", *_ROOTS, ":(top,glob)*"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    files: list[Path] = []
    for rel in filter(None, listing.split("\0")):
        path = _REPO / rel
        if _SKIP_PARTS & set(Path(rel).parts) or not path.is_file():
            continue
        if path.stat().st_size > _MAX_BYTES:
            continue
        files.append(path)
    return files


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # binary


@pytest.fixture(scope="module")
def tracked() -> list[Path]:
    return _tracked_files()


def test_every_scanned_tree_contributes_files(tracked: list[Path]) -> None:
    """Guard against vacuous passing: a moved tree would silently scan nothing."""
    rels = [p.relative_to(_REPO).as_posix() for p in tracked]
    for root, suffix in (
        ("aura_client/", ".dart"), ("aura_client/", ".json"), ("aura_backend/", ".py"),
        ("aura_admin/", ".ts"), ("aura_hostd/", ".dart"), ("deploy/", ".yml"),
        (".github/workflows/", ".yml"),
    ):
        assert any(r.startswith(root) and r.endswith(suffix) for r in rels), (root, suffix)


@pytest.mark.parametrize("pattern,label", _SECRET_PATTERNS, ids=[p[1] for p in _SECRET_PATTERNS])
def test_no_credential_literal_in_tracked_sources(
    tracked: list[Path], pattern: str, label: str
) -> None:
    compiled = re.compile(pattern)
    offenders: list[str] = []

    for path in tracked:
        rel = path.relative_to(_REPO).as_posix()
        if (rel, label) in _ALLOWLIST:
            continue
        text = _text(path)
        if text is None or not compiled.search(text):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                # The location, never the value: a failure message that quotes the credential
                # would republish it into CI logs, which is the problem rather than the report.
                offenders.append(f"{rel}:{number}")

    assert not offenders, (
        f"a {label} appears as a literal in tracked source at:\n  "
        + "\n  ".join(offenders)
        + "\n\nSecrets reach a build through --dart-define (see scripts/deploy_client_desktop.sh), "
        "a gitignored deploy/.env or a CI secret -- never through source. If this was committed, "
        "rotate it: it is in git history and, for the client, in every binary already shipped."
    )


def test_allowlist_entries_still_match(tracked: list[Path]) -> None:
    """A stale entry would quietly exempt whatever lands at that path next."""
    patterns = {label: re.compile(pattern) for pattern, label in _SECRET_PATTERNS}
    by_rel = {p.relative_to(_REPO).as_posix(): p for p in tracked}
    for (rel, label), reason in _ALLOWLIST.items():
        assert reason.strip()
        assert rel in by_rel, f"allowlisted file no longer tracked: {rel}"
        assert patterns[label].search(_text(by_rel[rel]) or ""), (
            f"stale allowlist entry: {rel} no longer contains a {label}"
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
