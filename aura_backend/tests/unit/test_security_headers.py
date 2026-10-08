"""The response header floor and the metrics gate do not depend on nginx being in front.

Found live, not by reading code. A probe of the Railway Control Plane
(`aura-ai-production-03a4.up.railway.app`, 2026-09-27) returned a correct v3 gateway with **no**
security headers of any kind, and served `/metrics` unauthenticated to the public internet --
publishing `aura_insecure_mode` (which safety bypasses are on), `aura_dependency_up` (which
dependencies are down) and retrieval-quality histograms.

Neither is a new decision: `deploy/nginx/templates/aura.conf.template` already set the headers
and already answered `/metrics` with `deny all; return 404;`. The gap was architectural -- the
policy lived in a component that one deploy target does not have. So it moved into the app, and
nginx now strips the duplicates it would otherwise stack.

These pin both halves, because either alone is a regression waiting to happen: the app must send
the headers, *and* nginx must hide the upstream copies so a response through both carries one of
each.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aura_core.api.security_headers import DEFAULT_HEADERS, HSTS_HEADER
from aura_core.domain.identifiers import UserId

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NGINX = _REPO_ROOT / "deploy" / "nginx" / "templates" / "aura.conf.template"
_HIDDEN = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Strict-Transport-Security",
)


def _client(container, **kw):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("headers-caller"), role="Admin")

    return TestClient(build_app(container, authenticator=authenticator), **kw)


class TestHeaderFloor:
    async def test_every_response_carries_the_floor(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.get("/health/live")

        for name, value in DEFAULT_HEADERS.items():
            assert response.headers.get(name) == value, f"{name} missing"

    async def test_an_error_response_carries_it_too(self, container) -> None:
        """The case a route-level decorator would miss. A 404 body is still a response an
        attacker can reach, and `nosniff` matters most on content nobody designed."""
        await container.startup()
        with _client(container) as client:
            response = client.get("/no/such/path")

        assert response.status_code == 404
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    async def test_hsts_only_over_tls(self, container) -> None:
        """Over plaintext it is meaningless, and a browser that caches it from
        http://127.0.0.1 will refuse plain HTTP to that host for two years."""
        await container.startup()
        with _client(container, base_url="http://testserver") as client:
            assert HSTS_HEADER[0] not in client.get("/health/live").headers
        with _client(container, base_url="https://testserver") as client:
            assert client.get("/health/live").headers.get(HSTS_HEADER[0]) == HSTS_HEADER[1]

    async def test_a_forwarded_proto_of_https_is_enough(self, container) -> None:
        """Behind a proxy the ASGI scheme is the internal hop. Trusting the header only ever
        makes the response stricter, so a forged value cannot weaken anything."""
        await container.startup()
        with _client(container, base_url="http://testserver") as client:
            response = client.get("/health/live", headers={"X-Forwarded-Proto": "https"})

        assert response.headers.get(HSTS_HEADER[0]) == HSTS_HEADER[1]

    async def test_no_csp_is_sent_by_default(self, container) -> None:
        """Left to the edge: this origin also fronts the operator console and Grafana, whose
        policies are much looser than the API's `default-src 'none'`."""
        await container.startup()
        with _client(container) as client:
            assert "Content-Security-Policy" not in client.get("/health/live").headers

    async def test_the_floor_can_be_turned_off_without_a_code_change(self, container) -> None:
        """An escape hatch that needs no deploy of new code, in case the floor breaks a client."""
        import dataclasses

        from aura_core.api import Principal, build_app
        from fastapi.testclient import TestClient

        container.settings = dataclasses.replace(
            container.settings,
            security=dataclasses.replace(
                container.settings.security, security_headers_enabled=False
            ),
        )

        async def authenticator(request):
            return Principal(user_id=UserId("headers-caller"), role="Admin")

        await container.startup()
        with TestClient(build_app(container, authenticator=authenticator)) as client:
            assert "X-Frame-Options" not in client.get("/health/live").headers


class TestMetricsGate:
    async def test_metrics_is_open_when_no_token_is_configured(self, container) -> None:
        """The VPS case: nginx answers the public path with 404 and Prometheus scrapes
        `gateway:8000/metrics` on the internal network. Requiring a token by default would break
        that correct deployment to fix the misconfigured one."""
        await container.startup()
        with _client(container) as client:
            assert client.get(container.settings.observability.metrics_path).status_code == 200

    @pytest.fixture()
    def tokened(self, container):
        import dataclasses

        container.settings = dataclasses.replace(
            container.settings,
            observability=dataclasses.replace(
                container.settings.observability, metrics_token="s3cret-scrape-token"
            ),
        )
        return container

    async def test_a_configured_token_is_required(self, tokened) -> None:
        await tokened.startup()
        path = tokened.settings.observability.metrics_path
        with _client(tokened) as client:
            # 404, not 401: an endpoint that announces itself to an unauthenticated caller tells
            # them the stack and that there is something here worth guessing at. nginx returns
            # 404 for the same path, so both targets behave identically.
            assert client.get(path).status_code == 404
            assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 404
            assert client.get(path, headers={"Authorization": "s3cret-scrape-token"}).status_code == 404
            # Status only. Whether the body holds real samples depends on prometheus_client
            # being installed and metrics_enabled, which is the renderer's concern, not the
            # gate's -- asserting on it here would make this test fail for an unrelated reason.
            assert client.get(
                path, headers={"Authorization": "Bearer s3cret-scrape-token"}
            ).status_code == 200

    async def test_the_gate_does_not_touch_other_paths(self, tokened) -> None:
        await tokened.startup()
        with _client(tokened) as client:
            assert client.get("/health/live").status_code == 200


class TestNginxDoesNotStackDuplicates:
    """The other half. Two `X-Frame-Options` values are worse than one -- some browsers discard a
    duplicated one entirely, so adding the app's headers without this would have *weakened* the
    VPS edge that was already correct."""

    def test_both_https_server_blocks_hide_the_upstream_copies(self) -> None:
        template = _NGINX.read_text(encoding="utf-8")

        # Server-level `add_header` marks an HTTPS server block that terminates TLS and proxies.
        server_level = len(re.findall(r"^    add_header Strict-Transport-Security", template, re.M))
        assert server_level >= 2, "expected the production and dev HTTPS server blocks"

        for header in _HIDDEN:
            hidden = len(re.findall(rf"^    proxy_hide_header {re.escape(header)};", template, re.M))
            assert hidden == server_level, (
                f"{header}: hidden in {hidden} server block(s) but set in {server_level}. "
                "Every block that adds it must also hide the gateway's own copy."
            )

    def test_metrics_stays_refused_at_the_edge(self) -> None:
        """The app-level gate is opt-in, so nginx's refusal is still the thing protecting the
        VPS. It must not be dropped on the grounds that the app now has a token option."""
        template = _NGINX.read_text(encoding="utf-8")

        blocks = re.findall(r"location /metrics \{(.*?)\}", template, re.S)
        assert blocks, "nginx no longer has a /metrics location"
        for body in blocks:
            assert "deny all" in body and "404" in body
