"""One Postgres target for the legacy integration suites (audit G-05).

``aura_backend/test_*_integration.py`` need a real Postgres 16. They used to get one way only: a
``testcontainers`` container, which needs a Docker daemon -- so they ran on whichever machine
happened to have one and never in CI's ``postgres`` job, which already has a server. Now there
are two ways, behind one object with the ``testcontainers`` lifecycle the suites already use:

* ``AURA_TEST_DATABASE_URL`` set (CI's ``postgres`` job, a developer with a local server): a fresh
  database is created on that server for the suite and dropped afterwards, so suites that
  ``create_all`` and delete rows do not see each other's data;
* otherwise, a Docker daemon: the ``testcontainers`` container as before.

Neither present: :func:`available` is false and the suite skips with the reason it always gave.

Stdlib plus ``asyncpg`` (a runtime dependency) and, on the Docker path, ``testcontainers`` from the
dev lock. Used from the legacy ``unittest`` tree, so this module lives beside it, not under
``aura_core`` (which must stay free of test plumbing).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

ENV_VAR = "AURA_TEST_DATABASE_URL"
SKIP_REASON = (
    "needs a real Postgres 16: set AURA_TEST_DATABASE_URL to a server, or run where a Docker "
    "daemon is available"
)


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def available() -> bool:
    """A server URL in the environment, or a Docker daemon to start one."""
    return bool(os.environ.get(ENV_VAR)) or _docker_available()


def as_psycopg2_url(url: str) -> str:
    """The scheme ``testcontainers`` reports, which every suite then rewrites to the driver it
    wants (``postgresql+asyncpg``, plain ``postgresql``)."""
    scheme, rest = url.split("://", 1)
    if scheme in ("postgresql", "postgres"):
        scheme = "postgresql+psycopg2"
    return f"{scheme}://{rest}"


def with_database(url: str, name: str) -> str:
    """The same server and credentials, a different database."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _asyncpg_dsn(url: str) -> str:
    scheme, rest = url.split("://", 1)
    return f"postgresql://{rest}"


class ServerDatabase:
    """A fresh database on an existing server, with the ``testcontainers`` lifecycle."""

    def __init__(self, server_url: str) -> None:
        self._server_url = server_url
        self._name = f"aura_legacy_{uuid.uuid4().hex[:12]}"

    def start(self) -> "ServerDatabase":
        asyncio.run(self._run(f'CREATE DATABASE "{self._name}"'))
        return self

    def stop(self) -> None:
        asyncio.run(self._run(f'DROP DATABASE IF EXISTS "{self._name}" WITH (FORCE)'))

    def get_connection_url(self) -> str:
        return as_psycopg2_url(with_database(self._server_url, self._name))

    async def _run(self, statement: str) -> None:
        import asyncpg

        conn = await asyncpg.connect(_asyncpg_dsn(self._server_url))
        try:
            await conn.execute(statement)
        finally:
            await conn.close()


def container():
    """What ``PostgresContainer("postgres:16-alpine")`` used to be: call ``start()``, read
    ``get_connection_url()``, call ``stop()``."""
    server_url = os.environ.get(ENV_VAR)
    if server_url:
        return ServerDatabase(server_url)
    from testcontainers.postgres import PostgresContainer

    return PostgresContainer("postgres:16-alpine")
