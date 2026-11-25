"""devices registered for push notifications

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-25

One row per (user, device). ``token`` is unique: a push token identifies one app install, so
registering it for another account moves it (``adapters/postgres_push.py``) rather than letting two
accounts be notified on one phone. See ADR-060.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_push_devices (
        user_id     TEXT        NOT NULL CHECK (length(user_id) > 0),
        device_id   TEXT        NOT NULL CHECK (length(device_id) > 0),
        token       TEXT        NOT NULL CHECK (length(token) > 0),
        platform    TEXT        NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (user_id, device_id),
        UNIQUE (token)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_push_devices")
