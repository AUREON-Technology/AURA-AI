"""AP6 against a real Postgres: every compiled SQL template runs and returns schema-valid rows.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN with password
auth (see ``test_adaptation_postgres.py``). Each pilot is loaded into a fresh database, compiled
from its committed goldens, and every SQL template of the blueprint is executed through
:func:`render_sql` in a read-only transaction.

What the unit tests cannot show and this does: the rendered statements are valid PostgreSQL
(parameter typing of the optional ``($n IS NULL OR ...)`` filters, the ``LIMIT LEAST(...)`` bound,
quoted mixed-case identifiers); the rows they return match the output schema each tool declares
once converted to JSON (``Decimal`` -> number, timestamps -> ISO text); and hostile argument
values are bound data -- they match nothing and change nothing.
"""

from __future__ import annotations

import datetime as dt
import decimal
import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest

from aura_core.domain.output_schema import output_errors
from aura_core.domain.templates import TemplateKind, render_sql

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]


def _module(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _BACKEND / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISCOVERY = _module("pilot_discovery_goldens", "pilot_discovery_goldens.py")
TWINS = _module("pilot_twin_goldens", "pilot_twin_goldens.py")


def _json(value: Any) -> Any:
    """How a read adapter must present a row (AP7 will own this): JSON types only."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


@pytest.fixture
async def pilot_db():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    created: list[str] = []

    async def load(pilot: str) -> str:
        dsn = await DISCOVERY.load_pilot(_URL, pilot)
        created.append(dsn)
        return dsn

    yield load
    for dsn in created:
        await DISCOVERY.drop_database(_URL, dsn)


async def _sample(conn, template, name: str, column_by_param: dict[str, str]) -> Any:
    """A real value for a required parameter: the first non-null value of the column it filters."""
    column = column_by_param[name]
    select = template.sql
    quoted = '"' + column.replace('"', '""') + '"'
    table = '"' + select.schema.replace('"', '""') + '"."' + select.table.replace('"', '""') + '"'
    return await conn.fetchval(f"SELECT {quoted} FROM {table} WHERE {quoted} IS NOT NULL ORDER BY 1 LIMIT 1")


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
async def test_every_sql_template_runs_and_matches_its_schema(pilot_db, pilot: str) -> None:
    import asyncpg

    dsn = await pilot_db(pilot)
    compiled = TWINS.compile_pilot(pilot)
    templates = {t.id: t for t in compiled.blueprint.templates}
    conn = await asyncpg.connect(dsn)
    executed = 0
    try:
        async with conn.transaction(readonly=True):
            for tool in compiled.blueprint.tools:
                template = templates[tool.template]
                if template.kind is not TemplateKind.SQL:
                    continue
                by_param = {f.param: f.column for f in template.sql.filters + template.sql.any_of}
                arguments: dict[str, Any] = {}
                for parameter in tool.parameters:
                    if parameter.required:
                        arguments[parameter.name] = (
                            "a" if parameter.name == "query" else await _sample(conn, template, parameter.name, by_param)
                        )
                rendered = render_sql(template, arguments)
                rows = await conn.fetch(rendered.text, *rendered.values)
                data = [{k: _json(v) for k, v in dict(r).items()} for r in rows]
                assert output_errors(tool.output_schema, data) == [], (tool.name, rendered.text)
                if template.sql.limit_param:
                    assert len(rows) <= template.sql.max_rows
                executed += 1
    finally:
        await conn.close()
    assert executed == sum(1 for t in compiled.blueprint.templates if t.kind is TemplateKind.SQL)


async def test_hostile_arguments_are_bound_values(pilot_db) -> None:
    import asyncpg

    dsn = await pilot_db("hr")
    compiled = TWINS.compile_pilot("hr")
    templates = {t.id: t for t in compiled.blueprint.templates}
    search = templates["search_employees"]
    listing = templates["list_leave_requests"]
    conn = await asyncpg.connect(dsn)
    try:
        before = await conn.fetchval("SELECT count(*) FROM employees")
        # "%" would match every row if it reached a LIKE; containment is a literal substring test.
        for payload in ("'; DROP TABLE employees; --", '" OR 1=1 --', "%"):
            rendered = render_sql(search, {"query": payload, "limit": 500})
            assert await conn.fetch(rendered.text, *rendered.values) == []
            # An optional equality filter with an injection string matches nothing either.
            rendered = render_sql(listing, {"status": payload})
            assert await conn.fetch(rendered.text, *rendered.values) == []
        attendance = templates["list_attendance"]  # 60 seeded rows, above the 50-row cap
        rendered = render_sql(attendance, {"limit": 10_000})
        assert len(await conn.fetch(rendered.text, *rendered.values)) == attendance.sql.max_rows == 50
        assert await conn.fetchval("SELECT count(*) FROM employees") == before
    finally:
        await conn.close()


@pytest.fixture
async def migrated_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import subprocess
    import sys
    import uuid
    from urllib.parse import urlsplit, urlunsplit

    import asyncpg

    name = f"ap6_{uuid.uuid4().hex[:10]}"
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


async def test_a_compiled_blueprint_round_trips_through_postgres(migrated_url) -> None:
    import asyncpg

    from aura_core.adapters.postgres_adaptation import PostgresBlueprintStore
    from aura_core.domain.blueprint import BlueprintState
    from aura_core.domain.compilation import BlueprintRecord
    from aura_core.domain.errors import ConflictError, DataIntegrityError

    compiled = TWINS.compile_pilot("ecommerce")
    record = BlueprintRecord(
        project_id="p0", version=1, state=BlueprintState.PROPOSED, digest=compiled.blueprint.digest,
        created_at="2026-10-01T12:00:00+00:00", created_by="sa@example.com",
        plan_digest="sha256:" + "1" * 64, registry_digest="sha256:" + "2" * 64,
        blueprint=compiled.blueprint, suites=compiled.suites, dropped=compiled.dropped,
    )
    store = PostgresBlueprintStore(migrated_url)
    await store.append(record)
    loaded = await store.get("p0", 1)
    assert loaded == record and loaded.blueprint.digest == record.digest  # JSONB keeps the digest
    assert [r.version for r in await store.versions("p0")] == [1]
    with pytest.raises(ConflictError):
        await store.append(record)
    conn = await asyncpg.connect(migrated_url)
    try:
        await conn.execute(
            "UPDATE aura_adaptation_blueprints SET record = jsonb_set(record, '{blueprint,tools,0,sensitivity}', "
            "'\"public\"') WHERE version = 1"
        )
    finally:
        await conn.close()
    with pytest.raises(DataIntegrityError):
        await store.get("p0", 1)  # a hand-lowered sensitivity no longer matches the digest
