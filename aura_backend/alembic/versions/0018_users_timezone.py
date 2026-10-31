"""store each user's IANA timezone

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-31

"Today" in the insights and the daily briefing was the UTC day, and their hour checks the UTC
hour, while the client stores a day's health record at local midnight (audit F-17): a UTC+3 user
was told "0ml of water logged today" every evening. The services now compute day boundaries in
the user's own zone, which the client reports in ``X-Client-Timezone``. NULL means UTC -- the old
behaviour -- so existing rows need no backfill.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS timezone")
