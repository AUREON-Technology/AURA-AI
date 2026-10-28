"""The `/api/v3/sync` pull cursor and LWW clock bound, without a database (audit F-12).

The race itself needs two real transactions and lives in ``tests/load/test_sync_postgres.py``.
What is pinned here is the part a refactor could quietly undo: that the cursor is a database
transaction id rather than a time, that a cursor this server cannot honour means a full sync
rather than a guess, and that every syncable table actually carries the column the cursor reads.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.v2.sync import (
    CLIENT_CLOCK_SKEW_ALLOWANCE,
    bounded_client_clock,
    decode_cursor,
    encode_cursor,
)
from infrastructure.db.models import SYNC_TXID_SQL, SYNCABLE_MODELS

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0014_sync_txid_cursor.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0014", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCursorCodec:
    def test_round_trip(self) -> None:
        assert decode_cursor(encode_cursor(812), xmax=900) == 812

    def test_no_cursor_is_a_full_sync(self) -> None:
        assert decode_cursor(None, xmax=900) is None
        assert decode_cursor("", xmax=900) is None

    def test_a_timestamp_cursor_from_an_older_client_is_a_full_sync(self) -> None:
        """Those cursors came from the application clock and may already have skipped rows; one
        full sync is what repairs that."""
        assert decode_cursor("2026-10-01T00:00:00+00:00", xmax=900) is None

    def test_garbage_is_a_full_sync_not_an_error(self) -> None:
        assert decode_cursor("txid:abc", xmax=900) is None
        assert decode_cursor("txid:-5", xmax=900) is None

    def test_a_cursor_ahead_of_the_database_is_a_full_sync(self) -> None:
        """Transaction ids only grow, so this cursor came from another cluster."""
        assert decode_cursor(encode_cursor(901), xmax=900) is None
        assert decode_cursor(encode_cursor(900), xmax=900) == 900


class TestClientClockBound:
    def test_an_honest_clock_is_untouched(self) -> None:
        earlier = NOW - timedelta(days=3)
        assert bounded_client_clock(earlier, now=NOW) == earlier

    def test_a_fast_clock_is_clamped(self) -> None:
        assert bounded_client_clock(NOW + timedelta(days=1), now=NOW) == NOW + CLIENT_CLOCK_SKEW_ALLOWANCE

    def test_a_naive_clock_is_read_as_utc(self) -> None:
        """Comparing a naive value with the aware column would raise mid-batch."""
        value = bounded_client_clock(datetime(2026, 1, 1), now=NOW)
        assert value == datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestSyncTxidColumn:
    def test_every_syncable_model_carries_a_database_assigned_txid(self) -> None:
        for table, model in SYNCABLE_MODELS.items():
            column = model.__table__.columns.get("sync_txid")
            assert column is not None, table
            assert not column.nullable, table
            assert column.server_default is not None, f"{table}: INSERT must take the txid from Postgres"
            assert column.onupdate is not None, f"{table}: UPDATE must move the row past the cursor"

    def test_every_syncable_model_indexes_the_cursor(self) -> None:
        for table, model in SYNCABLE_MODELS.items():
            indexed = {tuple(c.name for c in index.columns) for index in model.__table__.indexes}
            assert ("user_id", "sync_txid") in indexed, table

    def test_the_migration_covers_every_syncable_table_with_the_same_default(self) -> None:
        migration = _migration()
        assert set(migration._SYNCABLE_TABLES) == set(SYNCABLE_MODELS)
        assert migration.SYNC_TXID_SQL == SYNC_TXID_SQL
        assert migration.down_revision == "0013"
