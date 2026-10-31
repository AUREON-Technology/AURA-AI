"""per-user data keys for the synced personal-OS fields (ADR-032)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-31

The owner chose envelope encryption for the synced tables' ``*_enc`` fields (ADR-032, option A):
each user gets a 32-byte data key, generated server-side and stored here only wrapped by the
server key-encryption key (``AURA_DATA_ENCRYPTION_KEYS``). The table is read and written by
``aura_core/adapters/postgres_user_data_keys.py``, which issues no DDL of its own.

* ``owner_id`` is the auth subject, like every other ``aura_*`` table, and references
  ``users(auth_subject)`` with ``ON DELETE CASCADE``: erasing a user deletes their keys with the
  row, so any of their ciphertext that survives elsewhere is unreadable (crypto-shredding).
* ``kek_id`` names the key-encryption key that wrapped the row, so a KEK rotation finds what is
  left under the old one (``scripts/reencrypt_fields.py``).
* ``status``: one ``primary`` per owner (partial unique index) seals new values; demoted keys are
  ``decrypt_only`` and stay, so values sealed under them stay readable.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_user_data_keys (
        owner_id     TEXT        NOT NULL
                                 REFERENCES users (auth_subject) ON DELETE CASCADE,
        kid          TEXT        NOT NULL CHECK (kid ~ '^[0-9a-f]{8}$'),
        wrapped_key  TEXT        NOT NULL,
        kek_id       TEXT,
        status       TEXT        NOT NULL DEFAULT 'primary'
                                 CHECK (status IN ('primary', 'decrypt_only')),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (owner_id, kid)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_aura_user_data_keys_primary
        ON aura_user_data_keys (owner_id) WHERE status = 'primary'
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Dropping this table destroys every user's data key, and with it the ability to read any
    # gcm2 value. Only ever run against a database whose synced rows are still legacy-only.
    op.execute("DROP TABLE IF EXISTS aura_user_data_keys")
