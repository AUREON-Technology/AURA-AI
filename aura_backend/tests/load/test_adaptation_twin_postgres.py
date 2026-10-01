"""AP4 against a real Postgres: a pilot twin persisted, the live schema changed, and diffed.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN with password
auth (see ``test_adaptation_postgres.py``). One fresh database is migrated to head (so migration
``0010`` creates what :class:`PostgresTwinStore` queries); a second holds the HR pilot, which is
discovered live, snapshotted, altered with real DDL, rediscovered and snapshotted again.

What the unit tests (golden reports, in-memory store) cannot show and this does: JSONB element
bodies round-trip into the strict codec with an unchanged digest; the version key refuses a
duplicate; a row edited behind AURA's back is refused on read; and the diff of two *live*
discoveries finds the rename a real ``ALTER TABLE ... RENAME`` produces.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adaptation.labeling import LabelingPass
from aura_core.adaptation.twin import TwinService
from aura_core.adapters.connectors.openapi import artifacts_from_openapi, parse_spec
from aura_core.adapters.connectors.postgres import PostgresConnectorFactory
from aura_core.adapters.postgres_adaptation import PostgresTwinStore
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, Credential, IdentityMode
from aura_core.domain.discovery import DiscoveryBudget
from aura_core.domain.errors import ConflictError, DataIntegrityError

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]


def _goldens():
    spec = importlib.util.spec_from_file_location("pilot_goldens", _BACKEND / "scripts" / "pilot_discovery_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens()


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return 0.0


@pytest.fixture
async def store_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"ap4_{uuid.uuid4().hex[:10]}"
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


@pytest.fixture
async def pilot():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    dsn = await GOLDENS.load_pilot(_URL, "hr")
    yield dsn
    await GOLDENS.drop_database(_URL, dsn)


async def _discover(dsn: str):
    parts = urlsplit(_URL)
    target = urlsplit(dsn)
    descriptor = ConnectorDescriptor(
        id="hr-db", kind=ConnectorKind.POSTGRES, identity_mode=IdentityMode.SERVICE_ACCOUNT, display_name="HR",
        endpoint=urlunsplit(target._replace(netloc=f"{target.hostname}:{target.port}")),
        options={"user": parts.username, "sslmode": "disable"}, timeout_s=30.0,
    )
    connector = await PostgresConnectorFactory().open(descriptor, Credential(parts.password))
    try:
        return await connector.discover(DiscoveryBudget())
    finally:
        await connector.aclose()


def _openapi():
    return artifacts_from_openapi("hr-api", parse_spec(GOLDENS.spec_path("hr").read_bytes()), DiscoveryBudget())


async def test_a_live_twin_is_persisted_changed_and_diffed(store_url, pilot) -> None:
    import asyncpg

    store = PostgresTwinStore(store_url)
    service = TwinService(project_id="p0", registry=None, discovery=None, store=store,
                          labeling=LabelingPass(None), clock=_Clock())

    first = await service.snapshot_reports([await _discover(pilot), _openapi()], by="sa@example.com")
    assert first.created and first.version.version == 1
    meta, twin = await store.get("p0", 1)
    assert meta == first.version and twin.digest == first.version.digest  # JSONB round-trip is lossless

    again = await service.snapshot_reports([await _discover(pilot), _openapi()], by="sa@example.com")
    assert not again.created  # an unchanged live system mints no version

    conn = await asyncpg.connect(pilot)
    try:
        await conn.execute("ALTER TABLE employees RENAME COLUMN full_name TO display_name")
        await conn.execute("ALTER TABLE assets RENAME TO equipment")
        await conn.execute("ALTER TABLE employees ADD COLUMN badge_number text")
    finally:
        await conn.close()
    second = await service.snapshot_reports([await _discover(pilot), _openapi()], by="sa@example.com")
    assert second.created and second.version.version == 2 and second.version.parent_version == 1

    diff = await service.diff(1, 2)  # both sides read back from Postgres
    renames = {(r.old_id, r.new_id): r.reason for r in diff.renames}
    assert ("hr-db:column:public.employees.full_name", "hr-db:column:public.employees.display_name") in renames
    assert renames[("hr-db:table:public.assets", "hr-db:table:public.equipment")] == "attribute_overlap"
    assert "hr-db:column:public.employees.badge_number" in diff.added
    assert [v.version for v in await service.versions()] == [2, 1]

    with pytest.raises(ConflictError):
        await store.append(first.version, twin)  # append-only

    conn = await asyncpg.connect(store_url)
    try:
        await conn.execute(
            """
            UPDATE aura_adaptation_twin_elements
               SET body = jsonb_set(body, '{label}', '"Totally different"')
             WHERE version = 1 AND element_id = 'hr-db:table:public.employees'
            """
        )
    finally:
        await conn.close()
    with pytest.raises(DataIntegrityError):
        await store.get("p0", 1)
