"""adaptation connectors and credential vault (Track AP, AP2, ADR-021)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-01

Adds `aura_connectors` (registered connectors: the non-secret descriptor plus lifecycle state) and
`aura_connector_credentials` (vaulted connector credentials -- ciphertext only, enforced by a
CHECK on the `aura-enc:v1:` envelope). Both are owned by this migration alone: the adapter
(`aura_core/adapters/postgres_adaptation.py`) issues no DDL, following master-roadmap principle 9
(one schema authority).

`project_id` defaults to `p0`: one project per install (ADR-019), carried as a key so a later
multi-project change needs no table rewrite. No API accepts it.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_connectors (
        id              TEXT        PRIMARY KEY,
        project_id      TEXT        NOT NULL DEFAULT 'p0',
        kind            TEXT        NOT NULL,
        identity_mode   TEXT        NOT NULL,
        display_name    TEXT        NOT NULL,
        endpoint        TEXT        NOT NULL,
        options         JSONB       NOT NULL DEFAULT '{}'::jsonb,
        secret_ref      TEXT        NULL,
        max_concurrency INTEGER     NOT NULL,
        timeout_s       DOUBLE PRECISION NOT NULL,
        row_cap         INTEGER     NOT NULL,
        state           TEXT        NOT NULL,
        approval_id     TEXT        NULL,
        registered_by   TEXT        NOT NULL,
        registered_at   TIMESTAMPTZ NOT NULL,
        activated_by    TEXT        NULL,
        activated_at    TIMESTAMPTZ NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_connector_credentials (
        secret_ref  TEXT        PRIMARY KEY,
        project_id  TEXT        NOT NULL DEFAULT 'p0',
        ciphertext  TEXT        NOT NULL CHECK (ciphertext LIKE 'aura-enc:v1:%'),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_connector_credentials")
    op.execute("DROP TABLE IF EXISTS aura_connectors")
