"""Fitness functions against committing a client-side credential literal.

The incident these exist for (SEC-03 in ``docs/reports/AURA_FULL_SYSTEM_REVIEW.md``): a real
Google OAuth client secret was checked in as the ``defaultValue`` of
``AppConfig.googleDesktopClientSecret`` in ``aura_client/lib/core/config/config.dart``, which
compiled it into every desktop binary and left it in git history from 2026-09-12 until it was
rotated at Google. What makes it worth a test rather than a note is *how* it survived review:
the doc comment three lines above the literal stated, at length, that the value was deliberately
never hardcoded, and the upgrade roadmap said the same. Nothing disagreed with anything except
the code.

So the rule is not "remember not to do that". It is three scans, each of which would have caught
it on its own:

1. **No vendor-issued credential literal in any source file.** The narrowest and bluntest check:
   Google's own ``GOCSPX-`` prefix followed by the body it actually issues. Deliberately does not
   match the ``GOCSPX-...`` placeholder that several doc comments now use to *describe* this
   incident -- a test that forbade talking about the bug would be deleted the first time someone
   documented it.
2. **No ``String.fromEnvironment`` with a credential-shaped key may carry a non-empty
   ``defaultValue``.** This is the exact shape of the incident, and it generalises past the one
   provider: it catches a future ``defaultValue: 'sk-...'`` that layer 1 knows nothing about. A
   compile-time default is the one place a Dart credential can hide while looking like
   configuration, because the ``--dart-define`` at the call site reads as if the value came from
   the build.
3. **``googleDesktopClientSecret`` specifically still defaults to empty.** Redundant with layer 2
   on purpose: it pins the single member that regressed, by name, so the failure message names a
   file to open rather than a pattern class.

Scope note: layers 1 and 2 scan the whole repository, not just ``aura_backend/`` -- the value that
leaked was Dart, and ``tests/architecture/test_no_external_inference.py`` already records what it
cost to have scans that read only one file type.

They scan what ``git ls-files`` reports, not what happens to be sitting on disk, because
"committed" is the actual rule and the difference is not cosmetic. A checkout legitimately holds
real credentials that are correctly gitignored -- ``aura_backend/credentials.json``,
``deploy/.env``, and stale ``.claude/worktrees/`` copies still carrying the pre-rotation secret.
An earlier draft of this file walked the filesystem and failed on all of them, which is the
failure mode that gets a test deleted rather than fixed: it was reporting the ignore rules
working exactly as intended.

What this cannot claim, and the limits matter:

* It reads the **current** tree, so it cannot see a credential that was committed and later
  deleted. Those live on in history and need rotating at the provider, not a test.
* It does not stop a secret reaching a **binary** -- only a source file.
* It knows only the credential prefixes listed below.

It closes the accidental path -- someone pasting a working value into a config default to get
login working -- not the determined one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Text suffixes worth reading. An allowlist rather than a binary sniff, so a committed .apk or
# .png is never decoded: the point is to scan sources, and a credential baked into a build
# artifact is a different problem that cannot be enforced from here anyway.
TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".conf",
        ".dart",
        ".example",
        ".gradle",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".kts",
        ".md",
        ".plist",
        ".ps1",
        ".py",
        ".sh",
        ".template",
        ".tf",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)

TEXT_FILENAMES = frozenset({".env.example", "Dockerfile"})

# 1 MiB. Nothing hand-written here is larger; anything that is, is generated.
MAX_SCANNED_BYTES = 1024 * 1024

# Credential literals, keyed by what they are so a failure says which vendor to go rotate at.
#
# Every body pattern is long enough that the ellipsis placeholders these very docs use
# ("GOCSPX-...", "sk-...") cannot match. That is the difference between forbidding a committed
# secret and forbidding the sentence explaining that someone committed one.
CREDENTIAL_LITERALS = {
    "Google OAuth client secret": re.compile(r"GOCSPX-[A-Za-z0-9_\-]{12,}"),
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9]{24,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}"),
    "PEM private key block": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
    ),
}

# What makes a --dart-define key a credential rather than configuration. BACKEND_URL and
# GOOGLE_DESKTOP_CLIENT_ID must both stay allowed to have real defaults -- an OAuth *client id*
# is published inside the app by design.
CREDENTIAL_KEY_PATTERN = re.compile(
    r"SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY|CREDENTIAL|_TOKEN\b|\bTOKEN_", re.IGNORECASE
)

CONFIG_DART = REPO_ROOT / "aura_client" / "lib" / "core" / "config" / "config.dart"


def _scannable_files() -> list[Path]:
    """Every git-tracked source file worth reading.

    Asking git rather than walking the tree is what makes this test about *committed* secrets:
    build output, vendored dependencies, ``.claude/worktrees/`` and correctly-gitignored real
    credential files are all absent from the answer for free, with no exclusion list to keep in
    step with the repo's layout.
    """
    try:
        listing = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - no git present
        pytest.skip(f"cannot enumerate tracked files without git: {exc}")

    found: list[Path] = []
    for name in listing.split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        if path.name not in TEXT_FILENAMES and path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCANNED_BYTES:
                continue
        except OSError:  # pragma: no cover
            continue
        found.append(path)
    return found


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _from_environment_calls(content: str) -> list[tuple[str, str | None]]:
    """Every ``String.fromEnvironment(...)`` in a Dart source, as ``(key, defaultValue)``.

    Scans to the matching close paren rather than regexing the whole call, so a multi-line
    invocation -- which is how every one in this repo is formatted -- is read as a single unit.
    ``defaultValue`` is ``None`` when the call omits it; Dart then yields the empty string, which
    is exactly the state these tests want.
    """
    calls: list[tuple[str, str | None]] = []
    needle = "String.fromEnvironment("
    index = content.find(needle)
    while index != -1:
        cursor = index + len(needle)
        depth = 1
        while cursor < len(content) and depth:
            if content[cursor] == "(":
                depth += 1
            elif content[cursor] == ")":
                depth -= 1
            cursor += 1
        body = content[index + len(needle) : cursor - 1]

        key_match = re.search(r"""^\s*(['"])(?P<key>[^'"]*)\1""", body)
        if key_match:
            default_match = re.search(
                r"""defaultValue\s*:\s*(['"])(?P<value>(?:\\.|(?!\1)[^\\])*)\1""", body
            )
            calls.append(
                (key_match.group("key"), default_match.group("value") if default_match else None)
            )
        index = content.find(needle, cursor)
    return calls


@pytest.fixture(scope="module")
def source_files() -> list[Path]:
    files = _scannable_files()
    # Self-check: if the walk silently matched nothing, every assertion below would pass
    # vacuously, which is the one failure mode a scanning test cannot afford.
    assert CONFIG_DART in files, (
        "the source walk did not reach aura_client/lib/core/config/config.dart -- the scan is not "
        "covering the tree it claims to, so every check below would pass vacuously"
    )
    return files


def test_no_credential_literal_in_any_source_file(source_files: list[Path]) -> None:
    """Layer 1: no vendor-issued credential literal appears in a source file."""
    violations: list[str] = []
    for path in source_files:
        content = _read(path)
        for kind, pattern in CREDENTIAL_LITERALS.items():
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}: {kind}")

    assert not violations, (
        "Credential literal(s) committed to source:\n  "
        + "\n  ".join(violations)
        + "\n\nRotate the credential at the provider first -- it is in git history now, so "
        "deleting the line is not sufficient -- then supply it at build or run time instead "
        "(--dart-define for the Flutter client, an untracked deploy/.env for the backend)."
    )


def test_no_credential_dart_define_has_a_non_empty_default(source_files: list[Path]) -> None:
    """Layer 2: a credential-shaped ``--dart-define`` key must not ship a baked-in default.

    Generalises past the one secret that leaked: any ``defaultValue`` on a key naming a secret,
    password, API key or token is a compiled-in credential whoever issued it.
    """
    violations: list[str] = []
    for path in source_files:
        if path.suffix != ".dart":
            continue
        for key, default in _from_environment_calls(_read(path)):
            if default and CREDENTIAL_KEY_PATTERN.search(key):
                relative = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{relative}: String.fromEnvironment('{key}') has a default")

    assert not violations, (
        "Credential-shaped compile-time default(s):\n  "
        + "\n  ".join(violations)
        + "\n\nA credential must have no defaultValue: leave it empty so a build without the "
        "matching --dart-define fails closed and says so, rather than silently shipping whatever "
        "was committed here."
    )


def test_desktop_oauth_client_secret_still_defaults_to_empty() -> None:
    """Layer 3: the member that actually regressed, pinned by name.

    Redundant with layer 2, and kept anyway, because this is the one that points at a file to
    open. The desktop flow authenticates with PKCE alone (RFC 8252 section 8.5), so an empty
    default is not a placeholder awaiting a value -- it is the finished design.
    """
    calls = dict(_from_environment_calls(_read(CONFIG_DART)))

    assert "GOOGLE_DESKTOP_CLIENT_SECRET" in calls, (
        "GOOGLE_DESKTOP_CLIENT_SECRET is no longer read in config.dart. If desktop login "
        "genuinely dropped support for a confidential OAuth client, delete this test in the same "
        "commit -- do not leave it asserting something that moved."
    )
    default = calls["GOOGLE_DESKTOP_CLIENT_SECRET"]
    assert not default, (
        "AppConfig.googleDesktopClientSecret ships a non-empty default in "
        "aura_client/lib/core/config/config.dart. That value is compiled into every desktop "
        "binary and committed to git history. Desktop login needs no secret at all -- see that "
        "member's doc comment and core/auth/desktop_oauth_client_io.dart."
    )


def test_desktop_oauth_client_id_may_keep_its_public_default() -> None:
    """The mirror of layer 3, so the rule above is not read as "no defaults in config.dart".

    An OAuth *client id* is a public identifier that is meant to be compiled in, and it must stay
    equal to ``aura_backend/config.py::google_desktop_client_id`` or ``verify_identity_token``
    rejects every desktop login on its ``aud`` check. Asserting it is present stops a future
    over-eager "remove the defaults" sweep from breaking sign-in in a way nothing else notices.
    """
    calls = dict(_from_environment_calls(_read(CONFIG_DART)))

    assert calls.get("GOOGLE_DESKTOP_CLIENT_ID"), (
        "AppConfig.googleDesktopClientId lost its default. A client id is not a secret; it is the "
        "app's public identity and the backend's expected token audience."
    )
