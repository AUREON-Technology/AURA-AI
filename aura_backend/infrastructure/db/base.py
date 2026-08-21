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
        _engine = create_async_engine(url, pool_pre_ping=True, echo=False)
        logger.info("Postgres async engine initialized.")
    return _engine


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
