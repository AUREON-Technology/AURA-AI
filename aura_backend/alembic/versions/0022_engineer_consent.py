"""Engineer consent to sending code to Anthropic (module readiness WP1, D-03)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17

Read and written by ``aura_core/adapters/postgres_engineer_consent.py``, which issues no DDL of
its own. One row per person: the notice version they agreed to and when, and when they withdrew.
The kernel refuses an Engineer turn unless the row is current (``withdrawn_at IS NULL`` and the
version is the one the server presents). ``user_id`` is the ``UserId`` string form, like
``aura_engineer_runs``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    r"""
    CREATE TABLE IF NOT EXISTS aura_engineer_consents (
        user_id        TEXT        PRIMARY KEY CHECK (length(user_id) > 0),
        notice_version TEXT        NOT NULL CHECK (length(notice_version) > 0),
        accepted_at    TIMESTAMPTZ NOT NULL,
        withdrawn_at   TIMESTAMPTZ
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_engineer_consents")
