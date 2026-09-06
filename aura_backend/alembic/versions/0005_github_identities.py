"""github identities

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-06

Adds `github_identities`: one row per user who has connected a personal GitHub account through
the engineer agent's OAuth flow (`api/v2/github_integration.py`). Distinct from the
deployment-wide `AURA_GITHUB_TOKEN` env var (`aura_core/config/settings.py`'s `GitHubSettings`)
-- that is one shared PAT an operator configures once; this table lets each user authorize their
own GitHub account and exactly the repositories they grant during installation instead.

Not a cursor-synced table (no `SyncableMixin`): nothing here is client-visible, it is a
server-only OAuth credential the client never reads or writes directly. `access_token_enc`/
`refresh_token_enc` hold ciphertext from `aura_core/adapters/secrets_crypto.py`'s own symmetric
key -- unlike every other `*_enc` column in this schema, which is zero-knowledge ciphertext the
server cannot read, these two the server decrypts on every use. `user_id` is unique: a user has
at most one connected GitHub account at a time, so reconnecting overwrites the existing row.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(255), nullable=False),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column("scopes", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_github_identities_github_user_id", "github_identities", ["github_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_github_identities_github_user_id", table_name="github_identities")
    op.drop_table("github_identities")
