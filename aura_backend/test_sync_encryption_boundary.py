"""Guards the zero-knowledge property of the v2 sync API (Doc/V2_BLUEPRINT.md
§9): the backend must never inspect, transform, or attempt to decrypt the
`_enc` fields it stores — they're opaque AES-256-GCM ciphertext the client
produced, and only the client holds the key. This is a regression test,
not a feature test: if someone later adds server-side logic that touches
these fields' contents (e.g., "let's decrypt titles for search indexing"),
this is the test that should catch the architecture violation.

Constructs SQLAlchemy model objects directly (no DB session/connection
needed — `_row_to_dict` only introspects `__table__.columns` + `getattr`),
so this runs without Postgres, same as the ORM mapper validation already
used elsewhere in this test suite.
"""
import unittest
import uuid
from datetime import datetime, timezone

from api.v2.sync import _row_to_dict
from infrastructure.db.models import CalendarEvent, FinanceRecord, Task


class TestSyncEncryptionBoundary(unittest.TestCase):
    def test_task_enc_fields_pass_through_unmodified(self):
        # Deliberately shaped like the client's real ciphertext format
        # (gcm:<iv>:<ciphertext>) but with fake base64 payloads — if the
        # server ever tried to decrypt this, it would raise, not silently
        # alter it, so an unmodified round-trip is the correct assertion
        # either way.
        title_ct = "gcm:aGVsbG8=:d29ybGQ="
        desc_ct = "gcm:Zm9v:YmFy"
        task = Task(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            title_enc=title_ct,
            description_enc=desc_ct,
            priority="medium",
            category="personal",
            due_date=None,
            completed_at=None,
            google_task_id=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            deleted_at=None,
        )

        result = _row_to_dict(task)

        self.assertEqual(result["title_enc"], title_ct)
        self.assertEqual(result["description_enc"], desc_ct)

    def test_calendar_event_enc_fields_pass_through_unmodified(self):
        title_ct = "gcm:AAAA:BBBB"
        event = CalendarEvent(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            title_enc=title_ct,
            description_enc=None,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            all_day=False,
            google_event_id=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            deleted_at=None,
        )

        result = _row_to_dict(event)
        self.assertEqual(result["title_enc"], title_ct)
        self.assertIsNone(result["description_enc"])

    def test_finance_record_description_enc_passes_through_unmodified(self):
        desc_ct = "gcm:CCCC:DDDD"
        record = FinanceRecord(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            type="expense",
            amount=42.5,
            category="food",
            description_enc=desc_ct,
            occurred_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            deleted_at=None,
        )

        result = _row_to_dict(record)
        self.assertEqual(result["description_enc"], desc_ct)

    def test_row_to_dict_never_imports_encryption_service(self):
        # Architectural smoke check: the sync module has no way to decrypt
        # anything even if someone tried, because it never imports a
        # decryption capability. Server-side decryption doesn't exist in
        # this codebase — this test fails loudly if that ever changes
        # without an explicit, deliberate decision.
        import api.v2.sync as sync_module

        module_globals = vars(sync_module)
        suspicious = [name for name in module_globals if "decrypt" in name.lower() or "cipher" in name.lower()]
        self.assertEqual(suspicious, [], f"sync.py should never reference decryption: found {suspicious}")


if __name__ == "__main__":
    unittest.main()
