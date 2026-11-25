"""conversations: the title, pin and archive state a person sets on a chat

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-22

A chat has never been a row: it is the ``conversation_id`` its ``messages`` share. That left
nowhere to put a title, a pin or an archive flag, and nothing for ``/api/v3/chats`` to update.

This adds ``conversations`` as a *metadata* table, not the chat itself. The chat list is derived
from ``messages``, so every existing chat already appears without a backfill; a row here is
created only when the person sets something the messages cannot say. It joins the cursor sync
(``SYNCABLE_MODELS``) like every other personal table, so a title set on one device reaches the
others.

* ``conversation_key`` -- the shared ``messages.conversation_id`` (the client mints it, so it is
  a string, not a UUID). One row per user per key (``uq_conversations_user_key``); two devices
  that each create the row offline are merged onto it by the sync's natural-key rule.
* ``title_enc`` -- ciphertext, nullable: the server stores and returns it but cannot read it, and
  a tombstone clears it.
* ``pinned`` / ``archived`` -- plaintext, default false, so the list can be filtered and ordered
  server-side.

Nothing existing is changed or deleted. Downgrade drops the table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Kept textually equal to ``infrastructure.db.models.SYNC_TXID_SQL`` (pinned by a unit test).
SYNC_TXID_SQL = "(pg_current_xact_id()::text)::bigint"


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_key", sa.String(128), nullable=False),
        sa.Column("title_enc", sa.Text(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("client_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_txid", sa.BigInteger(), nullable=False, server_default=sa.text(SYNC_TXID_SQL)),
        sa.UniqueConstraint("user_id", "conversation_key", name="uq_conversations_user_key"),
    )
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", "updated_at"])
    op.create_index("ix_conversations_user_sync_txid", "conversations", ["user_id", "sync_txid"])


def downgrade() -> None:
    op.drop_index("ix_conversations_user_sync_txid", table_name="conversations")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
    op.drop_table("conversations")
