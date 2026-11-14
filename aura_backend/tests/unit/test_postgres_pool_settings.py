"""The asyncpg pool is bounded in time as well as size, and its size is a setting (audit FSA-073).

``PostgresPool`` passed only ``min_size``/``max_size`` to asyncpg: no ``command_timeout`` (a hung
statement held its connection forever) and the per-process pool size was a constant nothing in
the deployment could tune against Postgres's ``max_connections``. The legacy SQLAlchemy engine
had the same two gaps and was never disposed. Every per-process figure now comes from settings,
so the deployment's connection budget can be computed (``test_postgres_connection_budget.py``).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

asyncpg = pytest.importorskip("asyncpg")

from aura_core.adapters.postgres_store import PostgresPool  # noqa: E402
from aura_core.config.settings import InfrastructureSettings  # noqa: E402

URL = "postgresql://aura:secret@db.internal:5432/aura"


class _Recorder:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def __call__(self, dsn: str, **kwargs):
        self.kwargs = kwargs
        return object()


class TestThePoolPassesItsBoundsToAsyncpg:
    async def test_command_timeout_and_inactive_lifetime_reach_create_pool(self, monkeypatch) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(asyncpg, "create_pool", recorder)
        pool = PostgresPool(
            URL, min_size=1, max_size=3, command_timeout_s=45.0, max_inactive_connection_lifetime_s=120.0
        )
        await pool._get_pool()
        assert recorder.kwargs == {
            "min_size": 1,
            "max_size": 3,
            "command_timeout": 45.0,
            "max_inactive_connection_lifetime": 120.0,
        }


class TestTheSettingsCarryThePoolBounds:
    def test_defaults(self, monkeypatch) -> None:
        for key in (
            "AURA_POSTGRES_POOL_MIN_SIZE", "AURA_POSTGRES_POOL_MAX_SIZE",
            "AURA_POSTGRES_COMMAND_TIMEOUT_S", "AURA_POSTGRES_MAX_INACTIVE_CONNECTION_LIFETIME_S",
            "AURA_LEGACY_DB_POOL_SIZE", "AURA_LEGACY_DB_MAX_OVERFLOW",
        ):
            monkeypatch.delenv(key, raising=False)
        infra = InfrastructureSettings.from_env()
        assert (infra.postgres_pool_min_size, infra.postgres_pool_max_size) == (2, 10)
        assert infra.postgres_command_timeout_s == 60.0
        assert infra.postgres_max_inactive_connection_lifetime_s == 300.0
        assert (infra.legacy_db_pool_size, infra.legacy_db_max_overflow) == (5, 10)

    def test_the_environment_overrides_them(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_POSTGRES_POOL_MIN_SIZE", "1")
        monkeypatch.setenv("AURA_POSTGRES_POOL_MAX_SIZE", "4")
        monkeypatch.setenv("AURA_POSTGRES_COMMAND_TIMEOUT_S", "15")
        monkeypatch.setenv("AURA_POSTGRES_MAX_INACTIVE_CONNECTION_LIFETIME_S", "90")
        monkeypatch.setenv("AURA_LEGACY_DB_POOL_SIZE", "2")
        monkeypatch.setenv("AURA_LEGACY_DB_MAX_OVERFLOW", "1")
        infra = InfrastructureSettings.from_env()
        assert (infra.postgres_pool_min_size, infra.postgres_pool_max_size) == (1, 4)
        assert infra.postgres_command_timeout_s == 15.0
        assert infra.postgres_max_inactive_connection_lifetime_s == 90.0
        assert infra.postgres_connections_per_process == 4 + 2 + 1

    def test_a_pool_smaller_than_its_minimum_is_refused(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_POSTGRES_POOL_MIN_SIZE", "5")
        monkeypatch.setenv("AURA_POSTGRES_POOL_MAX_SIZE", "2")
        from aura_core.domain.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            InfrastructureSettings.from_env()


class TestTheContainerBuildsThePoolFromSettings:
    def test_the_shared_pool_carries_the_configured_bounds(self) -> None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        settings = load_settings(refresh=True, validate=False)
        infra = replace(
            settings.infrastructure, database_url=URL, postgres_pool_min_size=1, postgres_pool_max_size=7,
            postgres_command_timeout_s=12.0, postgres_max_inactive_connection_lifetime_s=99.0,
        )
        container = Container(replace(settings, infrastructure=infra))
        pool = container._postgres_pool()
        assert (pool._min, pool._max) == (1, 7)
        assert pool._command_timeout_s == 12.0
        assert pool._max_inactive_connection_lifetime_s == 99.0


class TestTheLegacyEngineIsBoundedAndDisposed:
    @pytest.fixture(autouse=True)
    def _fresh_engine_module(self, monkeypatch):
        import infrastructure.db.base as base
        from config import settings

        monkeypatch.setattr(base, "_engine", None)
        monkeypatch.setattr(base, "_session_factory", None)
        monkeypatch.setattr(settings, "database_url", URL)
        yield
        base._engine = None
        base._session_factory = None

    async def test_pool_size_and_overflow_come_from_the_environment(self, monkeypatch) -> None:
        import infrastructure.db.base as base

        monkeypatch.setenv("AURA_LEGACY_DB_POOL_SIZE", "3")
        monkeypatch.setenv("AURA_LEGACY_DB_MAX_OVERFLOW", "2")
        engine = base.get_engine()
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 2
        await base.dispose_engine()

    async def test_dispose_releases_the_engine_so_the_next_use_builds_a_new_one(self) -> None:
        import infrastructure.db.base as base

        first = base.get_engine()
        base.get_session_factory()
        await base.dispose_engine()
        assert base._engine is None and base._session_factory is None
        assert base.get_engine() is not first
        await base.dispose_engine()

    def test_the_gateway_disposes_the_legacy_engine_on_shutdown(self) -> None:
        import aura_service
        import infrastructure.db.base as base

        assert base.dispose_engine in aura_service.app.router.on_shutdown

    def test_the_worker_shutdown_disposes_the_legacy_engine_too(self, monkeypatch) -> None:
        import infrastructure.db.base as base
        import tasks

        base.get_engine()
        monkeypatch.setattr(tasks, "_worker_container", None)
        tasks._shutdown_worker_container()
        assert base._engine is None
