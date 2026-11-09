"""Migration 0007 builds the knowledge-graph schema and seeds its vocabulary.

It is the only thing that does: ``PostgresGraphStore`` used to run the same DDL on first use, and
this file pinned the two copies together. The adapter's copy is gone (CB-20; see
``tests/architecture/test_no_runtime_ddl.py``), so what stays pinned here is that the seeded
vocabulary is exactly what the ingestion pipeline validates against, and that every fact table is
owner-scoped.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from aura_core.domain.graph import DEFAULT_GRAPH_SCHEMA

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0007_knowledge_graph.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0007", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_migration_chains_after_idempotency_keys() -> None:
    migration = _migration()
    assert migration.revision == "0007"
    assert migration.down_revision == "0006"


def test_the_seeded_vocabulary_is_the_schema_the_pipeline_validates_against() -> None:
    # Nothing re-seeds at runtime any more, so a change to DEFAULT_GRAPH_SCHEMA needs a new
    # migration that upserts the new vocabulary (editing 0007 would never reach a database that
    # already ran it) -- and this test then reads that migration instead of 0007.
    migration = _migration()
    assert [(n, lit) for n, lit, _d in migration.ENTITY_TYPES] == [
        (t.name, t.literal) for t in DEFAULT_GRAPH_SCHEMA.entity_types
    ]
    assert [(p, set(s), set(o)) for p, s, o, _d in migration.RELATION_TYPES] == [
        (r.predicate, set(r.subject_types), set(r.object_types))
        for r in DEFAULT_GRAPH_SCHEMA.relation_types
    ]


def test_every_graph_table_is_owner_scoped() -> None:
    """The vocabulary tables are global by design; every table holding facts leads with owner."""
    fact_tables = [
        s for s in _migration().STATEMENTS
        if "CREATE TABLE" in s and "_types" not in s
    ]
    assert len(fact_tables) == 5
    for statement in fact_tables:
        assert "owner_id" in statement
        primary_key = statement[statement.index("PRIMARY KEY"):]
        assert primary_key.startswith("PRIMARY KEY (owner_id"), statement
