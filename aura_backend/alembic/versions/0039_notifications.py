"""notifications: a person's in-app inbox, and which kinds they have switched off

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-23

Read and written by ``aura_core/adapters/postgres_notifications.py``, which issues no DDL of its own
(one schema authority, CB-20). See ``aura_core/domain/notifications.py``.

* ``aura_notifications`` holds a **kind and its parameters**, never prose: the client renders and
  translates them, so no user-visible text and no user content is stored. ``params`` is bounded by
  the application (2 KB) and holds identifiers, counts, plan names and dates.
* ``dedupe_key`` makes a repeated event one row: the unique index covers only rows that have one.
  The partial index on unread rows serves the unread badge.
* ``aura_notification_settings`` holds the kinds a person switched OFF. No row means everything on, so
  a kind added later reaches people until they turn it off.

Nothing existing is touched. Downgrade drops both tables.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    r"""
    CREATE TABLE IF NOT EXISTS aura_notifications (
        id          TEXT        PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 100),
        owner_id    TEXT        NOT NULL CHECK (length(owner_id) > 0),
        kind        TEXT        NOT NULL CHECK (length(kind) BETWEEN 1 AND 64),
        params      JSONB       NOT NULL DEFAULT '{}'::jsonb,
        created_at  TIMESTAMPTZ NOT NULL,
        read_at     TIMESTAMPTZ,
        dedupe_key  TEXT        CHECK (dedupe_key IS NULL OR length(dedupe_key) BETWEEN 1 AND 200)
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_aura_notifications_owner_dedupe ON aura_notifications (owner_id, dedupe_key) WHERE dedupe_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_aura_notifications_owner_created ON aura_notifications (owner_id, created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_aura_notifications_owner_unread ON aura_notifications (owner_id) WHERE read_at IS NULL",
    r"""
    CREATE TABLE IF NOT EXISTS aura_notification_settings (
        owner_id        TEXT        PRIMARY KEY CHECK (length(owner_id) > 0),
        disabled_kinds  JSONB       NOT NULL DEFAULT '[]'::jsonb,
        updated_at      TIMESTAMPTZ NOT NULL
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_notification_settings")
    op.execute("DROP TABLE IF EXISTS aura_notifications")
