"""payments outlive the workspace they paid for (audit F34)

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-18

``aura_payments.workspace_id`` referenced ``aura_workspaces`` with ``ON DELETE CASCADE`` (0025).
Erasing a user deletes their ``users`` row, which cascades to their personal workspace, which
cascaded to every payment made for it: the record of money taken went with the account. Deleting
a team workspace did the same. A payment row names no person -- an amount, a plan, a status and
the provider's reference -- so it is kept now, and only the link to the deleted workspace goes
(``ON DELETE SET NULL``, which needs the column nullable). A payment is still written against an
existing workspace; NULL only ever means "its workspace was deleted".

The downgrade refuses while any payment has lost its workspace: restoring ``NOT NULL`` would
need those ledger rows deleted, which is the loss this revision exists to prevent.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The name Postgres gave 0025's inline ``REFERENCES``; kept so a downgrade restores it exactly.
FK_NAME = "aura_payments_workspace_id_fkey"

UPGRADE_STATEMENTS = (
    f"ALTER TABLE aura_payments DROP CONSTRAINT IF EXISTS {FK_NAME}",
    "ALTER TABLE aura_payments ALTER COLUMN workspace_id DROP NOT NULL",
    f"""
    ALTER TABLE aura_payments ADD CONSTRAINT {FK_NAME}
        FOREIGN KEY (workspace_id) REFERENCES aura_workspaces (id) ON DELETE SET NULL
    """,
)

#: In SQL so it also holds in `alembic downgrade --sql` output.
REFUSE_WHILE_ORPHANED = """
    DO $$
    DECLARE
        orphaned bigint;
    BEGIN
        SELECT count(*) INTO orphaned FROM aura_payments WHERE workspace_id IS NULL;
        IF orphaned > 0 THEN
            RAISE EXCEPTION 'refusing to downgrade 0030: aura_payments holds % payment(s) whose workspace was deleted, and restoring NOT NULL would require deleting them from the ledger', orphaned
                USING HINT = 'Export and archive those rows first if the downgrade is really needed.';
        END IF;
    END
    $$
"""

DOWNGRADE_STATEMENTS = (
    REFUSE_WHILE_ORPHANED,
    f"ALTER TABLE aura_payments DROP CONSTRAINT IF EXISTS {FK_NAME}",
    "ALTER TABLE aura_payments ALTER COLUMN workspace_id SET NOT NULL",
    f"""
    ALTER TABLE aura_payments ADD CONSTRAINT {FK_NAME}
        FOREIGN KEY (workspace_id) REFERENCES aura_workspaces (id) ON DELETE CASCADE
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
