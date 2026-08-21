"""chat messages in v2 cursor sync

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-21

Adds the ``messages`` table so chat rows participate in generic v2 cursor
sync (AURA-014). Column names match the Drift ``Messages`` table; ciphertext
lives in ``*_enc`` like the other syncable tables. ``persona`` is the
server-side name for Drift's ``agentId``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content_enc", sa.Text(), nullable=False),
        sa.Column("model_used", sa.String(128), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("citations_enc", sa.Text(), nullable=True),
        sa.Column("tool_calls_enc", sa.Text(), nullable=True),
        sa.Column("persona", sa.String(64), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("client_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_messages_user_updated", "messages", ["user_id", "updated_at"])
    op.create_index(
        "ix_messages_user_conversation", "messages", ["user_id", "conversation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_messages_user_conversation", table_name="messages")
    op.drop_index("ix_messages_user_updated", table_name="messages")
    op.drop_table("messages")
