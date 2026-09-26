import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from config import settings
from infrastructure.db.base import Base, with_asyncpg_driver
from infrastructure.db import models  # noqa: F401 — registers all tables on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url_from_secret_file() -> str | None:
    """Build the DSN from a Docker secret file, the way aura_service.py hydrates
    AURA_DATABASE_URL at app startup (SEC-08: the password never sits in an env var on
    disk). `docker compose run ... alembic` invokes this module directly, bypassing that
    hydration, so deploy/docker-compose.prod.yml's AURA_DATABASE_URL_FILE would otherwise
    be silently ignored and this would fall through to the localhost default."""
    path = os.environ.get("AURA_DATABASE_URL_FILE")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        password = handle.read().strip()
    if not password:
        return None
    host = os.environ.get("POSTGRES_HOST", "postgres")
    return f"postgresql+asyncpg://aura:{password}@{host}:5432/aura"


# Single source of truth for the connection string: config.settings (populated from
# DATABASE_URL), falling back to the Docker-secret-file DSN when DATABASE_URL itself was
# never set, then to localhost as a last resort for a bare local run with neither.
# `with_asyncpg_driver` (infrastructure/db/base.py) is shared with the live FastAPI runtime's
# own engine construction -- both need the identical bare-DSN-to-asyncpg rewrite, and drift
# between two copies of this logic is exactly how the runtime path went unfixed after this one
# already carried the fix (see that function's own docstring for the production incident).
config.set_main_option(
    "sqlalchemy.url",
    with_asyncpg_driver(
        settings.database_url or _database_url_from_secret_file() or "postgresql+asyncpg://localhost/aura"
    ),
)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
