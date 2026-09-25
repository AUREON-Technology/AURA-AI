"""The knowledge graph (Phase 11 / GAP-023) against a real Postgres.

Opt-in, like everything under ``tests/load``: the ``load`` marker keeps it out of the default run,
and it skips unless ``AURA_TEST_DATABASE_URL`` names a database. It needs *only* Postgres -- no
Qdrant, Redis or model -- so a throwaway local cluster is enough::

    initdb -D /tmp/pg/data -U aura --auth=trust
    pg_ctl -D /tmp/pg/data -o "-p 55435 -k /tmp/pg -c listen_addresses=localhost" -l /tmp/pg/log start
    createdb -h /tmp/pg -p 55435 -U aura aura_kg
    AURA_TEST_DATABASE_URL='postgresql://aura@localhost:55435/aura_kg' \\
        pytest tests/load/test_knowledge_graph_postgres.py -m load -q

Store and engine tests each run in a freshly created schema (selected through the DSN's
``search_path``) that is dropped afterwards. The Alembic tests create and drop their own
databases, because a migration chain is per database.

What the fake-connection unit tests (``tests/unit/test_postgres_graph.py``) cannot show and this
does: the DDL and backfill actually execute; foreign keys and ``ON DELETE CASCADE`` hold;
``DELETE ... RETURNING`` / ``IS DISTINCT FROM $3::integer`` bind correctly under asyncpg; JSONB
and TIMESTAMPTZ round-trip into the domain types; and ingest -> supersede -> delete through the
real ``KnowledgeEngine`` leaves the tables in the state the port promises, per owner.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from aura_core.adapters.memory_stores import InMemoryDocumentStore, InMemoryVectorStore
from aura_core.adapters.postgres_graph import PostgresGraphStore
from aura_core.adapters.postgres_store import PostgresPool
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.domain.knowledge import Entity, GraphDelta, Relation
from aura_core.knowledge import KnowledgeEngine, RuleEntityExtractor
from aura_core.knowledge.index_manager import IndexManager
from aura_core.llm.embeddings import HashEmbeddingProvider

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
_BACKEND = Path(__file__).resolve().parents[2]
ALICE = UserId("alice")
BOB = UserId("bob")
ORG_CHART = (
    "Sarah Chen works at Acme Corp. Sarah Chen reports to Omar Farouk. "
    "Acme Corp is located in Lisbon District."
)


def _with_search_path(url: str, schema: str) -> str:
    """asyncpg passes unrecognised DSN query parameters through as server settings."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema
    return urlunsplit(parts._replace(query=urlencode(query)))


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


@pytest.fixture
async def schema_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    schema = f"kg_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield _with_search_path(_URL, schema), schema
    finally:
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.fixture
async def store(schema_url):
    url, _schema = schema_url
    pool = PostgresPool(url, min_size=1, max_size=4)
    graph = PostgresGraphStore(url, pool=pool)
    try:
        yield graph
    finally:
        if pool._pool is not None:
            await pool._pool.close()


@pytest.fixture
async def raw(schema_url):
    import asyncpg

    url, _schema = schema_url
    conn = await asyncpg.connect(url)
    try:
        yield conn
    finally:
        await conn.close()


def _engine(graph) -> KnowledgeEngine:
    documents = InMemoryDocumentStore()
    vectors = InMemoryVectorStore()
    embedder = HashEmbeddingProvider(dimension=64)
    return KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=IndexManager(
            vectors=vectors, documents=documents, embedder=embedder, alias="kg_test"
        ),
        graph=graph, extractor=RuleEntityExtractor(),
    )


def _entity(key: str, name: str, type_: str, source: str, **extra) -> Entity:
    return Entity(key=key, name=name, type=type_, sources=(source,), **extra)


def _relation(s: str, p: str, o: str, source: str, confidence: float = 0.7) -> Relation:
    return Relation(subject_key=s, predicate=p, object_key=o, confidence=confidence,
                    sources=(source,))


# -- store ------------------------------------------------------------------------------


class TestPostgresGraphStore:
    async def test_the_schema_creates_and_seeds_the_vocabulary(self, store, raw) -> None:
        await store.find_entities(owner_id=ALICE)
        tables = {
            r["table_name"] for r in await raw.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        }
        assert {
            "aura_graph_entities", "aura_graph_relations", "aura_graph_entity_aliases",
            "aura_graph_entity_provenance", "aura_graph_relation_provenance",
            "aura_graph_entity_types", "aura_graph_relation_types",
        } <= tables
        predicates = {r["predicate"] for r in await raw.fetch(
            "SELECT predicate FROM aura_graph_relation_types")}
        assert {"works_at", "reports_to", "depends_on"} <= predicates
        # Running the schema again (a second gateway worker) is a no-op, not an error.
        second = PostgresGraphStore("", pool=store._pool)
        await second.find_entities(owner_id=ALICE)

    async def test_apply_round_trips_provenance_confidence_and_validity(self, store, raw) -> None:
        await store.apply(GraphDelta(
            entities=(
                _entity("person:sarah-chen", "Sarah Chen", "person", "org@v1", confidence=0.7,
                        aliases=("Dr. Sarah Chen",)),
                _entity("organisation:acme", "Acme Corp", "organisation", "org@v1",
                        confidence=0.7),
            ),
            relations=(_relation("person:sarah-chen", "works_at", "organisation:acme", "org@v1"),),
        ), owner_id=ALICE)

        entity = await store.get_entity("person:sarah-chen", owner_id=ALICE)
        assert isinstance(entity.confidence, float) and entity.confidence == pytest.approx(0.7)
        assert entity.valid_from is not None and entity.valid_from.tzinfo is not None
        assert entity.valid_to is None and entity.sources == ("org@v1",)
        # Found through the alias table by a differently-cased, differently-spaced surface form.
        assert [e.key for e in await store.find_entities(owner_id=ALICE, name="DR.  SARAH")] == [
            "person:sarah-chen"
        ]
        provenance = await store.provenance(
            owner_id=ALICE, relation_key=("person:sarah-chen", "works_at", "organisation:acme")
        )
        assert [(p.document_id, p.document_version, p.retired_at) for p in provenance] == [
            ("org", 1, None)
        ]
        rows = await raw.fetch("SELECT owner_id, document_id, document_version "
                               "FROM aura_graph_relation_provenance")
        assert [tuple(r) for r in rows] == [("alice", "org", 1)]

    async def test_corroboration_raises_confidence_and_withdrawal_lowers_it(self, store) -> None:
        for source in ("a@v1", "b@v1"):
            await store.apply(GraphDelta(relations=(
                _relation("x", "depends_on", "y", source, confidence=0.7),
            )), owner_id=ALICE)
        (relation,) = await store.list_relations(owner_id=ALICE)
        assert relation.confidence == pytest.approx(0.91)
        assert set(relation.sources) == {"a@v1", "b@v1"}

        await store.revert("a@v1", owner_id=ALICE)
        (relation,) = await store.list_relations(owner_id=ALICE)
        assert relation.confidence == pytest.approx(0.7)
        assert relation.sources == ("b@v1",)

    async def test_retire_expires_and_purge_removes(self, store, raw) -> None:
        await store.apply(GraphDelta(
            entities=(_entity("person:sarah-chen", "Sarah Chen", "person", "org@v1"),
                      _entity("organisation:acme", "Acme Corp", "organisation", "org@v1")),
            relations=(_relation("person:sarah-chen", "works_at", "organisation:acme", "org@v1"),),
        ), owner_id=ALICE)
        await store.apply(GraphDelta(
            entities=(_entity("person:sarah-chen", "Sarah Chen", "person", "org@v2"),),
        ), owner_id=ALICE)

        expired = await store.retire_document("org", owner_id=ALICE, keep_version=2)
        assert expired == 2  # the relation and Acme; Sarah is still asserted by v2
        assert await store.list_relations(owner_id=ALICE) == []
        history = await store.list_relations(owner_id=ALICE, include_expired=True)
        assert len(history) == 1 and history[0].valid_to is not None
        assert await store.find_entities(owner_id=ALICE, name="acme") == []
        assert (await store.get_entity("organisation:acme", owner_id=ALICE)).valid_to is not None
        assert (await store.get_entity("person:sarah-chen", owner_id=ALICE)).is_current
        _entities, edges = await store.neighbours("person:sarah-chen", owner_id=ALICE, depth=3)
        assert edges == []

        removed = await store.purge_document("org", owner_id=ALICE)
        assert removed == 3
        for table in ("aura_graph_entities", "aura_graph_relations", "aura_graph_entity_aliases",
                      "aura_graph_entity_provenance", "aura_graph_relation_provenance"):
            assert await raw.fetchval(f"SELECT count(*) FROM {table}") == 0, table

    async def test_owners_are_isolated(self, store) -> None:
        delta = GraphDelta(
            entities=(_entity("person:sarah-chen", "Sarah Chen", "person", "org@v1"),),
            relations=(_relation("person:sarah-chen", "works_at", "organisation:acme", "org@v1"),),
        )
        await store.apply(delta, owner_id=ALICE)
        await store.apply(delta, owner_id=BOB)

        assert await store.purge_document("org", owner_id=BOB) == 2
        assert await store.get_entity("person:sarah-chen", owner_id=BOB) is None
        assert (await store.get_entity("person:sarah-chen", owner_id=ALICE)) is not None
        assert len(await store.list_relations(owner_id=ALICE)) == 1
        assert await store.retire_document("org", owner_id=BOB, keep_version=None) == 0
        assert len(await store.list_relations(owner_id=ALICE)) == 1
        assert await store.find_entities(owner_id=UserId("mallory"), name="sarah") == []


# -- engine: ingest -> facts -> supersede -> delete ------------------------------------


class TestKnowledgeEngineOnPostgres:
    async def test_ingest_supersede_delete_and_isolation(self, store, raw) -> None:
        engine = _engine(store)
        await engine.ingest(title="Org", content=ORG_CHART, document_id=DocumentId("alice-org"),
                            owner_id=ALICE)
        await engine.ingest(title="Org", content=ORG_CHART + " ",
                            document_id=DocumentId("bob-org"), owner_id=BOB)

        # Facts present, with provenance naming the document version.
        alice = {(r.subject_key, r.predicate, r.object_key)
                 for r in await store.list_relations(owner_id=ALICE)}
        assert alice == {
            ("person:sarah-chen", "works_at", "organisation:acme"),
            ("person:sarah-chen", "reports_to", "person:omar-farouk"),
            ("organisation:acme", "located_in", "place:lisbon-district"),
        }
        assert await raw.fetchval(
            "SELECT count(*) FROM aura_graph_relation_provenance "
            "WHERE owner_id = 'alice' AND source_id = 'alice-org@v1'"
        ) == 3

        # Supersede: v2 drops the employer; that fact expires, the rest carry on under v2.
        await engine.ingest(
            title="Org", content="Sarah Chen reports to Omar Farouk.",
            document_id=DocumentId("alice-org"), owner_id=ALICE,
        )
        current = {r.predicate for r in await store.list_relations(owner_id=ALICE)}
        assert current == {"reports_to"}
        retired = await raw.fetchval(
            "SELECT count(*) FROM aura_graph_relation_provenance "
            "WHERE owner_id = 'alice' AND retired_at IS NOT NULL"
        )
        assert retired == 3  # v1's three assertions, all retired; v2 re-asserted one

        # Delete: every fact derived from the document is gone, history included.
        await engine.delete_document(DocumentId("alice-org"), owner_id=ALICE)
        for table in ("aura_graph_entities", "aura_graph_relations",
                      "aura_graph_entity_provenance", "aura_graph_relation_provenance",
                      "aura_graph_entity_aliases"):
            assert await raw.fetchval(
                f"SELECT count(*) FROM {table} WHERE owner_id = 'alice'"
            ) == 0, table

        # Bob's identical facts were never touched.
        assert len(await store.list_relations(owner_id=BOB)) == 3
        # And Bob deleting Alice's (now gone) id, or Alice deleting Bob's, does nothing.
        assert await engine.delete_document(DocumentId("bob-org"), owner_id=ALICE) == 0
        assert len(await store.list_relations(owner_id=BOB)) == 3


# -- legacy rows and Alembic ---------------------------------------------------------------

_LEGACY_DDL = (
    """CREATE TABLE aura_graph_entities (
        owner_id TEXT NOT NULL DEFAULT '', key TEXT NOT NULL, name TEXT NOT NULL,
        type TEXT NOT NULL, aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
        attributes JSONB NOT NULL DEFAULT '{}'::jsonb, mention_count INTEGER NOT NULL DEFAULT 1,
        first_seen TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        sources JSONB NOT NULL DEFAULT '[]'::jsonb, PRIMARY KEY (owner_id, key))""",
    """CREATE TABLE aura_graph_relations (
        owner_id TEXT NOT NULL DEFAULT '', subject_key TEXT NOT NULL, predicate TEXT NOT NULL,
        object_key TEXT NOT NULL, confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
        sources JSONB NOT NULL DEFAULT '[]'::jsonb, attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (owner_id, subject_key, predicate, object_key))""",
    """INSERT INTO aura_graph_entities (owner_id, key, name, type, aliases, sources)
       VALUES ('alice', 'person:sarah-chen', 'Sarah Chen', 'person', '["S. Chen"]',
               '["old-doc@v3"]')""",
    """INSERT INTO aura_graph_relations (owner_id, subject_key, predicate, object_key,
                                         confidence, sources)
       VALUES ('alice', 'person:sarah-chen', 'works_at', 'organisation:acme', 0.7,
               '["old-doc@v3"]')""",
)


class TestLegacyRows:
    async def test_pre_phase_11_rows_are_backfilled_and_then_purgeable(self, store, raw) -> None:
        """A production database already holds graph rows written by the old adapter."""
        for statement in _LEGACY_DDL:
            await raw.execute(statement)

        assert await store.find_entities(owner_id=ALICE, name="s. chen")
        (prov,) = await store.provenance(owner_id=ALICE, entity_key="person:sarah-chen")
        assert (prov.document_id, prov.document_version) == ("old-doc", 3)
        entity = await store.get_entity("person:sarah-chen", owner_id=ALICE)
        assert entity.valid_from is not None and entity.is_current

        assert await store.purge_document("old-doc", owner_id=ALICE) == 2
        assert await raw.fetchval("SELECT count(*) FROM aura_graph_entities") == 0


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": database_url, "AURA_DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=_BACKEND, env=env,
        capture_output=True, text=True, timeout=300,
    )


@pytest.fixture
async def fresh_database():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"kg_alembic_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = _with_database(_URL, name)
    try:
        yield url
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def _sqlalchemy_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


class TestAlembic:
    async def test_upgrade_head_downgrade_and_upgrade_again(self, fresh_database) -> None:
        import asyncpg

        result = _alembic(_sqlalchemy_url(fresh_database), "upgrade", "head")
        assert result.returncode == 0, result.stderr[-3000:]

        conn = await asyncpg.connect(fresh_database)
        try:
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0007"
            columns = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'aura_graph_relations'")}
            assert {"valid_from", "valid_to", "confidence"} <= columns
            assert await conn.fetchval("SELECT count(*) FROM aura_graph_entity_types") == 11
        finally:
            await conn.close()

        # The adapter then finds the schema already there and works on top of it.
        pool = PostgresPool(fresh_database, min_size=1, max_size=2)
        graph = PostgresGraphStore(fresh_database, pool=pool)
        try:
            await graph.apply(GraphDelta(relations=(
                _relation("a", "depends_on", "b", "doc@v1"),)), owner_id=ALICE)
            assert len(await graph.list_relations(owner_id=ALICE)) == 1
        finally:
            await pool._pool.close()

        down = _alembic(_sqlalchemy_url(fresh_database), "downgrade", "0006")
        assert down.returncode == 0, down.stderr[-3000:]
        up = _alembic(_sqlalchemy_url(fresh_database), "upgrade", "head")
        assert up.returncode == 0, up.stderr[-3000:]

        conn = await asyncpg.connect(fresh_database)
        try:
            # The pre-existing relation survived the round trip and was re-backfilled.
            assert await conn.fetchval(
                "SELECT count(*) FROM aura_graph_relation_provenance WHERE source_id = 'doc@v1'"
            ) == 1
        finally:
            await conn.close()

    async def test_upgrade_head_over_tables_the_gateway_already_created(
        self, fresh_database
    ) -> None:
        """The gateway ran first (adapter DDL), then the operator migrates: must not fail."""
        import asyncpg

        base = _alembic(_sqlalchemy_url(fresh_database), "upgrade", "0006")
        assert base.returncode == 0, base.stderr[-3000:]
        conn = await asyncpg.connect(fresh_database)
        try:
            for statement in _LEGACY_DDL:
                await conn.execute(statement)
        finally:
            await conn.close()

        result = _alembic(_sqlalchemy_url(fresh_database), "upgrade", "head")
        assert result.returncode == 0, result.stderr[-3000:]

        conn = await asyncpg.connect(fresh_database)
        try:
            rows = await conn.fetch(
                "SELECT source_id, document_id, document_version "
                "FROM aura_graph_entity_provenance"
            )
            assert [tuple(r) for r in rows] == [("old-doc@v3", "old-doc", 3)]
            aliases = {r["alias_norm"] for r in await conn.fetch(
                "SELECT alias_norm FROM aura_graph_entity_aliases")}
            assert aliases == {"sarah chen", "s. chen"}
            assert json.loads(await conn.fetchval(
                "SELECT sources FROM aura_graph_relations")) == ["old-doc@v3"]
        finally:
            await conn.close()
