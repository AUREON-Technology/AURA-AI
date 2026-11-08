"""The interactive API docs and the OpenAPI schema are not served in staging or production.

Found live, not by reading code: on 2026-11-07 `GET https://aureon-ai.tech/docs` and
`/openapi.json` answered 200 to an anonymous request (`docs/reports/
AURA_PRODUCTION_READINESS_2026-11-07.md`). FastAPI mounts `/docs`, `/redoc` and `/openapi.json`
by default and nothing in `build_app` ever turned them off, so every live target published a
complete, machine-readable route map -- every admin route, every request schema, every field
name -- to anyone who asked. The routes themselves stay behind auth; the map is still a free
reconnaissance step an attacker would otherwise have to guess at.

Development keeps them: the schema is how the client generator and the reachability report
(`docs/guides/api-reachability.md`) are checked, and a developer reading `/docs` on a local
stack is the point of having them.
"""

from __future__ import annotations

import dataclasses

import pytest

from aura_core.config.settings import Environment
from aura_core.domain.identifiers import UserId

_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("docs-caller"), role="Admin")

    return TestClient(build_app(container, authenticator=authenticator))


def _run_in(container, environment: Environment) -> None:
    container.settings = dataclasses.replace(container.settings, environment=environment)


class TestOpenApiExposure:
    @pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
    async def test_docs_and_schema_are_not_served_in_production_like_environments(
        self, container, environment: Environment
    ) -> None:
        _run_in(container, environment)
        await container.startup()
        with _client(container) as client:
            for path in _DOC_PATHS:
                assert client.get(path).status_code == 404, f"{path} is served in {environment}"

    async def test_turning_them_off_does_not_touch_the_api_itself(self, container) -> None:
        """The hardening is about the map, not the territory: a real route still answers."""
        _run_in(container, Environment.PRODUCTION)
        await container.startup()
        with _client(container) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/api/v3/capabilities").status_code == 200

    async def test_docs_stay_available_in_development(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            for path in _DOC_PATHS:
                assert client.get(path).status_code == 200, f"{path} missing in development"
