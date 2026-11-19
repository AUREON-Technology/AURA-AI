"""chats shared with a team (SaaS phase 1, plan F1)

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-18

``aura_shared_conversations``: a copy of a member's chat, readable by every member of the team
workspace. One row per (workspace, sharer, source chat): sharing again replaces it. Cascades with
the workspace. Read and written by ``aura_core/adapters/postgres_sharing.py``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_shared_conversations (
        id           TEXT        PRIMARY KEY,
        workspace_id TEXT        NOT NULL REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        shared_by    TEXT        NOT NULL,
        source_id    TEXT        NOT NULL,
        title        TEXT        NOT NULL,
        messages     JSONB       NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT aura_shared_conversations_source UNIQUE (workspace_id, shared_by, source_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS aura_shared_conversations_by_workspace
        ON aura_shared_conversations (workspace_id, created_at DESC)
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_shared_conversations")
