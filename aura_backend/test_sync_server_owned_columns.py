"""Guards the server-owned-column boundary of the v2 sync push API
(api/v2/sync.py). The push handler copies arbitrary keys out of a
client-supplied `payload` onto an ORM row, so the set of columns it refuses
to copy is a security boundary, not a style choice.

`id`/`user_id` are the obvious ones (IDOR). The timestamp trio is the
subtle half and the reason this test exists:

  - `updated_at` is the last-write-wins comparand in push_sync. A client
    that writes a far-future value pins its own row permanently into the
    "server has a newer version" conflict branch — the row can never be
    updated again. A self-inflicted denial of service that no amount of
    client retrying recovers from.
  - `deleted_at` would let `op="upsert"` silently tombstone or resurrect a
    row, routing around the `op` field that is supposed to be the only way
    to express a delete.
  - `created_at` is audit provenance and should not be forgeable.

Pure-unit: builds the payload filter the same way the handler does, with no
DB session or Postgres needed, matching test_sync_encryption_boundary.py.
"""
import unittest
import uuid

from api.v2.sync import _SERVER_OWNED_COLUMNS, _coerce_value
from infrastructure.db.models import SYNCABLE_MODELS, Task


def _apply_payload(model, row, payload: dict):
    """Mirrors the assignment loop in api/v2/sync.py::push_sync."""
    for key, value in payload.items():
        if key in _SERVER_OWNED_COLUMNS:
            continue
        if hasattr(row, key):
            setattr(row, key, _coerce_value(model, key, value))
    return row


class TestSyncServerOwnedColumns(unittest.TestCase):
    def test_updated_at_cannot_be_set_from_payload(self):
        """The LWW comparand must not be client-writable — see module docstring."""
        row = Task(id=uuid.uuid4(), user_id=uuid.uuid4(), title_enc="gcm:a:b")
        original = row.updated_at

        _apply_payload(Task, row, {
            "title_enc": "gcm:new:value",
            "updated_at": "3000-01-01T00:00:00+00:00",
        })

        self.assertEqual(row.updated_at, original, "client payload must not set updated_at")
        self.assertEqual(row.title_enc, "gcm:new:value", "non-owned fields must still apply")

    def test_deleted_at_cannot_be_set_from_payload(self):
        """An upsert must not be able to tombstone a row behind the op field's back."""
        row = Task(id=uuid.uuid4(), user_id=uuid.uuid4(), title_enc="gcm:a:b")

        _apply_payload(Task, row, {"deleted_at": "2020-01-01T00:00:00+00:00"})

        self.assertIsNone(row.deleted_at, "client payload must not set deleted_at")

    def test_identity_columns_cannot_be_reassigned(self):
        """Classic IDOR guard: reassigning user_id would move a row to another account."""
        real_id = uuid.uuid4()
        real_user = uuid.uuid4()
        attacker_user = uuid.uuid4()
        row = Task(id=real_id, user_id=real_user, title_enc="gcm:a:b")

        _apply_payload(Task, row, {
            "id": str(uuid.uuid4()),
            "user_id": str(attacker_user),
        })

        self.assertEqual(row.id, real_id)
        self.assertEqual(row.user_id, real_user)

    def test_every_syncable_model_defines_all_server_owned_columns(self):
        """If a future table omits one of these, the filter silently protects
        nothing on that table — assert the contract holds across the registry."""
        for table_name, model in SYNCABLE_MODELS.items():
            columns = set(model.__table__.columns.keys())
            missing = _SERVER_OWNED_COLUMNS - columns
            self.assertEqual(
                missing, set(),
                f"{table_name} is missing server-owned column(s) {missing}",
            )

    def test_messages_is_a_syncable_model(self):
        """AURA-014: chat rows must ride the generic cursor loop."""
        self.assertIn("messages", SYNCABLE_MODELS)
        columns = set(SYNCABLE_MODELS["messages"].__table__.columns.keys())
        for required in (
            "conversation_id",
            "role",
            "content_enc",
            "model_used",
            "tokens_used",
            "citations_enc",
            "tool_calls_enc",
            "persona",
        ):
            self.assertIn(required, columns)


if __name__ == "__main__":
    unittest.main()
