"""The operator console sends its own security headers; nginx must not send a second set.

The console (aura_admin) sets HSTS, nosniff, Referrer-Policy, Permissions-Policy, X-Frame-Options
and a per-request nonce Content-Security-Policy itself. When nginx also added a static CSP on
`/console`, every response carried two policies, and browsers enforce both: the static one has no
nonce, so it either blocks the app's nonce'd inline scripts or is redundant.

Removing the lines is only half of it. nginx inherits the *server-level* `add_header` set into a
location that defines none -- and at server level that set is `X-Frame-Options: DENY` and
`default-src 'none'`, which would break the console outright. So each `/console` location keeps
exactly one `add_header`, with an empty value: nginx sends nothing for an empty value, and its
presence still stops the inheritance.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_NGINX = Path(__file__).resolve().parents[3] / "deploy" / "nginx"
_FILES = (_NGINX / "templates" / "aura.conf.template", _NGINX / "host-proxy.conf.example")
_APP_OWNED = (
    "Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy",
    "Permissions-Policy", "Content-Security-Policy", "X-Frame-Options",
)


def _console_blocks(text: str) -> list[str]:
    blocks = []
    for match in re.finditer(r"location /console \{", text):
        depth, i = 0, match.end() - 1
        while True:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        blocks.append(text[match.end():i])
    return blocks


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_console_locations_leave_security_headers_to_the_app(path: Path) -> None:
    blocks = _console_blocks(path.read_text(encoding="utf-8"))
    assert blocks, f"{path.name} has no /console location"
    for block in blocks:
        headers = re.findall(r"^\s*add_header\s+(\S+)\s+(\"[^\"]*\"|\S+)", block, re.M)
        doubled = [name for name, _ in headers if name in _APP_OWNED]
        assert not doubled, f"{path.name}: /console restates {doubled}, which the console sends itself"
        # The inheritance guard: at least one add_header, and none that sends anything.
        assert headers, f"{path.name}: /console would inherit the server's DENY/'none' headers"
        assert all(value == '""' for _, value in headers), headers


def test_the_template_counts_both_consoles() -> None:
    """Prod and dev vhosts each proxy a console."""
    assert len(_console_blocks(_FILES[0].read_text(encoding="utf-8"))) == 2


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_http2_stays_off(path: Path) -> None:
    """CLAUDE.md: h2 breaks the /api/v3/voice/live WebSocket upgrade."""
    assert not re.search(r"^\s*http2\s+on\s*;", path.read_text(encoding="utf-8"), re.M)
