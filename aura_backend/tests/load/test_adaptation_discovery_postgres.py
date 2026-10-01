"""AP3 against a real Postgres: a live capture of each pilot equals its recorded catalog.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN with password
auth (see ``test_adaptation_postgres.py`` for a one-line throwaway cluster). Each test loads a
pilot into a fresh database through the same ``load_pilot`` the golden recorder uses.

What the golden tests cannot show and this does: the catalog queries still produce exactly the
committed snapshot on a real server, so the goldens are not describing a system that no longer
exists; a real read-only role discovers the schema but is refused profiling on a table it cannot
read (recorded as an issue, not a failed run); and budgets truncate on a live capture.
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adapters.connectors.postgres import PostgresConnectorFactory
from aura_core.adapters.connectors.postgres_catalog import artifacts_from_catalog
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, Credential, IdentityMode
from aura_core.domain.discovery import DiscoveryBudget

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]


def _goldens():
    spec = importlib.util.spec_from_file_location("pilot_goldens", _BACKEND / "scripts" / "pilot_discovery_goldens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDENS = _goldens()


@pytest.fixture
async def pilot_db():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    created: list[str] = []

    async def load(pilot: str) -> str:
        dsn = await GOLDENS.load_pilot(_URL, pilot)
        created.append(dsn)
        return dsn

    yield load
    for dsn in created:
        await GOLDENS.drop_database(_URL, dsn)


def _descriptor(dsn: str, user: str, **options) -> ConnectorDescriptor:
    parts = urlsplit(dsn)
    endpoint = urlunsplit(parts._replace(netloc=f"{parts.hostname}:{parts.port}"))
    return ConnectorDescriptor(
        id="pilot-db", kind=ConnectorKind.POSTGRES, identity_mode=IdentityMode.SERVICE_ACCOUNT,
        display_name="Pilot", endpoint=endpoint, options={"user": user, "sslmode": "disable", **options},
        timeout_s=30.0,
    )


async def _discover(dsn: str, user: str, password: str, budget: DiscoveryBudget = DiscoveryBudget(), **options):
    connector = await PostgresConnectorFactory().open(_descriptor(dsn, user, **options), Credential(password))
    return await connector.discover(budget)


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
async def test_live_capture_equals_the_recorded_catalog(pilot_db, pilot: str) -> None:
    dsn = await pilot_db(pilot)
    parts = urlsplit(_URL)
    live = await _discover(dsn, parts.username, parts.password)
    catalog = json.loads((_BACKEND / "tests" / "fixtures" / "pilots" / pilot / "golden" / "catalog.json").read_text())
    recorded = artifacts_from_catalog("pilot-db", catalog, DiscoveryBudget())
    assert live == recorded, "the recorded catalog no longer matches a live capture -- re-record it"


async def test_a_read_only_role_discovers_but_cannot_profile_what_it_cannot_read(pilot_db) -> None:
    import asyncpg

    dsn = await pilot_db("hr")
    role, password = f"ap3_reader_{uuid.uuid4().hex[:6]}", uuid.uuid4().hex
    database = urlsplit(dsn).path.lstrip("/")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        await conn.execute(f'GRANT CONNECT ON DATABASE "{database}" TO {role}')
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}")
        await conn.execute(f"REVOKE SELECT ON employees FROM {role}")
        report = await _discover(dsn, role, password)
    finally:
        await conn.execute(f"DROP OWNED BY {role}")
        await conn.execute(f"DROP ROLE {role}")
        await conn.close()
    assert ("not_readable", "table:public.employees") in {(i.code, i.locator) for i in report.issues}
    assert report.get("table:public.employees").attributes["readable"] is False
    assert report.get("column:public.employees.salary") is not None  # structure is still visible
    assert report.get("profile:public.employees.email") is None  # but no profile without SELECT
    assert report.get("profile:public.leave_requests.status") is not None


async def test_budgets_truncate_a_live_capture(pilot_db) -> None:
    dsn = await pilot_db("ecommerce")
    parts = urlsplit(_URL)
    small = await _discover(dsn, parts.username, parts.password, DiscoveryBudget(max_objects=20))
    assert small.truncated and len(small.artifacts) <= 20
    no_profile = await _discover(dsn, parts.username, parts.password, DiscoveryBudget(sample_rows=0))
    assert not any(a.kind.value == "column_profile" for a in no_profile.artifacts)
    capped = await _discover(dsn, parts.username, parts.password, DiscoveryBudget(max_profiled_columns=5))
    assert capped.truncated and any(i.code == "profile_budget_exhausted" for i in capped.issues)


async def test_opt_in_values_come_back_only_when_asked(pilot_db) -> None:
    dsn = await pilot_db("hr")
    parts = urlsplit(_URL)
    report = await _discover(dsn, parts.username, parts.password, DiscoveryBudget(sample_values=True))
    assert report.get("profile:public.employees.status").attributes["values"] == ("active", "on_leave", "terminated")


async def test_schemas_option_narrows_discovery(pilot_db) -> None:
    dsn = await pilot_db("ecommerce")
    parts = urlsplit(_URL)
    report = await _discover(dsn, parts.username, parts.password, schemas="public")
    assert not any("shop." in a.locator for a in report.artifacts)
