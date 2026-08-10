"""Guards which clock the v2 sync push API uses to detect write conflicts.

`api/v2/sync.py::push_sync` applies a mutation only when it is newer than what the server holds.
The comparison has to be client-clock against client-clock, and for a long time it was not: the
incoming mutation's `client_updated_at` was compared against the row's `updated_at`, which is
server-set (`onupdate=func.now()`).

The consequence was silent data loss on exactly the workflow this API exists for. An offline
client edits a task on the 1st and syncs on the 10th; the server compares "client says the 1st"
against "I wrote this on the 5th" and answers `conflict`. The response is HTTP 200 with a
per-mutation status the client does not inspect, so the edit vanishes with nothing raised, nothing
logged as an error, and no retry that would ever succeed.

It went unnoticed because the only test that exercised the comparison needed Docker and Postgres,
and the suite never got far enough to run it. These tests are pure-unit for that reason -- the rule
is a free function precisely so it can be checked without infrastructure, in the style of
test_sync_server_owned_columns.py.
"""
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from api.v2.sync import _SERVER_OWNED_COLUMNS, conflict_comparand
from infrastructure.db.models import Task

JAN_1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
JAN_2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
AUG_10 = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _row(*, updated_at, client_updated_at=None) -> Task:
    """A Task carrying only the two timestamps the rule reads."""
    return Task(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title_enc="gcm:iv:ct",
        updated_at=updated_at,
        client_updated_at=client_updated_at,
    )


def _would_apply(row, incoming: datetime) -> bool:
    """Mirrors the conflict branch in push_sync."""
    return not (conflict_comparand(row) >= incoming)


class TestConflictComparand(unittest.TestCase):
    def test_the_client_clock_is_preferred_over_the_server_clock(self):
        row = _row(updated_at=AUG_10, client_updated_at=JAN_1)
        self.assertEqual(conflict_comparand(row), JAN_1)

    def test_a_late_sync_of_an_earlier_edit_is_not_a_conflict(self):
        """The regression. An edit made after the row's last *client* write must apply, however
        long ago that was in server time."""
        row = _row(updated_at=AUG_10, client_updated_at=JAN_1)
        self.assertTrue(_would_apply(row, JAN_2))

    def test_the_old_comparand_would_have_rejected_that_edit(self):
        """Pins the bug itself, so a revert to `updated_at` fails here rather than silently.

        Asserting the *old* rule's outcome directly: had the comparison used the server clock, a
        January edit arriving against an August write would have been refused.
        """
        row = _row(updated_at=AUG_10, client_updated_at=JAN_1)
        self.assertGreaterEqual(row.updated_at, JAN_2)  # the old rule's comparison
        self.assertTrue(_would_apply(row, JAN_2))       # the new rule's outcome

    def test_a_genuinely_stale_edit_is_still_a_conflict(self):
        """The fix must not turn conflict detection off."""
        row = _row(updated_at=AUG_10, client_updated_at=JAN_2)
        self.assertFalse(_would_apply(row, JAN_1))

    def test_an_equal_timestamp_is_a_conflict_not_a_reapply(self):
        """Ties lose. Re-applying an identical mutation would resurrect a row a concurrent delete
        had just tombstoned."""
        row = _row(updated_at=AUG_10, client_updated_at=JAN_1)
        self.assertFalse(_would_apply(row, JAN_1))

    def test_a_legacy_row_falls_back_to_the_server_clock(self):
        """NULL means the row predates the column. Falling back keeps the old behaviour for it;
        returning epoch instead would let any stale mutation overwrite every pre-existing row."""
        row = _row(updated_at=AUG_10, client_updated_at=None)
        self.assertEqual(conflict_comparand(row), AUG_10)
        self.assertFalse(_would_apply(row, JAN_2))

    def test_a_legacy_row_still_accepts_a_mutation_newer_than_the_server_write(self):
        row = _row(updated_at=JAN_1, client_updated_at=None)
        self.assertTrue(_would_apply(row, JAN_2))


class TestClientUpdatedAtIsNotPayloadWritable(unittest.TestCase):
    def test_the_new_column_cannot_be_set_from_a_payload(self):
        """It is a client value, but only through the validated mutation envelope.

        Reachable from the free-form payload it would be the same self-inflicted denial of service
        `updated_at` already had: write a far-future value and the row pins itself into the
        conflict branch permanently, unrecoverable by any client retry.
        """
        self.assertIn("client_updated_at", _SERVER_OWNED_COLUMNS)

    def test_every_timestamp_the_conflict_rule_reads_is_payload_protected(self):
        for column in ("updated_at", "client_updated_at"):
            self.assertIn(column, _SERVER_OWNED_COLUMNS)


class TestCursorClockIsUnchanged(unittest.TestCase):
    def test_updated_at_remains_server_set(self):
        """Cursor pagination (`WHERE updated_at > ?`) must keep using the server clock.

        This is why the fix added a column rather than redefining `updated_at`: a cursor built from
        client clocks would skip or repeat rows whenever two devices disagreed about the time.
        """
        column = Task.__table__.columns["updated_at"]
        self.assertIsNotNone(column.server_default)
        self.assertIsNotNone(column.onupdate)

    def test_the_conflict_column_is_not_server_set(self):
        column = Task.__table__.columns["client_updated_at"]
        self.assertIsNone(column.server_default)
        self.assertIsNone(column.onupdate)
        self.assertTrue(column.nullable)


if __name__ == "__main__":
    unittest.main()
