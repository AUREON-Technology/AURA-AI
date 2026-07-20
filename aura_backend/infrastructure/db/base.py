import logging
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

logger = logging.getLogger("aura_backend.db")


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def is_configured() -> bool:
    return bool(settings.database_url)


def get_engine():
    """Lazily creates the async engine on first use so the app can still
    boot (and every non-sync endpoint keep working) when Postgres isn't
    configured yet — mirrors how services/rag.py degrades gracefully when
    Qdrant Cloud is unreachable rather than crashing at import time."""
    global _engine
    if _engine is None:
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Set it in .env / the environment "
                "to enable Postgres-backed sync (see docker-compose.yml's postgres service)."
            )
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)
        logger.info("Postgres async engine initialized.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
