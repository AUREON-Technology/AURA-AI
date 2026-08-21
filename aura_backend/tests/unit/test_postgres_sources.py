"""Postgres SourceRegistry: SQL shape against a fake connection (AURA-008)."""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.adapters.postgres_sources import PostgresSourceRegistry
from aura_core.domain.identifiers import UserId
from aura_core.domain.knowledge import KnowledgeSource, SourceType, TrustTier


def _registry(conn) -> PostgresSourceRegistry:
    registry = PostgresSourceRegistry("postgresql://u:p@h/db")
    registry._pool = _FakePgPool(conn)
    registry._schema_ready = True
    return registry


def _source(source_id: str = "policies") -> KnowledgeSource:
    return KnowledgeSource(
        id=source_id,
        type=SourceType.WEB,
        uri="https://example.com/policy",
        owner_id=UserId("alice"),
        trust_tier=TrustTier.VERIFIED,
    )


class TestPostgresSourceRegistry:
    async def test_list_enabled_predicates_on_enabled(self) -> None:
        conn = _FakePgConn()
        registry = _registry(conn)
        await registry.list_enabled()
        fetches = [c for c in conn.calls if c[0] == "fetch"]
        assert fetches
        assert "enabled = TRUE" in fetches[0][1]

    async def test_register_writes_owner_and_ceilings(self) -> None:
        conn = _FakePgConn()
        conn.row = None
        registry = _registry(conn)
        await registry.register(_source())
        inserts = [
            c for c in conn.calls if c[0] == "execute" and "INSERT INTO aura_knowledge_sources" in c[1]
        ]
        assert inserts
        params = inserts[0][2]
        assert params[0] == "policies"
        assert params[3] == "alice"
        assert params[7] == 1000

    async def test_record_success_updates_cursor(self) -> None:
        conn = _FakePgConn()
        registry = _registry(conn)
        at = datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)
        await registry.record_success("policies", cursor="etag-1", at=at)
        updates = [c for c in conn.calls if c[0] == "execute" and "UPDATE aura_knowledge_sources" in c[1]]
        assert updates
        assert updates[0][2][0] == "policies"
        assert updates[0][2][1] == "etag-1"


class _FakeTransaction:
    def __init__(self, conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
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
        return "UPDATE 1"

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
