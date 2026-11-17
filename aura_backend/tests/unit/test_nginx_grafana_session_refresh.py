"""Grafana embeds keep the operator signed in past the 15-minute access token (audit INT-033).

nginx gates /grafana/ on `auth_request /_grafana_auth`, which asks the console's session route.
That route can refresh with the refresh cookie, but only when nginx opts in with
`X-Aura-Session-Refresh: 1` (aura_admin/app/api/auth/session/route.ts). The opt-in is only safe
together with the second half: refresh tokens are single use and nginx drops an auth_request
subrequest's response headers, so nginx must carry the rotated cookies back to the browser.
Opting in without that would spend the browser's only refresh token and sign the operator out
of the whole console. These tests pin both halves together, and pin the cookie attributes to the
console's own (`aura_admin/src/lib/session.ts`) so the two cannot drift.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NGINX = _REPO_ROOT / "deploy" / "nginx" / "templates" / "aura.conf.template"
_SESSION_TS = _REPO_ROOT / "aura_admin" / "src" / "lib" / "session.ts"


def _location(template: str, header: str) -> str:
    # The directive itself, at the start of a line -- not a mention of it in a comment.
    found = re.search(r"^\s*" + re.escape(header) + r"\s*\{", template, re.M)
    assert found, f"no block: {header}"
    start = found.start()
    depth = 0
    for i in range(template.index("{", start), len(template)):
        if template[i] == "{":
            depth += 1
        elif template[i] == "}":
            depth -= 1
            if depth == 0:
                return template[start : i + 1]
    raise AssertionError(f"unterminated block: {header}")


def _map(template: str, target: str) -> str:
    match = re.search(r"map\s+\$\w+\s+\$" + target + r"\s*\{(.*?)\}", template, re.S)
    assert match, f"no map defines ${target}"
    return match.group(1)


def _refresh_max_age_s() -> int:
    source = _SESSION_TS.read_text(encoding="utf-8")
    match = re.search(r"REFRESH_MAX_AGE_S\s*=\s*([0-9 *]+);", source)
    assert match, "REFRESH_MAX_AGE_S not found in session.ts"
    value = 1
    for factor in match.group(1).split("*"):
        value *= int(factor)
    return value


def test_the_auth_subrequest_opts_in_to_refresh() -> None:
    block = _location(_NGINX.read_text(encoding="utf-8"), "location = /_grafana_auth")
    assert re.search(r'proxy_set_header\s+X-Aura-Session-Refresh\s+"1"\s*;', block)


def test_grafana_captures_and_re_emits_both_rotated_cookies() -> None:
    template = _NGINX.read_text(encoding="utf-8")
    block = _location(template, "location /grafana/")
    for cookie in ("aura_access", "aura_refresh"):
        assert re.search(
            rf"auth_request_set\s+\${cookie}_cookie\s+\$upstream_cookie_{cookie}\s*;", block
        ), f"{cookie} is not captured from the auth subrequest"
        assert re.search(
            rf"add_header\s+Set-Cookie\s+\${cookie}_set_cookie\s+always\s*;", block
        ), f"{cookie} is not sent back to the browser"


def test_cookies_are_only_re_emitted_when_the_session_route_rotated_them() -> None:
    # An empty map value makes nginx skip the add_header, so an ordinary check (no refresh)
    # sends nothing and cannot clear or overwrite the browser's cookies.
    template = _NGINX.read_text(encoding="utf-8")
    for cookie in ("aura_access", "aura_refresh"):
        body = _map(template, f"{cookie}_set_cookie")
        assert re.search(r'""\s+""\s*;', body), f"{cookie}: no empty case"


def test_re_emitted_cookies_match_the_console_attributes() -> None:
    template = _NGINX.read_text(encoding="utf-8")
    refresh = _map(template, "aura_refresh_set_cookie")
    access = _map(template, "aura_access_set_cookie")
    for body in (access, refresh):
        for attribute in ("Path=/", "HttpOnly", "Secure", "SameSite=Strict"):
            assert attribute in body
    assert f"Max-Age={_refresh_max_age_s()}" in refresh
    # The access cookie lives exactly as long as the access token (900 s, `expires_in`).
    assert "Max-Age=900" in access
