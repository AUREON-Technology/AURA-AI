"""tool approvals: a durable, content-free record of held tool calls and how each was decided

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-23

Read and written by ``aura_core/adapters/postgres_tool_approvals.py``, which issues no DDL of its own
(one schema authority, CB-20). The pending turn itself still lives in the shared cache for an hour
(``aura_core/execution/confirmations.py``); this is its record (``aura_core/domain/approvals.py``).

* ``id`` is the ``approval_id`` the client sends back to approve the call. It is a credential only
  its owner can use (the claim checks the owner and the conversation) and it stops working after an
  hour or one use, so keeping it in the row is what lets another device list and approve it.
* **No arguments and no message are stored**: ``digest`` is the same one-way digest the cache keeps
  for the exact call. An approval history is long-lived and widely read; arguments can be private.
* ``status`` holds what happened to the call. ``expired`` is not a value: it is a ``pending`` row
  past ``expires_at``, derived on read.
* The last CHECK keeps ``decided_at``/``decided_via`` set exactly when the row is no longer pending.

Nothing existing is touched. Downgrade drops the table.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    r"""
    CREATE TABLE IF NOT EXISTS aura_tool_approvals (
        id           TEXT        PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
        owner_id     TEXT        NOT NULL CHECK (length(owner_id) > 0),
        tool         TEXT        NOT NULL CHECK (length(tool) BETWEEN 1 AND 200),
        call_id      TEXT        NOT NULL CHECK (length(call_id) BETWEEN 1 AND 200),
        digest       TEXT        NOT NULL CHECK (length(digest) > 0),
        session_id   TEXT,
        turn_id      TEXT,
        status       TEXT        NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
        created_at   TIMESTAMPTZ NOT NULL,
        expires_at   TIMESTAMPTZ NOT NULL,
        decided_at   TIMESTAMPTZ,
        decided_via  TEXT,
        CHECK (expires_at > created_at),
        CHECK ((status = 'pending') = (decided_at IS NULL AND decided_via IS NULL))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_aura_tool_approvals_owner_created ON aura_tool_approvals (owner_id, created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_aura_tool_approvals_owner_tool ON aura_tool_approvals (owner_id, tool)",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_tool_approvals")
