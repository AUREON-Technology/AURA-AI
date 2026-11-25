"""local copy of the OSV vulnerability advisories (dependency scan, no network at scan time)

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-25

``scan_code_security`` matches the versions a lockfile pins against advisories but makes no network
call, so before this it could only use an advisory file the caller supplied. A scheduled job
(``learning/osv_sync.py``, off by default) now copies the public OSV advisories for PyPI and npm
into these tables and the scan reads them (``adapters/postgres_advisories.py``; no DDL there).

``aura_osv_advisories`` has one row per (ecosystem, package, advisory): an advisory that affects
several packages is stored once for each, so a lookup by package is one index range scan.
``aura_osv_sync`` is one row per ecosystem recording how fresh the copy is, which the scan reports
so an out-of-date copy is never mistaken for a clean bill of health.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_osv_advisories (
        ecosystem    TEXT        NOT NULL CHECK (length(ecosystem) > 0),
        package      TEXT        NOT NULL CHECK (length(package) > 0),
        advisory_id  TEXT        NOT NULL CHECK (length(advisory_id) > 0),
        modified     TIMESTAMPTZ NOT NULL,
        payload      JSONB       NOT NULL,
        PRIMARY KEY (ecosystem, package, advisory_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS aura_osv_advisories_by_id
        ON aura_osv_advisories (ecosystem, advisory_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_osv_sync (
        ecosystem        TEXT        PRIMARY KEY CHECK (length(ecosystem) > 0),
        newest_modified  TIMESTAMPTZ,
        synced_at        TIMESTAMPTZ,
        last_attempt_at  TIMESTAMPTZ,
        last_error       TEXT
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_osv_sync")
    op.execute("DROP TABLE IF EXISTS aura_osv_advisories")
