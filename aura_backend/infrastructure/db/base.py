import logging
import os
from typing import AsyncGenerator, Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

logger = logging.getLogger("aura_backend.db")


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _database_url() -> str:
    return (
        settings.database_url
        or os.environ.get("DATABASE_URL")
        or os.environ.get("AURA_DATABASE_URL")
        or ""
    )


def is_configured() -> bool:
    return bool(_database_url())


def with_asyncpg_driver(url: str) -> str:
    """Force the ``+asyncpg`` driver onto a bare ``postgresql://``/``postgres://`` DSN.

    ``config.settings.database_url`` is deliberately un-normalized (SEC-08: it's read straight
    from ``DATABASE_URL``/``AURA_DATABASE_URL`` with no rewriting) because ``PostgresPool`` calls
    ``asyncpg`` directly, which doesn't care about a driver prefix. ``create_async_engine`` below
    goes through SQLAlchemy's dialect resolution instead, which does: a managed-Postgres
    provider's connection string (Railway, Heroku, RDS) is almost always the bare scheme, and
    without this, SQLAlchemy falls back to its default sync-capable driver lookup (``psycopg2``)
    rather than the ``asyncpg`` this image actually ships -- ``ModuleNotFoundError: No module
    named 'psycopg2'`` at engine construction, before any connection is attempted. Confirmed live
    in production: every ``/api/v3/sync`` call 500'd this way once ``DATABASE_URL`` was set to a
    bare Railway Postgres string. ``alembic/env.py`` already carried this exact fix for its own
    ``async_engine_from_config`` call; this module -- the live FastAPI runtime path -- had not.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def get_engine():
    """Lazily creates the async engine on first use so the app can still
    boot (and every non-sync endpoint keep working) when Postgres isn't
    configured yet — mirrors how services/rag.py degrades gracefully when
    Qdrant Cloud is unreachable rather than crashing at import time."""
    global _engine
    if _engine is None:
        url = _database_url()
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Set it in .env / the environment "
                "to enable Postgres-backed sync (see docker-compose.yml's postgres service)."
            )
        # Pool bounds are part of the deployment's connection budget (audit FSA-073): every
        # process holds this pool beside aura_core's asyncpg pool, and the compose files size
        # both so the worst case fits under Postgres's max_connections. Same defaults as
        # SQLAlchemy's own (5 + 10) when unset.
        _engine = create_async_engine(
            with_asyncpg_driver(url),
            pool_pre_ping=True,
            echo=False,
            pool_size=int(os.environ.get("AURA_LEGACY_DB_POOL_SIZE") or 5),
            max_overflow=int(os.environ.get("AURA_LEGACY_DB_MAX_OVERFLOW") or 10),
            pool_recycle=int(os.environ.get("AURA_LEGACY_DB_POOL_RECYCLE_S") or 1800),
        )
        logger.info("Postgres async engine initialized.")
    return _engine


async def dispose_engine() -> None:
    """Close the engine's connections and forget it, so the next use builds a fresh one.

    Called at gateway shutdown and when a Celery child exits (audit FSA-073): before this the
    legacy engine was never disposed, and every recycled worker left its connections for the
    server to time out. Safe when no engine was ever built.
    """
    global _engine, _session_factory
    engine, _engine = _engine, None
    _session_factory = None
    if engine is not None:
        await engine.dispose()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped session."""
    if not is_configured():
        # Raised here rather than in get_engine(): FastAPI resolves this
        # Depends *before* the route body, so an unconfigured Postgres used
        # to become an uncaught RuntimeError → 500 instead of the documented 503.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Postgres sync is not configured (DATABASE_URL unset).",
        )
    factory = get_session_factory()
    async with factory() as session:
        yield session
