"""Readiness must fail on an unmigrated database.

Phase A1 found a gateway answering /health/ready with 200 against a database that had never been
migrated: only the three tables PostgresPool creates for itself existed, and the eight platform
tables did not. These cover the probe that closes that gap, and the deliberate asymmetry in how
it treats a stale schema (gating) versus an unreachable database (not gating).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services.schema_readiness import build_schema_check, expected_head


class _Conn:
    def __init__(self, values):
        self._values = list(values)

    async def fetchval(self, *_args):
        return self._values.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Acquirer:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


class _Pool:
    """Stands in for PostgresPool: _get_pool() then acquire() as a context manager."""

    def __init__(self, values=None, error=None):
        self._values = values or []
        self._error = error

    async def _get_pool(self):
        if self._error is not None:
            raise self._error
        return _Acquirer(_Conn(self._values))


def _run(check):
    return asyncio.run(check())


class TestExpectedHead:
    def test_reads_the_head_from_the_packaged_migrations(self) -> None:
        """Derived from the scripts, so a new migration cannot leave a stale constant behind."""
        assert expected_head() == "0007"

    def test_unreadable_directory_returns_none(self, tmp_path: Path) -> None:
        assert expected_head(tmp_path / "does-not-exist") is None


class TestSchemaCheck:
    def test_never_migrated_database_is_not_ready(self) -> None:
        """The Phase A1 case: no alembic_version table at all."""
        result = _run(build_schema_check(_Pool(values=[None])))

        assert result["healthy"] is False
        assert result["revision"] is None
        assert result["expected"] == "0007"
        assert "alembic upgrade head" in result["detail"]

    def test_schema_behind_the_image_is_not_ready(self) -> None:
        result = _run(build_schema_check(_Pool(values=["alembic_version", "0001"])))

        assert result["healthy"] is False
        assert result["revision"] == "0001"
        assert result["expected"] == "0007"

    def test_schema_at_head_is_ready(self) -> None:
        result = _run(build_schema_check(_Pool(values=["alembic_version", "0007"])))

        assert result["healthy"] is True
        assert result["revision"] == "0007"

    def test_unreachable_database_does_not_gate(self) -> None:
        """A blip is transient and the chat path still serves; a stale schema is neither."""
        result = _run(build_schema_check(_Pool(error=OSError("connection refused"))))

        assert result["healthy"] is True
        assert "unverified" in result["detail"]


class TestContainerPlumbing:
    """The generic hook in aura_core that carries the check above, without knowing about Alembic."""

    def test_registered_check_appears_and_can_gate(self, container) -> None:
        async def failing() -> dict:
            return {"healthy": False, "detail": "schema behind"}

        container.register_readiness_check("schema", failing)
        health = asyncio.run(container.health())

        assert health["healthy"] is False
        assert health["checks"]["schema"]["detail"] == "schema behind"

    def test_healthy_check_leaves_readiness_alone(self, container) -> None:
        async def passing() -> dict:
            return {"healthy": True, "revision": "0003"}

        baseline = asyncio.run(container.health())["healthy"]
        container.register_readiness_check("schema", passing)
        health = asyncio.run(container.health())

        assert health["healthy"] is baseline
        assert health["checks"]["schema"]["revision"] == "0003"

    def test_a_throwing_check_is_reported_but_does_not_gate(self, container) -> None:
        """A defect in a probe must not evict a working gateway from the load balancer."""

        async def broken() -> dict:
            raise RuntimeError("boom")

        baseline = asyncio.run(container.health())["healthy"]
        container.register_readiness_check("schema", broken)
        health = asyncio.run(container.health())

        assert health["healthy"] is baseline
        assert "boom" in health["checks"]["schema"]["detail"]

    def test_no_checks_means_no_checks_key(self, container) -> None:
        """Unchanged payload shape for the in-memory deployments that register nothing."""
        assert "checks" not in asyncio.run(container.health())


class TestReadinessEndpoint:
    """The probe has to reach the HTTP status a load balancer actually gates on."""

    def _client(self, container):
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app

        async def authenticator(request):
            return Principal(user_id="probe", email="u@example.com", role="Admin")

        return TestClient(build_app(container, authenticator=authenticator))

    def test_stale_schema_makes_the_probe_return_503(self, container) -> None:
        async def stale() -> dict:
            return {"healthy": False, "expected": "0003", "revision": None}

        container.register_readiness_check("schema", stale)
        with self._client(container) as client:
            response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["checks"]["schema"]["expected"] == "0003"

    def test_migrated_schema_still_returns_200(self, container) -> None:
        async def current() -> dict:
            return {"healthy": True, "revision": "0003"}

        container.register_readiness_check("schema", current)
        with self._client(container) as client:
            response = client.get("/health/ready")

        assert response.status_code == 200


class TestRegistration:
    """Only Postgres deployments get the probe; in-memory ones have no migrations to be behind."""

    def _container(self, document_backend: str, memory_backend: str = "memory"):
        import dataclasses

        from aura_core.config.settings import load_settings
        from aura_core.container import Container

        settings = load_settings(refresh=True, validate=False)
        infra = dataclasses.replace(
            settings.infrastructure,
            document_backend=document_backend,
            memory_backend=memory_backend,
            database_url="postgresql://u:p@localhost:5432/db",
        )
        return Container(dataclasses.replace(settings, infrastructure=infra))

    def test_in_memory_deployment_registers_nothing(self, container) -> None:
        from aura_service import _register_schema_readiness

        _register_schema_readiness(container)

        assert asyncio.run(container.health()).get("checks") is None

    @pytest.mark.parametrize(
        "document_backend,memory_backend",
        [("postgres", "memory"), ("memory", "postgres")],
    )
    def test_any_postgres_backend_registers_the_probe(
        self, document_backend, memory_backend
    ) -> None:
        from aura_service import _register_schema_readiness

        container = self._container(document_backend, memory_backend)
        _register_schema_readiness(container)

        assert "schema" in container._readiness_checks
