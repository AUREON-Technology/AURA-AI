"""Real-Postgres test databases, built the way production's are: by ``alembic upgrade head``.

No adapter creates its tables any more (CB-20 / roadmap M6: Alembic is the one schema authority),
so a test against a real Postgres must migrate its database first. A test that skipped this used
to pass only because the adapters ran their own ``CREATE TABLE IF NOT EXISTS`` on first use.

Used by the opt-in tests under ``tests/load`` (``AURA_TEST_DATABASE_URL``) and the two unit tests
that take ``AURA_TEST_POSTGRES_URL``; nothing here runs in the default suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

BACKEND = Path(__file__).resolve().parents[1]


def with_database(url: str, name: str) -> str:
    """``url`` pointed at database ``name`` (host, credentials and query kept)."""
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    """Run Alembic against ``database_url`` (a plain ``postgresql://`` DSN) from the backend root."""
    sqlalchemy_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    env = {**os.environ, "DATABASE_URL": sqlalchemy_url, "AURA_DATABASE_URL": sqlalchemy_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=300,
    )


def migrate(database_url: str, revision: str = "head") -> None:
    result = alembic(database_url, "upgrade", revision)
    assert result.returncode == 0, f"alembic upgrade {revision} failed:\n{result.stderr[-3000:]}"


@asynccontextmanager
async def migrated_database(admin_url: str, prefix: str, *, revision: str = "head") -> AsyncIterator[str]:
    """A new database on ``admin_url``'s server, migrated to ``revision`` (``"base"``: left empty);
    dropped afterwards."""
    import asyncpg

    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = with_database(admin_url, name)
        if revision != "base":
            migrate(url, revision)
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
