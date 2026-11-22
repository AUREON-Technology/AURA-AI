"""/metrics fails closed in staging and production when no scrape token is configured.

Found live (docs/status/BACKEND_STATUS.md, 2026-11-07): on Railway the gateway is the edge, and
`/metrics` answered anonymously because `MetricsAuthMiddleware` was only mounted when
`AURA_METRICS_TOKEN` was set. A production deployment that forgets the token now serves 404
there -- the same answer nginx gives -- instead of publishing which safety bypasses are on.
Development keeps the open endpoint so a local Prometheus needs no setup.
"""

from __future__ import annotations

import dataclasses

from aura_core.config.settings import Environment
from aura_core.domain.identifiers import UserId


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("metrics-caller"), role="Admin")

    return TestClient(build_app(container, authenticator=authenticator))


def _configure(container, environment: Environment, token: str) -> None:
    observability = dataclasses.replace(container.settings.observability, metrics_token=token)
    container.settings = dataclasses.replace(
        container.settings, environment=environment, observability=observability
    )


def _path(container) -> str:
    return container.settings.observability.metrics_path


class TestMetricsFailClosed:
    async def test_production_without_token_hides_metrics(self, container) -> None:
        _configure(container, Environment.PRODUCTION, "")
        await container.startup()
        with _client(container) as client:
            assert client.get(_path(container)).status_code == 404

    async def test_production_with_token_requires_it(self, container) -> None:
        _configure(container, Environment.PRODUCTION, "scrape-secret")
        await container.startup()
        with _client(container) as client:
            assert client.get(_path(container)).status_code == 404
            ok = client.get(_path(container), headers={"authorization": "Bearer scrape-secret"})
            assert ok.status_code == 200

    async def test_scheme_is_case_insensitive(self, container) -> None:
        """RFC 7235: `bearer` and `BEARER` are the same scheme as `Bearer`."""
        _configure(container, Environment.PRODUCTION, "scrape-secret")
        await container.startup()
        with _client(container) as client:
            for header in ("bearer scrape-secret", "BEARER scrape-secret", "Bearer scrape-secret "):
                ok = client.get(_path(container), headers={"authorization": header})
                assert ok.status_code == 200, header

    async def test_wrong_scheme_wrong_token_and_non_ascii_are_all_404(self, container) -> None:
        _configure(container, Environment.PRODUCTION, "scrape-secret")
        await container.startup()
        with _client(container) as client:
            # The last is raw latin-1 bytes: the old str comparison raised TypeError on non-ASCII
            # input (a 500), where a mismatch is the right answer.
            for header in (
                "Basic c2NyYXBlLXNlY3JldA==",
                "Bearer wrong",
                "Bearer",
                "scrape-secret",
                "Bearer scrape-sécret".encode("latin-1"),
            ):
                refused = client.get(_path(container), headers={"authorization": header})
                assert refused.status_code == 404, header

    async def test_refusal_is_logged_without_the_token(self, container, caplog) -> None:
        _configure(container, Environment.PRODUCTION, "scrape-secret")
        await container.startup()
        with _client(container) as client, caplog.at_level("WARNING"):
            client.get(
                _path(container),
                headers={"authorization": "Bearer not-the-token", "user-agent": "GrafanaProbe/1"},
            )
            client.get(_path(container))
        text = caplog.text
        assert "bearer token does not match" in text
        assert "no Authorization header" in text
        assert "GrafanaProbe/1" in text
        assert "not-the-token" not in text
        assert "scrape-secret" not in text

    async def test_repeated_refusals_are_logged_once_per_reason(self, container, caplog) -> None:
        _configure(container, Environment.PRODUCTION, "scrape-secret")
        await container.startup()
        with _client(container) as client, caplog.at_level("WARNING"):
            for length in range(1, 6):
                client.get(_path(container), headers={"authorization": "Bearer " + "x" * length})
        assert caplog.text.count("bearer token does not match") == 1

    async def test_development_without_token_serves_metrics(self, container) -> None:
        _configure(container, Environment.DEVELOPMENT, "")
        await container.startup()
        with _client(container) as client:
            assert client.get(_path(container)).status_code == 200
