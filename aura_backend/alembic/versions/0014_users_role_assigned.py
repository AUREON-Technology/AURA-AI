"""record which user roles an operator assigned on the Access page

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-28

`users.role` was written on every sign-in from whatever role the token resolved -- mostly the
SUPERADMIN_EMAILS / ADMIN_EMAILS / DEVELOPER_EMAILS lists -- and never read when resolving a
role, so assigning one on the Access page changed nothing. Role resolution now honours the
column, but only for rows an operator actually assigned: `role_assigned_at` marks those. Rows
written by the old sign-in path keep a NULL here, so a role someone once held through an env
list does not quietly become a permanent grant when it is removed from that list.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role_assigned_at TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role_assigned_by VARCHAR(320) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS role_assigned_by")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS role_assigned_at")
