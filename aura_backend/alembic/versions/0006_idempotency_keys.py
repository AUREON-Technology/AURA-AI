"""idempotency keys

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24

Adds `aura_idempotency_keys`: one row per `(user_id, operation, Idempotency-Key)` a v3 client
sent on a side-effecting request (roadmap P1.4, `aura_core/api/idempotency.py`). A completed row
holds the response to replay on a retry; an `in_progress` row is the atomic claim that stops a
concurrent duplicate from running the operation twice.

Written as raw `CREATE ... IF NOT EXISTS` rather than `op.create_table`: at the time the adapter
also created the table on first use, so a gateway that served a keyed request before this migration
ran must not make `alembic upgrade head` fail on an existing table. (The adapter's copy was removed when Alembic became the only schema authority, CB-20.)

`user_id` is the v3 `UserId` value (the authenticated subject string), not a foreign key to the v2
`users.id` UUID -- the same convention as `aura_documents.owner_id` and `aura_memories.owner_id`.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS aura_idempotency_keys (
            user_id           TEXT        NOT NULL,
            operation         TEXT        NOT NULL,
            idem_key          TEXT        NOT NULL,
            request_hash      TEXT        NOT NULL,
            status            TEXT        NOT NULL
                CHECK (status IN ('in_progress', 'completed', 'failed')),
            claim_token       TEXT        NOT NULL,
            response_status   INTEGER,
            response_body     BYTEA,
            response_headers  JSONB,
            created_at        TIMESTAMPTZ NOT NULL,
            updated_at        TIMESTAMPTZ NOT NULL,
            expires_at        TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (user_id, operation, idem_key)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_aura_idempotency_keys_expires_at
            ON aura_idempotency_keys (expires_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_aura_idempotency_keys_expires_at")
    op.execute("DROP TABLE IF EXISTS aura_idempotency_keys")
