"""service keys for non-human callers (AR-4, a narrow AP12a)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-05

Adds `aura_service_keys`: one row per issued service key. Only a SHA-256 digest of the token is
stored; the token itself is shown once at issue and never persisted. `revoked_at` is read on
every request the key makes, so a revocation takes effect on the next call. (The adapter's copy was removed when Alembic became the only schema authority, CB-20.)
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_service_keys (
        key_id           TEXT        PRIMARY KEY CHECK (key_id ~ '^[0-9a-f]{16}$'),
        name             TEXT        NOT NULL,
        digest           TEXT        NOT NULL CHECK (digest LIKE 'sha256:%'),
        routes           TEXT[]      NOT NULL,
        rate_per_minute  INTEGER     NOT NULL CHECK (rate_per_minute BETWEEN 1 AND 600),
        created_at       TIMESTAMPTZ NOT NULL,
        created_by       TEXT        NOT NULL,
        revoked_at       TIMESTAMPTZ NULL,
        revoked_by       TEXT        NULL
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_service_keys")
