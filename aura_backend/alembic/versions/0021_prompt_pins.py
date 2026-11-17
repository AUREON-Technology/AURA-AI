"""prompt-version pins shared by every gateway worker (INT-004)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-17

Read and written by ``aura_core/adapters/postgres_prompt_pins.py``, which issues no DDL of its
own. Before this, a pin lived in one worker's memory: with four uvicorn workers it reached about
a quarter of traffic and was lost on restart. One row per pinned template; the version is checked
as semantic (``x.y.z``), the rule ``PromptTemplate`` enforces.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    r"""
    CREATE TABLE IF NOT EXISTS aura_prompt_pins (
        template_id TEXT        PRIMARY KEY CHECK (length(template_id) > 0),
        version     TEXT        NOT NULL CHECK (version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
        pinned_by   TEXT,
        pinned_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_prompt_pins")
