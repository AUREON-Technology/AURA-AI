"""projects and goals

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-24

Adds `projects` and `goals` to v2 cursor sync (Phase D1 of the upgrade roadmap). Column names
match the pattern every other syncable table already uses: `*_enc` ciphertext for freeform text,
plaintext for structured fields the server (and Phase D2's context engine, Phase D3's
prioritisation tool) can read without ever decrypting a title or description. `goals.project_id`
is a nullable FK to `projects` -- a goal need not belong to a project, but when it does, deleting
the project must not cascade-delete the goal (SET NULL, not CASCADE): a goal survives its
project's archival.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sync_columns():
    return [
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("client_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title_enc", sa.Text(), nullable=False),
        sa.Column("description_enc", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("target_date", sa.DateTime(timezone=True), nullable=True),
        *_sync_columns(),
    )
    op.create_index("ix_projects_user_updated", "projects", ["user_id", "updated_at"])

    op.create_table(
        "goals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title_enc", sa.Text(), nullable=False),
        sa.Column("description_enc", sa.Text(), nullable=True),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("target_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        *_sync_columns(),
    )
    op.create_index("ix_goals_user_updated", "goals", ["user_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_goals_user_updated", table_name="goals")
    op.drop_table("goals")
    op.drop_index("ix_projects_user_updated", table_name="projects")
    op.drop_table("projects")
