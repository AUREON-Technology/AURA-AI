"""adaptation project twin versions (Track AP, AP4, ADR-020)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-01

Adds the append-only Project Twin: `aura_adaptation_twin_versions` (one row per snapshot: digest,
counts, source report digests, issues, labeling summary) and `aura_adaptation_twin_elements` (one
JSONB row per AUSM element of that snapshot). A version is never updated -- a bad one is
superseded by the next -- so there is no UPDATE path in the adapter at all.

Owned by this migration alone: the adapter (`aura_core/adapters/postgres_adaptation.py`) issues no
DDL. `project_id` defaults to `p0` (ADR-019).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_twin_versions (
        project_id      TEXT        NOT NULL DEFAULT 'p0',
        version         INTEGER     NOT NULL CHECK (version >= 1),
        digest          TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        parent_version  INTEGER     NULL,
        created_at      TIMESTAMPTZ NOT NULL,
        created_by      TEXT        NOT NULL,
        element_count   INTEGER     NOT NULL,
        record          JSONB       NOT NULL,
        PRIMARY KEY (project_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_adaptation_twin_elements (
        project_id  TEXT    NOT NULL DEFAULT 'p0',
        version     INTEGER NOT NULL,
        element_id  TEXT    NOT NULL,
        kind        TEXT    NOT NULL,
        body        JSONB   NOT NULL,
        PRIMARY KEY (project_id, version, element_id),
        FOREIGN KEY (project_id, version)
            REFERENCES aura_adaptation_twin_versions (project_id, version) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS aura_adaptation_twin_elements_kind
        ON aura_adaptation_twin_elements (project_id, version, kind)
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_adaptation_twin_elements")
    op.execute("DROP TABLE IF EXISTS aura_adaptation_twin_versions")
