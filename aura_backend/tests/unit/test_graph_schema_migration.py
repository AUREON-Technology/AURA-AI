"""Migration 0007 and ``PostgresGraphStore`` must build the same knowledge-graph schema.

Both create it -- the migration for an operator running ``alembic upgrade head``, the adapter on
first use for a gateway that got there first -- so a column or index added to one and not the
other would leave two production databases with different schemas depending on which ran first.
And the seeded vocabulary must be exactly what the ingestion pipeline validates against.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from aura_core.adapters.postgres_graph import GRAPH_SCHEMA, graph_type_seed_statements
from aura_core.domain.graph import DEFAULT_GRAPH_SCHEMA

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0007_knowledge_graph.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0007", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_the_migration_chains_after_idempotency_keys() -> None:
    migration = _migration()
    assert migration.revision == "0007"
    assert migration.down_revision == "0006"


def test_the_migration_and_the_adapter_issue_the_same_ddl() -> None:
    migration = _migration()
    assert [_norm(s) for s in migration.STATEMENTS] == [_norm(s) for s in GRAPH_SCHEMA]


def test_the_seeded_vocabulary_is_the_schema_the_pipeline_validates_against() -> None:
    migration = _migration()
    assert [(n, lit) for n, lit, _d in migration.ENTITY_TYPES] == [
        (t.name, t.literal) for t in DEFAULT_GRAPH_SCHEMA.entity_types
    ]
    assert [(p, set(s), set(o)) for p, s, o, _d in migration.RELATION_TYPES] == [
        (r.predicate, set(r.subject_types), set(r.object_types))
        for r in DEFAULT_GRAPH_SCHEMA.relation_types
    ]
    assert migration.seed_statements() == graph_type_seed_statements()


def test_every_graph_table_is_owner_scoped() -> None:
    """The vocabulary tables are global by design; every table holding facts leads with owner."""
    fact_tables = [
        s for s in GRAPH_SCHEMA
        if "CREATE TABLE" in s and "_types" not in s
    ]
    assert len(fact_tables) == 5
    for statement in fact_tables:
        assert "owner_id" in statement
        primary_key = statement[statement.index("PRIMARY KEY"):]
        assert primary_key.startswith("PRIMARY KEY (owner_id"), statement
