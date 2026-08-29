"""Feedback store: persist, owner isolation, and curator consumption (AURA-013)."""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.domain.identifiers import UserId
from aura_core.learning import DatasetCurator, FeedbackRecord, InMemoryFeedbackStore


def _record(*, owner: str, ident: str, prompt: str = "What is the leave policy exactly?") -> FeedbackRecord:
    return FeedbackRecord(
        id=ident,
        prompt=prompt,
        response="I am not sure about that.",
        rating=1,
        correction="Annual leave is 21 days per year, plus public holidays.",
        user_ref=owner,
        created_at=datetime.now(timezone.utc),
        metadata={"turn_id": f"t-{ident}", "comment": ""},
    )


class TestInMemoryFeedbackStore:
    async def test_put_then_list_returns_the_row(self) -> None:
        store = InMemoryFeedbackStore()
        alice = UserId("alice")
        await store.put(_record(owner="alice", ident="1"), owner_id=alice)
        rows = await store.list_for_owner(alice)
        assert len(rows) == 1
        assert rows[0].id == "1"
        assert rows[0].metadata["turn_id"] == "t-1"

    async def test_list_is_owner_scoped(self) -> None:
        store = InMemoryFeedbackStore()
        await store.put(_record(owner="alice", ident="a"), owner_id=UserId("alice"))
        await store.put(_record(owner="bob", ident="b"), owner_id=UserId("bob"))

        alice_rows = await store.list_for_owner(UserId("alice"))
        bob_rows = await store.list_for_owner(UserId("bob"))
        assert [r.id for r in alice_rows] == ["a"]
        assert [r.id for r in bob_rows] == ["b"]

    async def test_the_curator_can_curate_loaded_records(self) -> None:
        store = InMemoryFeedbackStore()
        await store.put(_record(owner="alice", ident="1"), owner_id=UserId("alice"))
        rows = await store.list_for_owner(UserId("alice"))
        examples, _stats = DatasetCurator().curate(rows)
        assert len(examples) == 1
        assert examples[0].was_corrected

    async def test_list_for_curation_crosses_owners(self) -> None:
        store = InMemoryFeedbackStore()
        await store.put(_record(owner="alice", ident="a"), owner_id=UserId("alice"))
        await store.put(_record(owner="bob", ident="b"), owner_id=UserId("bob"))
        rows = await store.list_for_curation(limit=10)
        assert {r.id for r in rows} == {"a", "b"}


class TestPostgresFeedbackSql:
    """SQL shape against a fake connection, matching the document-store tests."""

    async def test_list_predicates_on_owner_id(self) -> None:
        from aura_core.adapters.postgres_feedback import PostgresFeedbackStore

        conn = _FakePgConn()
        conn.rows = []
        store = PostgresFeedbackStore("postgresql://u:p@h/db")
        store._pool = _FakePgPool(conn)
        store._schema_ready = True

        await store.list_for_owner(UserId("alice"))
        fetches = [c for c in conn.calls if c[0] == "fetch"]
        assert fetches
        assert "owner_id = $1" in fetches[0][1]
        assert fetches[0][2][0] == "alice"

    async def test_put_writes_owner_id(self) -> None:
        from aura_core.adapters.postgres_feedback import PostgresFeedbackStore

        conn = _FakePgConn()
        store = PostgresFeedbackStore("postgresql://u:p@h/db")
        store._pool = _FakePgPool(conn)
        store._schema_ready = True
        await store.put(_record(owner="alice", ident="1"), owner_id=UserId("alice"))
        inserts = [c for c in conn.calls if c[0] == "execute" and "INSERT INTO aura_feedback" in c[1]]
        assert inserts
        assert "alice" in inserts[0][2]


class TestPostgresFeedbackCurationSql:
    async def test_list_for_curation_has_no_owner_predicate(self) -> None:
        from aura_core.adapters.postgres_feedback import PostgresFeedbackStore

        conn = _FakePgConn()
        conn.rows = []
        store = PostgresFeedbackStore("postgresql://u:p@h/db")
        store._pool = _FakePgPool(conn)
        store._schema_ready = True

        await store.list_for_curation(limit=100)
        fetches = [c for c in conn.calls if c[0] == "fetch"]
        assert fetches
        assert "owner_id =" not in fetches[0][1]
        assert "ORDER BY created_at DESC" in fetches[0][1]


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
        return "INSERT 0 1"

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
