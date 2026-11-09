"""Alembic is the one schema authority: no code under ``aura_core`` issues DDL (CB-20, roadmap M6).

Sixteen ``aura_*`` tables used to exist only as ``CREATE TABLE IF NOT EXISTS`` inside the
adapters, run on first use (audit F-27). Migration 0016 baselined them; this change removed the
runtime copies. Two reasons it matters, both from the master roadmap's M6 entry:

* **Rollback.** DDL that runs when a new image starts is not reversible by rolling the image back,
  and no ``alembic downgrade`` knows about it. Only migrations have a down path
  (``tests/load/test_migration_roundtrip_postgres.py`` exercises it on a real Postgres).
* **One answer to "what is the schema?".** With two writers, a database's shape depended on which
  ran first. A database is now exactly what ``alembic upgrade head`` made it; an unmigrated one
  fails ``/health/ready`` (``services/schema_readiness.py``) instead of being patched from the
  request path.

The scan reads every string literal under ``aura_core`` except docstrings (which may *describe*
DDL), with f-string placeholders standing in for names, so ``f"CREATE TABLE {name}"`` is caught.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
CORE = BACKEND / "aura_core"
VERSIONS = BACKEND / "alembic" / "versions"

_OBJECT = r"(?:TABLE|INDEX|EXTENSION|SCHEMA|TYPE|SEQUENCE|VIEW|MATERIALIZED\s+VIEW|FUNCTION|TRIGGER)"
#: A statement, not a bare keyword: the object kind must be followed by a name (or IF [NOT] EXISTS),
#: so a privilege name such as ``"CREATE VIEW"`` in the MySQL connector's grant check is not DDL.
_DDL = re.compile(
    r"\b(?:CREATE\s+(?:(?:UNIQUE|TEMP|TEMPORARY|UNLOGGED|OR\s+REPLACE)\s+)*" + _OBJECT
    + r"|ALTER\s+" + _OBJECT + r"|DROP\s+" + _OBJECT + r")"
    + r"\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?[\"\w{]"
)
_REFERENCE = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+(aura_\w+)")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, owners) and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _sql_literals(source: str) -> list[tuple[int, str]]:
    """Every non-docstring string literal, f-strings rendered with ``{x}`` for each placeholder."""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "{x}" for v in node.values)
            found.append((node.lineno, text))
            skip.update(id(v) for v in node.values)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            found.append((node.lineno, node.value))
    return found


def _core_files() -> list[Path]:
    return sorted(p for p in CORE.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_code_under_aura_core_issues_ddl() -> None:
    offenders = [
        f"{path.relative_to(BACKEND)}:{line}: {_DDL.search(text).group(0)!r}"
        for path in _core_files()
        for line, text in _sql_literals(path.read_text())
        if _DDL.search(text)
    ]
    assert not offenders, (
        "DDL in aura_core -- schema changes belong in an Alembic migration "
        "(alembic/versions/), not in code that runs when the app starts:\n  " + "\n  ".join(offenders)
    )


def test_every_aura_table_the_code_queries_is_created_by_a_migration() -> None:
    queried: dict[str, set[str]] = {}
    for path in _core_files():
        for _line, text in _sql_literals(path.read_text()):
            for table in _REFERENCE.findall(text):
                queried.setdefault(table, set()).add(path.name)
    assert queried, "the scan found no SQL at all -- the pattern is broken"

    migrated: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        text = path.read_text()
        migrated.update(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+\"?(aura_\w+)", text))
        migrated.update(re.findall(r"create_table\(\s*[\"'](aura_\w+)", text))

    missing = {table: sorted(files) for table, files in queried.items() if table not in migrated}
    assert not missing, f"tables queried by aura_core that no migration creates: {missing}"


def test_nothing_in_the_backend_still_calls_the_removed_schema_bootstrap() -> None:
    """The adapters' ``_ensure_schema`` methods are gone; a caller outside ``aura_core`` (a script,
    a service) would only fail at run time, as ``scripts/seed_dev_console.py`` did after #225."""
    callers = []
    for path in sorted(BACKEND.rglob("*.py")):
        parts = path.relative_to(BACKEND).parts
        if parts[0] in {"tests", "alembic"} or "__pycache__" in parts or path.name.startswith("test_"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "._ensure_schema(" in line:
                callers.append(f"{path.relative_to(BACKEND)}:{number}")
    assert not callers, f"calls to a schema bootstrap that no longer exists: {callers}"


class TestTheScanItself:
    """The detector is the whole test, so check that it fires on what it must and only that."""

    def _hits(self, source: str) -> list[str]:
        return [text for _line, text in _sql_literals(source) if _DDL.search(text)]

    def test_it_catches_plain_and_formatted_ddl(self) -> None:
        source = (
            'A = "CREATE TABLE IF NOT EXISTS aura_x (id TEXT)"\n'
            'B = """\n    ALTER TABLE aura_x ADD COLUMN IF NOT EXISTS y TEXT\n"""\n'
            'def f(name):\n    return f"CREATE INDEX ix_{name} ON aura_x (id)"\n'
            'C = "DROP TABLE aura_x"\n'
            'D = "CREATE UNIQUE INDEX IF NOT EXISTS ux ON aura_x (id)"\n'
        )
        assert len(self._hits(source)) == 5

    def test_it_ignores_docstrings_privilege_names_and_queries(self) -> None:
        source = (
            '"""Module docstring: this adapter never runs CREATE TABLE aura_x itself."""\n'
            'PRIVILEGES = {"CREATE", "CREATE VIEW", "ALTER ROUTINE"}\n'
            'Q = "SELECT id FROM aura_x WHERE created_at > $1"\n'
            'def g():\n    """CREATE TABLE aura_y (described, not run)."""\n'
        )
        assert self._hits(source) == []
