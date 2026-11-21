"""the console's Super-Admin control center (ADR-037)

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-21

Four tables, all new, so nothing existing changes:

* ``aura_control_platforms`` -- which credential-vault entry holds each platform's token. The
  token is sealed in ``aura_connector_credentials`` by the existing vault; this table never sees it.
* ``aura_control_changes`` -- the change journal: who changed what on which platform, the
  deployment it produced, and what became of it (watched, succeeded, rolled back). ``detail`` is
  redacted before it is written: variable names and secret flags, never values.
* ``aura_control_step_up`` -- step-up grants, stored as SHA-256 digests with their expiry.
* ``aura_control_leases`` -- one row per background loop (the rollback reconciler), so exactly one
  gateway process acts at a time.

The downgrade drops all four. That loses the journal and the platform-token index; the sealed
tokens stay in ``aura_connector_credentials`` until the vault entries are deleted.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE aura_control_platforms (
        platform        TEXT PRIMARY KEY,
        secret_ref      TEXT NOT NULL,
        configured_by   TEXT NOT NULL,
        configured_at   TIMESTAMPTZ NOT NULL,
        verified_at     TIMESTAMPTZ,
        verified_detail TEXT NOT NULL DEFAULT '',
        target          TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE aura_control_changes (
        change_id              TEXT PRIMARY KEY,
        actor                  TEXT NOT NULL,
        platform               TEXT NOT NULL,
        target                 TEXT NOT NULL,
        action                 TEXT NOT NULL,
        status                 TEXT NOT NULL,
        detail                 JSONB NOT NULL DEFAULT '{}'::jsonb,
        deployment_id          TEXT,
        previous_deployment_id TEXT,
        restore_ref            TEXT,
        reason                 TEXT NOT NULL DEFAULT '',
        created_at             TIMESTAMPTZ NOT NULL,
        updated_at             TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX aura_control_changes_created_idx ON aura_control_changes (created_at DESC)",
    "CREATE INDEX aura_control_changes_actor_idx ON aura_control_changes (actor, created_at)",
    """
    CREATE INDEX aura_control_changes_open_idx ON aura_control_changes (status)
        WHERE status IN ('watching', 'rolling_back')
    """,
    """
    CREATE TABLE aura_control_step_up (
        digest     TEXT PRIMARY KEY,
        subject    TEXT NOT NULL,
        email      TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX aura_control_step_up_subject_idx ON aura_control_step_up (subject)",
    """
    CREATE TABLE aura_control_leases (
        name       TEXT PRIMARY KEY,
        holder     TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
)

DOWNGRADE_STATEMENTS = (
    "DROP TABLE IF EXISTS aura_control_leases",
    "DROP TABLE IF EXISTS aura_control_step_up",
    "DROP TABLE IF EXISTS aura_control_changes",
    "DROP TABLE IF EXISTS aura_control_platforms",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
