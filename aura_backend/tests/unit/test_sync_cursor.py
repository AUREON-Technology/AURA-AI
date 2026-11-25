"""The `/api/v3/sync` pull cursor and LWW clock bound, without a database (audit F-12).

The race itself needs two real transactions and lives in ``tests/load/test_sync_postgres.py``.
What is pinned here is the part a refactor could quietly undo: that the cursor is a database
transaction id rather than a time, that a cursor this server cannot honour means a full sync
rather than a guess, and that every syncable table actually carries the column the cursor reads.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.v2.sync import (
    _LEGACY_PULL_TABLES,
    _PULL_TABLES,
    CLIENT_CLOCK_SKEW_ALLOWANCE,
    PageToken,
    bounded_client_clock,
    decode_cursor,
    decode_page,
    encode_cursor,
    encode_page,
    is_stale,
)
from infrastructure.db.models import SYNC_TXID_SQL, SYNCABLE_MODELS, Task

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATION = _MIGRATIONS_DIR / "0014_sync_txid_cursor.py"


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
        """0014 added the txid column to the eight tables that existed. A table added to the sync
        since creates its own txid column and index in the migration that creates it, with the
        same default: ``_LATER`` names each, and the two sets together must be every syncable
        table, so a new one cannot join the sync without a txid."""
        migration = _migration()
        assert migration.SYNC_TXID_SQL == SYNC_TXID_SQL
        assert migration.down_revision == "0013"
        created_later = {"conversations": _MIGRATIONS_DIR / "0035_conversations.py"}
        assert set(migration._SYNCABLE_TABLES) | set(created_later) == set(SYNCABLE_MODELS)
        assert not set(migration._SYNCABLE_TABLES) & set(created_later)
        for table, path in created_later.items():
            spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
            later = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(later)
            assert later.SYNC_TXID_SQL == SYNC_TXID_SQL, table
            source = path.read_text(encoding="utf-8")
            assert '"sync_txid"' in source and f'"ix_{table}_user_sync_txid"' in source, table


class TestPageToken:
    """The paged pull's continuation token (INT-013). The paging itself, against real rows, is in
    ``tests/load/test_sync_postgres.py``."""

    _ID = uuid.UUID("11111111-2222-3333-4444-555555555555")

    def test_round_trip(self) -> None:
        token = PageToken(since=40, cursor=812, table=3, after_txid=700, after_id=self._ID)
        assert decode_page(encode_page(token), xmax=900) == token

    def test_a_full_sync_at_a_table_boundary_round_trips(self) -> None:
        token = PageToken(since=None, cursor=812, table=1)
        assert decode_page(encode_page(token), xmax=900) == token

    def test_no_token_or_garbage_starts_over(self) -> None:
        for page in (None, "", "txid:5", "page1.", "page1.a.b.c.d.e", "page1.-.812.0.700.-",
                     "page1.-.812.0.-." + str(self._ID), f"page1.-.812.{len(_PULL_TABLES)}.-.-",
                     "page1.-.812.-1.-.-", "page1.900.812.0.-.-"):
            assert decode_page(page, xmax=900) is None, page

    def test_a_token_ahead_of_the_database_starts_over(self) -> None:
        """Same reasoning as a cursor from another cluster."""
        assert decode_page(encode_page(PageToken(since=None, cursor=901, table=0)), xmax=900) is None

    def test_tables_are_walked_in_a_fixed_order_covering_every_one(self) -> None:
        """The order is part of the protocol (a token's ``table`` indexes it, and another gateway
        instance may read the token mid-deploy): the original eight keep their sorted positions
        and every table added since is appended, sorted. See ``tests/unit/test_chats_api.py``."""
        assert sorted(_PULL_TABLES) == sorted(SYNCABLE_MODELS) and len(set(_PULL_TABLES)) == len(_PULL_TABLES)
        original = [t for t in _PULL_TABLES if t in _LEGACY_PULL_TABLES]
        assert list(_PULL_TABLES[: len(original)]) == original == sorted(original)
        appended = list(_PULL_TABLES[len(original):])
        assert appended == sorted(appended) and not set(appended) & _LEGACY_PULL_TABLES


class TestSameBatchTie:
    """INT-001: ties lose, except to a row this same batch wrote with that clock."""

    def _row(self, clock: datetime) -> Task:
        return Task(id=uuid.uuid4(), user_id=uuid.uuid4(), title_enc="x", updated_at=NOW, client_updated_at=clock)

    def test_a_tie_with_another_writer_is_stale(self) -> None:
        assert is_stale(self._row(NOW), NOW)

    def test_a_tie_with_this_batchs_own_write_is_not(self) -> None:
        assert not is_stale(self._row(NOW), NOW, written_in_batch=NOW)

    def test_an_older_clock_is_stale_even_within_the_batch(self) -> None:
        assert is_stale(self._row(NOW), NOW - timedelta(seconds=1), written_in_batch=NOW)

    def test_a_newer_clock_applies(self) -> None:
        assert not is_stale(self._row(NOW), NOW + timedelta(seconds=1))
