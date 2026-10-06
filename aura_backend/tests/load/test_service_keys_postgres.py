"""AR-4 against a real Postgres: migration 0013 and ``PostgresServiceKeyStore``.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN (see
``test_adaptation_postgres.py``).

What the in-memory store cannot show and this does:

* the migrated table round-trips a key exactly -- ``TEXT[]`` routes back to a ``frozenset``,
  ``TIMESTAMPTZ`` back to an aware UTC datetime -- through asyncpg's own type mapping;
* a revocation written by one store instance (one gateway worker) refuses the token on another
  instance's next ``authenticate``, with no cache in between;
* revoking twice keeps the first revocation's time and author;
* a duplicate key id is refused by the primary key, not silently overwritten.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adapters.postgres_service_keys import PostgresServiceKeyStore
from aura_core.domain.errors import AuthenticationError, ConflictError
from aura_core.security.service_keys import ServiceKeyService

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]


class _Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at


@pytest.fixture
async def migrated_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"ar4_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = urlunsplit(urlsplit(_URL)._replace(path=f"/{name}"))
    try:
        sqlalchemy_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        env = {**os.environ, "DATABASE_URL": sqlalchemy_url, "AURA_DATABASE_URL": sqlalchemy_url}
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=_BACKEND, env=env,
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stderr[-3000:]
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_keys_round_trip_and_revocation_crosses_workers(migrated_url) -> None:
    clock = _Clock()
    worker_a = ServiceKeyService(store=PostgresServiceKeyStore(migrated_url), clock=clock)
    worker_b = ServiceKeyService(store=PostgresServiceKeyStore(migrated_url), clock=clock)

    key, token = await worker_a.issue(name="svc-flame", by="sa@example.com",
                                      routes=["POST /api/v3/chat", "POST /api/v3/knowledge/documents"],
                                      rate_per_minute=6)
    seen = await worker_b.authenticate(token)
    assert seen == key  # every field, through asyncpg's TEXT[] and TIMESTAMPTZ mapping
    assert seen.created_at.tzinfo is not None and isinstance(seen.routes, frozenset)
    assert [k.key_id for k in await worker_b.list()] == [key.key_id]

    first = await worker_a.revoke(key.key_id, by="sa@example.com")
    with pytest.raises(AuthenticationError):
        await worker_b.authenticate(token)  # the other worker's very next call
    again = await worker_b.revoke(key.key_id, by="someone-else@example.com")
    assert (again.revoked_at, again.revoked_by) == (first.revoked_at, "sa@example.com")
    assert await worker_a.revoke("0" * 16, by="sa@example.com") is None


async def test_a_duplicate_key_id_is_refused(migrated_url) -> None:
    store = PostgresServiceKeyStore(migrated_url)
    service = ServiceKeyService(store=store, clock=_Clock())
    key, _ = await service.issue(name="svc-flame", by="sa@example.com")
    with pytest.raises(ConflictError):
        await store.put(key)


async def test_the_token_is_not_in_the_database(migrated_url) -> None:
    import asyncpg

    service = ServiceKeyService(store=PostgresServiceKeyStore(migrated_url), clock=_Clock())
    _, token = await service.issue(name="svc-flame", by="sa@example.com")
    conn = await asyncpg.connect(migrated_url)
    try:
        rows = await conn.fetch("SELECT * FROM aura_service_keys")
    finally:
        await conn.close()
    assert len(rows) == 1 and all(token not in str(value) for value in rows[0].values())
