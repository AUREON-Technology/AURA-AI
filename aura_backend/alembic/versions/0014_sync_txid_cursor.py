"""database-assigned sync cursor (audit F-12)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-28

Adds `sync_txid BIGINT NOT NULL` to every syncable table, plus a `(user_id, sync_txid)` index.

Why: `GET /api/v3/sync` returned rows `WHERE updated_at > since` and handed back a cursor read
from the *application* clock after the reads. `updated_at` is Postgres `now()`, which is the
*transaction start* time. A push that began before a concurrent pull and committed after it wrote
rows stamped earlier than that pull's cursor; no later pull ever returned them, so the device
diverged silently and permanently. App-to-database clock skew (separate hosts on Railway) did the
same thing without any concurrency at all.

`sync_txid` is the id of the transaction that last wrote the row, assigned by the database
(`pg_current_xact_id()`): as the column default on INSERT, and through the model's `onupdate` on
every UPDATE. A pull now records its snapshot's `xmin` -- every transaction below it has finished
-- as the next cursor, and returns rows `WHERE sync_txid >= cursor`. A row whose transaction was
still open during a pull has a txid at or above that pull's xmin, so the next pull returns it.
Re-delivery of an already-seen row is the only cost, and the client applies rows as idempotent
upserts. See `api/v2/sync.py` for the full argument.

`pg_current_xact_id()` is STABLE, so Postgres 11+ stores the default once as the column's
"missing" value instead of rewriting the table: every existing row reads as this migration's own
transaction id, which is below any cursor a pull hands out from now on. Needs Postgres 13+
(`xid8`); the deploy targets run 16.

Existing clients hold a timestamp cursor. The pull treats a cursor it cannot read as a cursor
it does not have and returns a full sync once -- which also re-delivers any row the old cursor
skipped.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Every table in `infrastructure.db.models.SYNCABLE_MODELS` (0001-0004).
_SYNCABLE_TABLES = (
    "tasks",
    "calendar_events",
    "finance_records",
    "health_records",
    "memories",
    "messages",
    "projects",
    "goals",
)

#: Kept textually equal to `infrastructure.db.models.SYNC_TXID_SQL` (pinned by a unit test).
SYNC_TXID_SQL = "(pg_current_xact_id()::text)::bigint"


def upgrade() -> None:
    for table in _SYNCABLE_TABLES:
        op.add_column(
            table,
            sa.Column(
                "sync_txid",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text(SYNC_TXID_SQL),
            ),
        )
        op.create_index(f"ix_{table}_user_sync_txid", table, ["user_id", "sync_txid"])


def downgrade() -> None:
    for table in _SYNCABLE_TABLES:
        op.drop_index(f"ix_{table}_user_sync_txid", table_name=table)
        op.drop_column(table, "sync_txid")
