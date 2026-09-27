"""Shared configuration for the live-target certification suite.

`tests/security/` proves the authorization invariants against the in-memory substrate through the
real ASGI app. That is genuine coverage and it is not duplicated here. What it cannot do is say
anything about **the deployment**: a production audit found a gateway whose behaviour nobody had
observed, and no amount of green local tests changed that.

So this suite talks to a running deployment over HTTP, with real tokens, and is opt-in for two
reasons: it needs credentials the repository must never hold, and it writes to whatever target it
is pointed at.

Run it with::

    AURA_CERT_BASE_URL=https://<host> \\
    AURA_CERT_TOKEN_A=<access token for synthetic user A> \\
    AURA_CERT_TOKEN_B=<access token for synthetic user B> \\
    pytest tests/certification -m cert

Absent credentials it **skips**, and a skip is not a pass. `AURA_PRODUCTION_READINESS_MATRIX.md`
records the gate as BLOCKED until a run produces evidence.

**On credentials.** Do not put a signing secret here. The operator mints two short-lived access
tokens (`scripts/loadtest/auth.py` documents the `mint` path) and exports them for one run; the
secret stays on the operator's machine and the tokens expire. Tokens are never logged by this
suite -- `_redact` exists so a failure message cannot carry one into CI output.

**On synthetic identities.** Both users must be dedicated synthetic accounts, never a real
person's. The whole point is attempting cross-user access, and a real account would mean
attempting it against real data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

pytest.importorskip("httpx")
import httpx  # noqa: E402

#: Prefix for everything this suite creates, so a leftover row is identifiable as ours at a
#: glance and a cleanup sweep can find it by name alone.
SYNTHETIC_PREFIX = "aura-cert-synthetic"

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass(frozen=True)
class Target:
    base_url: str
    token_a: str
    token_b: str

    def headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _redact(text: str, target: Target) -> str:
    """Never let a token reach an assertion message, a log or a CI artifact."""
    for secret in (target.token_a, target.token_b):
        if secret:
            text = text.replace(secret, "<redacted-token>")
    return text


@pytest.fixture(scope="session")
def target() -> Target:
    base = (os.environ.get("AURA_CERT_BASE_URL") or "").rstrip("/")
    token_a = os.environ.get("AURA_CERT_TOKEN_A") or ""
    token_b = os.environ.get("AURA_CERT_TOKEN_B") or ""

    missing = [
        name
        for name, value in (
            ("AURA_CERT_BASE_URL", base),
            ("AURA_CERT_TOKEN_A", token_a),
            ("AURA_CERT_TOKEN_B", token_b),
        )
        if not value
    ]
    if missing:
        pytest.skip(
            "live certification target not configured: "
            + ", ".join(missing)
            + ". This gate is BLOCKED, not passing -- see tests/certification/conftest.py."
        )
    if token_a == token_b:
        pytest.fail(
            "AURA_CERT_TOKEN_A and AURA_CERT_TOKEN_B are the same token. Cross-user isolation "
            "cannot be tested with one identity, and every probe would trivially 'pass'."
        )
    return Target(base_url=base, token_a=token_a, token_b=token_b)


@pytest.fixture(scope="session")
def client(target: Target):
    with httpx.Client(base_url=target.base_url, timeout=_TIMEOUT, follow_redirects=False) as c:
        yield c


def call(
    client: httpx.Client, target: Target, method: str, path: str, token: str, **kw
) -> httpx.Response:
    """One request, with the token attached and never echoed."""
    return client.request(method, path, headers=target.headers(token), **kw)


def explain(response: httpx.Response, target: Target) -> str:
    """A failure message an operator can act on, with tokens stripped."""
    body = response.text[:600]
    return _redact(
        f"{response.request.method} {response.request.url.path} -> {response.status_code}\n{body}",
        target,
    )
