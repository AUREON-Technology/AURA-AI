"""separate the conflict comparand from the cursor clock

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-10

Adds `client_updated_at` to every syncable table.

Why: `api/v2/sync.py` detected write conflicts by comparing the incoming
mutation's `client_updated_at` against the row's `updated_at`. Those are two
different clocks. `updated_at` is server-set (`onupdate=func.now()`), so any
mutation whose client timestamp predated the last server write was rejected as
"server has a newer version" — which for an offline-first client is the normal
case rather than a conflict: edit on a plane, sync on landing. The client
received HTTP 200 with a per-mutation `conflict` status it did not inspect, so
the edit disappeared without anything surfacing as an error.

`updated_at` keeps its meaning and its indexes: cursor pagination
(`WHERE updated_at > ?`) must stay on the server clock, or a cursor would skip
or repeat rows whenever two devices' clocks disagreed. Conflict detection moves
to the new column.

Nullable with no backfill, deliberately. A backfilled value would be a guess
about when a client last edited a row, and the two plausible guesses are both
wrong in a way that loses data: NULL-as-epoch lets any stale mutation overwrite
every pre-existing row, and NULL-as-now rejects every pending offline edit. The
read path in sync.py falls back to `updated_at` for NULL rows, so legacy rows
keep exactly today's behaviour until their first write populates the column.

No index. The column is only ever read for a row already fetched by primary key.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The tables built with `_syncable_columns()` in 0001. `insights` declares its own `user_id`
#: rather than using the mixin and does not participate in push, so it is not listed.
_SYNCABLE_TABLES = (
    "tasks",
    "calendar_events",
    "finance_records",
    "health_records",
    "memories",
)


def upgrade() -> None:
    for table in _SYNCABLE_TABLES:
        op.add_column(
            table,
            sa.Column("client_updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table in _SYNCABLE_TABLES:
        op.drop_column(table, "client_updated_at")
