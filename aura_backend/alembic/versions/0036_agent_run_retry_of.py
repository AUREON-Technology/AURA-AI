"""agent runs: the earlier run a retry retries

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-22

A run stores no instruction (``domain/agent_runs.py``: only a digest of the goal), so the server
cannot re-run one. A retry is the client sending the turn again; ``retry_of`` records which earlier
run it retries, so the history shows the chain and a person can see what a run was a second attempt
at. It is set when the run is created and never changed (the upsert in
``postgres_engineer_runs.py`` does not update it).

* NULL for every existing run and for any run that is not a retry.
* The partial index serves "the retries of this run" without costing the rows that have none.

Nothing existing is touched. Downgrade drops the index and the column.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    "ALTER TABLE aura_agent_runs ADD COLUMN IF NOT EXISTS retry_of TEXT NULL",
    """
    CREATE INDEX IF NOT EXISTS ix_aura_agent_runs_user_retry_of
        ON aura_agent_runs (user_id, retry_of) WHERE retry_of IS NOT NULL
    """,
)

DOWNGRADE_STATEMENTS = (
    "DROP INDEX IF EXISTS ix_aura_agent_runs_user_retry_of",
    "ALTER TABLE aura_agent_runs DROP COLUMN IF EXISTS retry_of",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
