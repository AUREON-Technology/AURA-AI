"""workspace members carry the email they joined with (SaaS phase 1, plan F2)

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-18

So a team's members list shows people rather than opaque subjects: the creator's verified email
at creation, an invitee's invited address on acceptance. Nullable: memberships made before this
revision, and personal workspaces, have none.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = ("ALTER TABLE aura_workspace_members ADD COLUMN IF NOT EXISTS email TEXT",)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE aura_workspace_members DROP COLUMN IF EXISTS email")
