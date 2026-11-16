"""workspaces, memberships and invites (SaaS phase 1, ADR-034)

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16

Read and written by ``aura_core/adapters/postgres_tenancy.py``, which issues no DDL of its own.

* People are the auth subject (``users.auth_subject``), like every other ``aura_*`` table.
  Erasing a user cascades to their memberships and to their *personal* workspace (through
  ``personal_owner``), never to a team they created: ``created_by`` is history, not ownership, and
  a team they handed over must survive them.
* One personal workspace per person (unique ``personal_owner``; NULL for every team).
* Exactly one owner per workspace (partial unique index on ``workspace_id`` where
  ``role = 'owner'``); ownership transfer demotes before it promotes.
* Invites store only the SHA-256 of their token.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_workspaces (
        id          TEXT        PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
        kind        TEXT        NOT NULL CHECK (kind IN ('personal', 'team')),
        name        TEXT        NOT NULL,
        created_by  TEXT        NOT NULL,
        personal_owner TEXT     UNIQUE REFERENCES users (auth_subject) ON DELETE CASCADE,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK ((kind = 'personal') = (personal_owner IS NOT NULL)),
        CHECK (personal_owner IS NULL OR personal_owner = created_by)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_workspace_members (
        workspace_id TEXT        NOT NULL REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        user_id      TEXT        NOT NULL REFERENCES users (auth_subject) ON DELETE CASCADE,
        role         TEXT        NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
        invited_by   TEXT,
        joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (workspace_id, user_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_workspace_members_user
        ON aura_workspace_members (user_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_aura_workspace_members_owner
        ON aura_workspace_members (workspace_id) WHERE role = 'owner'
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_workspace_invites (
        invite_id    TEXT        PRIMARY KEY CHECK (invite_id ~ '^[0-9a-f]{16}$'),
        workspace_id TEXT        NOT NULL REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        email        TEXT        NOT NULL,
        role         TEXT        NOT NULL CHECK (role IN ('admin', 'member')),
        digest       TEXT        NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
        created_by   TEXT        NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at   TIMESTAMPTZ NOT NULL,
        accepted_at  TIMESTAMPTZ,
        accepted_by  TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_workspace_invites_workspace
        ON aura_workspace_invites (workspace_id)
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_workspace_invites")
    op.execute("DROP TABLE IF EXISTS aura_workspace_members")
    op.execute("DROP TABLE IF EXISTS aura_workspaces")
