"""adaptation blueprint versions (Track AP, AP6, ADR-024)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-01

Adds `aura_adaptation_blueprints`: one row per compiled blueprint version -- the blueprint, its
generated evaluation suites and what the compiler dropped, as one JSONB `record`, plus the
columns a lifecycle query needs (`state`, `digest`, `twin_version`). AP6 only appends `proposed`
versions; the approve / deploy / rollback transitions are AP7's.

Owned by this migration alone: `aura_core/adapters/postgres_adaptation.py` issues no DDL.
`project_id` defaults to `p0` (ADR-019).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_blueprints (
        project_id    TEXT        NOT NULL DEFAULT 'p0',
        version       INTEGER     NOT NULL CHECK (version >= 1),
        state         TEXT        NOT NULL,
        digest        TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        twin_version  INTEGER     NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL,
        created_by    TEXT        NOT NULL,
        record        JSONB       NOT NULL,
        PRIMARY KEY (project_id, version)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_adaptation_blueprints")
