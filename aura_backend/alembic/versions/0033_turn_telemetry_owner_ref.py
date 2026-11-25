"""keyed owner reference on per-turn telemetry

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-22

``aura_turn_telemetry`` holds a content-free record of every turn and, by design, nothing that
identifies who made it. That makes ``GET /api/v3/turns/{turn_id}/usage`` unsafe to build on it as
it stands: anyone who learned a ``turn_id`` could read anyone's usage.

``owner_ref`` is the narrowest fix: the HMAC of the user id under a key derived from the
deployment secret (``aura_core/security/owner_ref.py``). It answers "is this caller the owner?"
and nothing else; it cannot be reversed to a user id.

* NULL for every row written before this revision, and for any turn recorded while no secret was
  configured. Such a row is unreadable through the usage route (a lookup needs a match) and is
  removed by the telemetry retention purge like any other.
* The index serves account erasure (``DELETE ... WHERE owner_ref = $1``), which would otherwise
  scan the table. Partial, so the NULL rows cost nothing.

Nothing is deleted or rewritten. Downgrade drops the index and the column, which restores 0032's
table exactly.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    "ALTER TABLE aura_turn_telemetry ADD COLUMN IF NOT EXISTS owner_ref TEXT",
    """
    CREATE INDEX IF NOT EXISTS ix_aura_turn_telemetry_owner_ref
        ON aura_turn_telemetry (owner_ref) WHERE owner_ref IS NOT NULL
    """,
)

DOWNGRADE_STATEMENTS = (
    "DROP INDEX IF EXISTS ix_aura_turn_telemetry_owner_ref",
    "ALTER TABLE aura_turn_telemetry DROP COLUMN IF EXISTS owner_ref",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
