"""store each user's preferred language

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17

The app sends the language it is showing (the user's in-app choice, or the device language) in
``X-Client-Language``. Requests that carry it answer in it directly; this column is for the work
that has no request to read it from -- the daily briefing and scheduled insights -- so it is
written wherever the timezone is (``services/user_language.py``;
docs/plans/arabic-language_plan.md, stage 1). NULL means "never reported", which every reader
treats as English, the behaviour before this column existed, so existing rows need no backfill.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(16) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS language")
