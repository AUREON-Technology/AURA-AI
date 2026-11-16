"""The ``source_ip`` an audit record carries must not be chosen by the caller.

The gateway runs uvicorn with ``--proxy-headers --forwarded-allow-ips *`` on Railway (Railway's
edge has no fixed address to name). With ``*``, uvicorn sets ``request.client`` to the *leftmost*
``X-Forwarded-For`` entry, which is whatever the client sent. The rate limiter never used that
value: it resolves the client from the right with ``security.trusted_proxy_count``
(``extract_client_ip``). The audit records did use it, so any caller could write an arbitrary
address into the audit trail. They now resolve the address the same way the limiter does.

The app is wrapped in uvicorn's own ``ProxyHeadersMiddleware`` configured exactly as the
Dockerfile configures it, so this reproduces the production behaviour, not a model of it.
"""

from __future__ import annotations

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import UserId

pytest.importorskip("fastapi")

SPOOFED = "6.6.6.6"
REAL_CLIENT = "198.51.100.4"


def _client(container):
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    async def as_user(request):
        return Principal(user_id=UserId("user-a"), email="user-a@example.com", role="User",
                         capabilities=())

    app = ProxyHeadersMiddleware(build_app(container, authenticator=as_user), trusted_hosts="*")
    return TestClient(app)


async def test_a_spoofed_forwarded_for_does_not_reach_the_audit_trail(container) -> None:
    # What a client sends, followed by the address Railway's edge appends for it.
    with _client(container) as client:
        response = client.get(
            "/api/v3/keys/data", headers={"X-Forwarded-For": f"{SPOOFED}, {REAL_CLIENT}"},
        )
    assert response.status_code == 200

    (event,) = [e for e in await container.audit.query() if e.action == "data_key.read"]
    assert event.source_ip == REAL_CLIENT


async def test_without_a_forwarded_header_the_peer_is_recorded(container) -> None:
    with _client(container) as client:
        assert client.get("/api/v3/keys/data").status_code == 200

    (event,) = [e for e in await container.audit.query() if e.action == "data_key.read"]
    assert event.source_ip == "testclient"
