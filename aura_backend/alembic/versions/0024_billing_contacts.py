"""billing contacts: where a workspace's billing email goes (SaaS phase 1, plan F1)

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-18

``aura_billing_contacts``: the Owner's verified address, stored at checkout and when a store
binding is issued, so receipts, renewal reminders and past-due notices have somewhere to go.
Cascades with the workspace. Read and written by ``aura_core/adapters/postgres_billing.py``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_billing_contacts (
        workspace_id TEXT        PRIMARY KEY REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        email        TEXT        NOT NULL,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_billing_contacts")
