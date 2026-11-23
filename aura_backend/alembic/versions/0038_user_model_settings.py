"""user model settings: a person's standing defaults for how their chats are answered

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-23

Read and written by ``aura_core/adapters/postgres_model_settings.py``, which issues no DDL of its own
(one schema authority, CB-20). One row per person (``aura_core/domain/model_settings.py``).

* Every column except the key is NULLable: NULL means "no preference", and the server's own default
  applies. A chat request that names a value wins over the row.
* The CHECKs repeat the API's ranges and the five routing policies, the last guard if a write ever
  bypassed it. A routing policy is the only model choice stored: never a provider or a model name,
  because which model may see a piece of content is decided by its data classification, not by a
  preference.

Nothing existing is touched. Downgrade drops the table.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    r"""
    CREATE TABLE IF NOT EXISTS aura_user_model_settings (
        owner_id          TEXT             PRIMARY KEY CHECK (length(owner_id) > 0),
        routing_policy    TEXT             CHECK (routing_policy IN ('local_first', 'cheapest', 'fastest', 'quality_first', 'balanced')),
        require_local     BOOLEAN,
        temperature       DOUBLE PRECISION CHECK (temperature BETWEEN 0 AND 2),
        max_output_tokens INTEGER          CHECK (max_output_tokens BETWEEN 16 AND 8192),
        updated_at        TIMESTAMPTZ      NOT NULL
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_user_model_settings")
