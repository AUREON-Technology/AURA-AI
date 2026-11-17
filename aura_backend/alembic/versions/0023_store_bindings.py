"""in-app purchase bindings and the store event replay log (SaaS phase 1, plan D2)

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17

* ``aura_store_bindings``: a token AURA issues before an in-app purchase; the app hands it to the
  store (Apple ``appAccountToken`` / Google ``obfuscatedExternalAccountId``) and every store event
  names it. Cascades with the workspace.
* ``aura_store_events``: every store notification id seen (Apple ``notificationUUID``, Pub/Sub
  ``messageId``), so a replay is a no-op.

Read and written by ``aura_core/adapters/postgres_billing.py``; no DDL there.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_store_bindings (
        token        TEXT        PRIMARY KEY,
        workspace_id TEXT        NOT NULL REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        created_by   TEXT        NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_store_events (
        provider    TEXT        NOT NULL CHECK (provider IN ('apple', 'google')),
        event_id    TEXT        NOT NULL,
        received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (provider, event_id)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_store_events")
    op.execute("DROP TABLE IF EXISTS aura_store_bindings")
