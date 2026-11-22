"""knowledge source provenance and licence (WP3, KM-04)

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-22

``aura_knowledge_sources`` held a URL and ceilings and nothing about who published it or under
what licence, so AURA could not honour an attribution requirement or refuse a source whose terms
bar the use. Owner decision D-001 (2026-11-22): the global corpus accepts CC0, CC BY and
permissive licences only. These columns carry the facts that decision needs:

* ``name``, ``publisher``, ``domains``, ``language`` -- who and what the source is;
* ``license_spdx`` and ``license_status`` -- the licence and whether anyone checked it
  (``approved`` / ``restricted`` / ``unknown``);
* ``attribution`` -- the credit line a citation must carry.

Every column has a default, so existing rows stay valid. They start as ``license_status =
'unknown'``, which the refresh treats as *not permitted* for a global source: a source registered
before this revision is skipped until an admin states its licence. Nothing is deleted.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    "ALTER TABLE aura_knowledge_sources ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE aura_knowledge_sources ADD COLUMN IF NOT EXISTS publisher TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE aura_knowledge_sources ADD COLUMN IF NOT EXISTS domains JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE aura_knowledge_sources ADD COLUMN IF NOT EXISTS language TEXT",
    "ALTER TABLE aura_knowledge_sources ADD COLUMN IF NOT EXISTS license_spdx TEXT",
    """
    ALTER TABLE aura_knowledge_sources
        ADD COLUMN IF NOT EXISTS license_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_status IN ('approved', 'restricted', 'unknown'))
    """,
    "ALTER TABLE aura_knowledge_sources ADD COLUMN IF NOT EXISTS attribution TEXT NOT NULL DEFAULT ''",
)

# Dropping a column drops its CHECK with it, which restores 0031's table exactly. The provenance
# data is lost on downgrade by definition; the sources themselves and their ceilings are kept.
DOWNGRADE_STATEMENTS = (
    "ALTER TABLE aura_knowledge_sources DROP COLUMN IF EXISTS attribution",
    "ALTER TABLE aura_knowledge_sources DROP COLUMN IF EXISTS license_status",
    "ALTER TABLE aura_knowledge_sources DROP COLUMN IF EXISTS license_spdx",
    "ALTER TABLE aura_knowledge_sources DROP COLUMN IF EXISTS language",
    "ALTER TABLE aura_knowledge_sources DROP COLUMN IF EXISTS domains",
    "ALTER TABLE aura_knowledge_sources DROP COLUMN IF EXISTS publisher",
    "ALTER TABLE aura_knowledge_sources DROP COLUMN IF EXISTS name",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
