"""adaptation active blueprint pointer (Track AP, AP7, ADR-024)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-04

Adds `aura_adaptation_blueprint_active`: one row per project naming the live blueprint version.
A deploy or rollback changes this row and the two affected `aura_adaptation_blueprints` rows in
one transaction (`PostgresBlueprintStore.activate`), so a reader never sees two active versions or
a pointer to a version that is not active. Owned by this migration; the adapter issues no DDL and
its `ACTIVE_SCHEMA` is kept textually in step (`tests/unit/test_adaptation_runtime.py`).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_blueprint_active (
        project_id        TEXT        PRIMARY KEY DEFAULT 'p0',
        version           INTEGER     NOT NULL,
        digest            TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        activated_at      TIMESTAMPTZ NOT NULL,
        activated_by      TEXT        NOT NULL,
        previous_version  INTEGER     NULL,
        FOREIGN KEY (project_id, version) REFERENCES aura_adaptation_blueprints (project_id, version)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_adaptation_blueprint_active")
