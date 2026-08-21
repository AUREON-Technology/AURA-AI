"""Postgres GraphStore: SQL shape and owner isolation (port contract).

Behavioural GraphRAG tests stay on ``InMemoryGraphStore``. These cases assert
that the postgres adapter puts ``owner_id`` on every statement and clamps
traversal depth, using a fake connection the same way the document adapter tests do.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.adapters.postgres_graph import PostgresGraphStore
from aura_core.domain.identifiers import UserId
from aura_core.domain.knowledge import Entity, GraphDelta, Relation


def _entity(key: str = "sarah_chen", *, name: str = "Sarah Chen", type: str = "person",
            sources: tuple[str, ...] = ("doc:1",), **extra) -> Entity:
    return Entity(key=key, name=name, type=type, sources=sources, **extra)


def _relation(subject: str, predicate: str, obj: str, *, confidence: float = 0.7,
              sources: tuple[str, ...] = ("doc:1",)) -> Relation:
    return Relation(subject_key=subject, predicate=predicate, object_key=obj,
                    confidence=confidence, sources=sources)


def _store(conn) -> PostgresGraphStore:
    store = PostgresGraphStore("postgresql://u:p@h/db")
    store._pool = _FakePgPool(conn)
    store._schema_ready = True
    return store


class TestPostgresGraphStore:
    async def test_apply_scopes_upserts_by_owner(self) -> None:
        conn = _FakePgConn()
        store = _store(conn)
        await store.apply(GraphDelta(entities=(_entity(),)), owner_id=UserId("alice"))
        inserts = [c for c in conn.calls if c[0] == "execute" and "INSERT INTO aura_graph_entities" in c[1]]
        assert inserts
        assert inserts[0][2][0] == "alice"
        lookups = [c for c in conn.calls if c[0] == "fetchrow" and "aura_graph_entities" in c[1]]
        assert lookups
        assert "owner_id = $1" in lookups[0][1]

    async def test_get_entity_predicates_on_owner(self) -> None:
        conn = _FakePgConn()
        store = _store(conn)
        assert await store.get_entity("sarah_chen", owner_id=UserId("bob")) is None
        assert conn.calls[-1][2] == ("bob", "sarah_chen")
        assert "owner_id = $1" in conn.calls[-1][1]

    async def test_find_entities_predicates_on_owner(self) -> None:
        conn = _FakePgConn()
        store = _store(conn)
        await store.find_entities(owner_id=UserId("alice"), name="chen")
        fetches = [c for c in conn.calls if c[0] == "fetch"]
        assert "owner_id = $1" in fetches[0][1]
        assert fetches[0][2][0] == "alice"

    async def test_neighbours_clamps_depth_and_scopes_owner(self) -> None:
        conn = _FakePgConn()
        store = _store(conn)
        await store.neighbours("n0", owner_id=UserId("alice"), depth=99)
        fetches = [c for c in conn.calls if c[0] == "fetch"]
        relation_fetches = [c for c in fetches if "aura_graph_relations" in c[1]]
        assert len(relation_fetches) <= 3
        assert all(c[2][0] == "alice" for c in relation_fetches)

    async def test_revert_is_owner_scoped(self) -> None:
        conn = _FakePgConn()
        store = _store(conn)
        await store.revert("doc:1", owner_id=UserId("bob"))
        deletes = [c for c in conn.calls if c[0] == "execute" and "DELETE FROM" in c[1]]
        assert deletes
        assert all(c[2][0] == "bob" for c in deletes)

    async def test_row_mapping_round_trips_jsonb_shapes(self) -> None:
        now = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        entity = PostgresGraphStore._entity({
            "owner_id": "u1",
            "key": "person:sarah",
            "name": "Sarah",
            "type": "person",
            "aliases": '["S. Chen"]',
            "attributes": '{"role": "director"}',
            "mention_count": 2,
            "first_seen": now,
            "last_seen": now,
            "sources": '["doc:1"]',
        })
        assert entity.aliases == ("S. Chen",)
        assert entity.attributes["role"] == "director"
        assert entity.owner_id.value == "u1"

        decoded = PostgresGraphStore._entity({
            "owner_id": "u1",
            "key": "person:sarah",
            "name": "Sarah",
            "type": "person",
            "aliases": ["S. Chen"],
            "attributes": {"role": "director"},
            "mention_count": 2,
            "first_seen": now,
            "last_seen": now,
            "sources": ["doc:1"],
        })
        assert decoded.aliases == entity.aliases


class _FakeTransaction:
    def __init__(self, conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        self.conn.calls.append(("BEGIN",))
        return self

    async def __aexit__(self, *exc):
        self.conn.calls.append(("COMMIT" if exc[0] is None else "ROLLBACK",))
        return False


class _FakePgConn:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.rows: list = []
        self.row = None

    def transaction(self):
        return _FakeTransaction(self)

    async def execute(self, sql, *params):
        self.calls.append(("execute", " ".join(sql.split()), params))
        return "DELETE 0"

    async def fetch(self, sql, *params):
        self.calls.append(("fetch", " ".join(sql.split()), params))
        return self.rows

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", " ".join(sql.split()), params))
        return self.row


class _FakePgPool:
    def __init__(self, conn: _FakePgConn) -> None:
        self._conn = conn
        self._pool = self

    async def _get_pool(self):
        return self

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()
