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
    # gcm2 value (audit F34). So it only runs on a table with no keys in it -- an empty database,
    # as the migration round-trip test walks, or one whose synced rows are still legacy-only.
    # With keys present it refuses, and Alembic's single transaction rolls back the whole run.
    op.execute(REFUSE_WHILE_KEYS_EXIST)
    op.execute("DROP TABLE IF EXISTS aura_user_data_keys")


#: In SQL rather than Python so it also holds in `alembic downgrade --sql` output. EXECUTE, so a
#: database where the table is already gone does not fail to plan the count.
REFUSE_WHILE_KEYS_EXIST = """
    DO $$
    DECLARE
        keys bigint;
    BEGIN
        IF to_regclass('aura_user_data_keys') IS NOT NULL THEN
            EXECUTE 'SELECT count(*) FROM aura_user_data_keys' INTO keys;
            IF keys > 0 THEN
                RAISE EXCEPTION 'refusing to downgrade 0019: aura_user_data_keys holds % wrapped data key(s), and dropping them makes every value sealed under them unreadable for good', keys
                    USING HINT = 'Re-encrypt those values to the legacy format and delete the keys first, or restore a backup taken before 0019.';
            END IF;
        END IF;
    END
    $$
"""
