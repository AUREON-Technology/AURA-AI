"""Every Alembic migration can be undone and re-applied on a real Postgres (roadmap M6, CB-20).

With the adapters' runtime DDL gone, migrations are the only thing that changes the schema, so a
release is reversible exactly when its migrations are. These walk the whole chain one revision at
a time on a fresh database: upgrade to ``rev``, downgrade to its parent, upgrade to ``rev`` again.

* Every revision must downgrade and re-apply cleanly, ending in the same schema it produced the
  first time.
* Every revision **after the baseline** (``0016``) must also leave the schema exactly as its
  parent had it. The baseline and ``0007`` deliberately keep tables that existed before them
  (dropping ``aura_documents`` to undo a baseline would delete every document), so they are held
  only to the first rule; :data:`KEEPS_ON_DOWNGRADE` names what each keeps, and the test fails if a
  downgrade keeps anything else.

The schema is read from the catalogs (columns, constraints, indexes) rather than with ``pg_dump``,
whose client version must not be older than the server's -- not a given on a CI runner.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a server where the role may
create databases. The ``postgres`` job in ``backend-ci.yml`` runs it against Postgres 16.
"""

from __future__ import annotations

import os

import pytest

from tests.postgres_migrations import BACKEND, alembic, migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
BASELINE = "0016"

#: Tables a downgrade leaves in place on purpose, by revision. Anything else left behind fails.
KEEPS_ON_DOWNGRADE: dict[str, frozenset[str]] = {
    # The two fact tables predate 0007: PostgresGraphStore created them before any migration did.
    "0007": frozenset({"aura_graph_entities", "aura_graph_relations"}),
    # The baseline only recorded tables that already existed; undoing it must not drop user data.
    BASELINE: frozenset({
        "aura_documents", "aura_chunks", "aura_memories", "aura_audit_events",
        "aura_event_outbox", "aura_approvals", "aura_agent_workspaces", "aura_engineer_runs",
        "aura_experiments", "aura_experiment_runs", "aura_feedback", "aura_finetune_jobs",
        "aura_knowledge_sources", "aura_model_versions", "aura_personal_os_records",
        "aura_turn_telemetry",
    }),
}


def _chain() -> list[tuple[str, str]]:
    """``(revision, parent)`` pairs from the first migration to head; ``base`` is the first's parent."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert len(script.get_heads()) == 1, "more than one Alembic head"
    chain = [(rev.revision, rev.down_revision or "base") for rev in script.walk_revisions("base", "heads")]
    return list(reversed(chain))


async def _schema(url: str) -> dict[str, frozenset]:
    """Every column, constraint and index in ``public``, keyed by the table it belongs to."""
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        columns = await conn.fetch(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns WHERE table_schema = 'public'
            """
        )
        constraints = await conn.fetch(
            """
            SELECT c.conrelid::regclass::text AS table_name, c.conname, pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = 'public' AND c.conrelid <> 0
            """
        )
        indexes = await conn.fetch(
            "SELECT tablename AS table_name, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
        )
    finally:
        await conn.close()
    tables: dict[str, set] = {}
    for kind, rows in (("column", columns), ("constraint", constraints), ("index", indexes)):
        for row in rows:
            name = str(row["table_name"])
            if name == "alembic_version":  # Alembic's own bookkeeping, not the schema
                continue
            tables.setdefault(name, set()).add((kind, *(str(v) for v in tuple(row)[1:])))
    return {name: frozenset(parts) for name, parts in tables.items()}


def _describe(before: dict, after: dict) -> str:
    added = sorted(set(after) - set(before))
    dropped = sorted(set(before) - set(after))
    changed = sorted(t for t in set(before) & set(after) if before[t] != after[t])
    return f"tables left behind: {added}; tables lost: {dropped}; tables changed: {changed}"


def _run(url: str, *args: str) -> None:
    result = alembic(url, *args)
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr[-3000:]}"


async def test_every_revision_downgrades_and_reapplies() -> None:
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    chain = _chain()
    assert chain[0][1] == "base" and BASELINE in [rev for rev, _ in chain]
    after_baseline = False
    checked_exactly: list[str] = []

    async with migrated_database(_URL, "roundtrip", revision="base") as url:
        for revision, parent in chain:
            before = await _schema(url)
            _run(url, "upgrade", revision)
            applied = await _schema(url)

            _run(url, "downgrade", parent)
            undone = await _schema(url)

            kept = KEEPS_ON_DOWNGRADE.get(revision, frozenset())
            assert set(undone) - set(before) <= kept, f"{revision} downgrade: {_describe(before, undone)}"
            restored = {t: parts for t, parts in undone.items() if t not in kept or t in before}
            assert restored == before, f"{revision} downgrade: {_describe(before, restored)}"
            if after_baseline:
                assert not kept, f"{revision} is after the baseline and must restore its parent exactly"
                assert undone == before, f"{revision} downgrade: {_describe(before, undone)}"
                checked_exactly.append(revision)

            _run(url, "upgrade", revision)
            assert await _schema(url) == applied, f"{revision} re-applied differently: {_describe(applied, await _schema(url))}"
            after_baseline = after_baseline or revision == BASELINE

    assert checked_exactly, "no revision after the baseline was checked -- the chain walk is broken"
