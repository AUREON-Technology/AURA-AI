"""Postgres AuditSink: SQL shape against a fake connection (AURA-009)."""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.adapters.postgres_audit import PostgresAuditSink
from aura_core.ports.runtime import AuditEvent


def _sink(conn) -> PostgresAuditSink:
    sink = PostgresAuditSink("postgresql://u:p@h/db")
    sink._pool = _FakePgPool(conn)
    sink._schema_ready = True
    return sink


class TestPostgresAuditSink:
    async def test_record_inserts_actor_and_action(self) -> None:
        conn = _FakePgConn()
        sink = _sink(conn)
        await sink.record(
            AuditEvent(action="tool.execute", actor="alice", outcome="allow", resource="remember")
        )
        inserts = [c for c in conn.calls if c[0] == "execute" and "INSERT INTO aura_audit_events" in c[1]]
        assert inserts
        params = inserts[0][2]
        assert params[2] == "alice"
        assert params[4] == "tool.execute"
        assert params[6] == "allow"

    async def test_query_filters_actor_and_action_prefix(self) -> None:
        conn = _FakePgConn()
        sink = _sink(conn)
        await sink.query(actor="alice", action="tool", since=datetime(2026, 1, 1, tzinfo=timezone.utc))
        fetches = [c for c in conn.calls if c[0] == "fetch"]
        assert fetches
        sql = fetches[0][1]
        assert "actor = $1" in sql
        assert "action LIKE $2" in sql
        assert fetches[0][2][0] == "alice"
        assert fetches[0][2][1] == "tool"

    async def test_row_mapping_round_trips_detail(self) -> None:
        now = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        event = PostgresAuditSink._event({
            "id": "abc",
            "at": now,
            "actor": "alice",
            "actor_role": "User",
            "action": "auth.login",
            "resource": "",
            "outcome": "allow",
            "correlation_id": "c1",
            "source_ip": "10.0.0.1",
            "detail": '{"reason": "ok"}',
        })
        assert event.actor == "alice"
        assert event.detail["reason"] == "ok"
        assert event.at == now


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
