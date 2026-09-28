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
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CLIENT = Path(__file__).resolve().parents[3] / "aura_client"

#: Directories whose contents are not shipped source.
_SKIP_PARTS = {"build", ".dart_tool", "ephemeral", ".git", "Pods"}

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
        if not _SKIP_PARTS & set(path.parts) and path.is_file()
    ]
    assert files, f"no Dart sources found under {_CLIENT}; this check would pass vacuously"
    return files


@pytest.mark.parametrize("pattern,label", _SECRET_PATTERNS, ids=[p[1] for p in _SECRET_PATTERNS])
def test_no_credential_literal_in_the_dart_client(pattern: str, label: str) -> None:
    compiled = re.compile(pattern)
    offenders: list[str] = []

    for path in _dart_sources():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - defensive
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                # The location, never the value: a failure message that quotes the credential
                # would republish it into CI logs, which is the problem rather than the report.
                offenders.append(f"{path.relative_to(_CLIENT)}:{number}")

    assert not offenders, (
        f"a {label} appears as a literal in the Dart client at:\n  "
        + "\n  ".join(offenders)
        + "\n\nSecrets reach a build through --dart-define (see scripts/deploy_client_desktop.sh), "
        "never through source. If this was committed, rotate it -- it is in git history and in "
        "every binary already shipped."
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
