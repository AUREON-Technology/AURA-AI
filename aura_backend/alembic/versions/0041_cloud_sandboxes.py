"""cloud sandboxes: which sandbox serves which workspace, and who provides each workspace

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-26

``aura_cloud_sandboxes`` is the record behind the Railway sandbox runner
(``adapters/postgres_cloud_sandboxes.py``; no DDL there). It is separate from
``aura_agent_workspaces`` on purpose: a row names a running sandbox and a saved checkpoint at the
vendor, so it must outlive a workspace row until both are confirmed deleted, and a single task
gets one too.

``aura_agent_workspaces.provider`` says which runner holds a workspace's files. Existing rows are
the Docker sandbox's, which is the default.

Another open branch may claim this number too: whoever merges second renumbers and re-points
``down_revision`` at the new head.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_cloud_sandboxes (
        key              TEXT        PRIMARY KEY CHECK (length(key) > 0),
        kind             TEXT        NOT NULL CHECK (kind IN ('task', 'workspace')),
        sandbox_id       TEXT,
        booted_at        TIMESTAMPTZ,
        checkpoint_name  TEXT,
        saving_name      TEXT,
        saving_until     TIMESTAMPTZ,
        in_use           INTEGER     NOT NULL DEFAULT 0 CHECK (in_use >= 0),
        in_use_until     TIMESTAMPTZ,
        used_bytes       BIGINT      CHECK (used_bytes >= 0),
        lost             BOOLEAN     NOT NULL DEFAULT FALSE,
        created_at       TIMESTAMPTZ NOT NULL,
        updated_at       TIMESTAMPTZ NOT NULL
    )
    """,
    """
    ALTER TABLE aura_agent_workspaces
        ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'docker'
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Dropping the table forgets which sandboxes and checkpoints exist at the vendor: they are
    # then found only by listing the sandbox project.
    op.execute("ALTER TABLE aura_agent_workspaces DROP COLUMN IF EXISTS provider")
    op.execute("DROP TABLE IF EXISTS aura_cloud_sandboxes")
