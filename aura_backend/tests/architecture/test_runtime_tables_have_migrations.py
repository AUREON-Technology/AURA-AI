"""Every table a Postgres adapter creates at runtime has an Alembic migration (audit F-27).

Sixteen ``aura_*`` tables -- documents, chunks and memories among them -- existed only as
``CREATE TABLE IF NOT EXISTS`` inside the adapters, so ``alembic upgrade head`` on an empty
database did not produce the schema the code queries. Migration 0016 baselined them. This keeps
it that way: a new runtime table without a migration fails here, and the 0016 baseline is pinned
to the adapters' current DDL so the two definitions cannot drift apart silently.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
ADAPTERS = BACKEND / "aura_core" / "adapters"
VERSIONS = BACKEND / "alembic" / "versions"

_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(aura_\w+)")
_DDL = re.compile(
    r"(CREATE TABLE IF NOT EXISTS|CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+\s+ON|ALTER TABLE)\s+(aura_\w+)"
)


def _normalise(sql: str) -> str:
    return " ".join(textwrap.dedent(sql).split())


def _adapter_ddl() -> list[tuple[str, str, str]]:
    found = []
    for path in sorted(ADAPTERS.glob("postgres_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = _DDL.search(node.value)
                if match:
                    found.append((path.name, match.group(2), _normalise(node.value)))
    return found


def _baseline():
    spec = importlib.util.spec_from_file_location(
        "baseline_0016", VERSIONS / "0016_runtime_tables_baseline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_runtime_table_is_created_by_a_migration() -> None:
    migrated = set()
    for path in VERSIONS.glob("*.py"):
        migrated.update(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+\"?(aura_\w+)", path.read_text()))
        migrated.update(re.findall(r"create_table\(\s*[\"'](aura_\w+)", path.read_text()))
    runtime = {table for _, table, sql in _adapter_ddl() if sql.startswith("CREATE TABLE")}

    missing = sorted(runtime - migrated)
    assert not missing, f"tables created at runtime with no Alembic migration: {missing}"


def test_the_baseline_matches_the_adapters_ddl() -> None:
    baseline = _baseline()
    statements = {_normalise(s) for s in baseline.UPGRADE_STATEMENTS}
    expected = {sql for _, table, sql in _adapter_ddl() if table in baseline.TABLES}

    assert expected - statements == set(), (
        "an adapter's DDL for a baselined table changed -- add a migration for the change"
    )
