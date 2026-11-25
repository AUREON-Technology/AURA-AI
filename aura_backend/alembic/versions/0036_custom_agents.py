"""custom agents: a person's own agents, each a built-in persona narrowed and given instructions

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-22

Read and written by ``aura_core/adapters/postgres_custom_agents.py``, which issues no DDL of its
own (one schema authority, CB-20). The built-in agents are code (``aura_core/agents/registry.py``);
this holds only what a person adds on top of one of them (``aura_core/domain/custom_agents.py``).

* ``owner_id`` is the ``UserId`` string form, like ``aura_engineer_consents``; every read and write
  is scoped to it.
* ``base_agent_id`` names the built-in persona. It is a plain string, not a foreign key: the
  built-ins are not rows. ``engineer`` is refused by a CHECK as well as by the API.
* ``allowed_tools`` is a JSON array or NULL (NULL keeps all of the base persona's tools);
  ``risk_ceiling`` NULL keeps the base's.
* The unique index on ``(owner_id, lower(name))`` makes a name unique per owner ignoring case.
  The per-owner limit is enforced by the store, atomically with the insert.

Nothing existing is touched. Downgrade drops the table.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    r"""
    CREATE TABLE IF NOT EXISTS aura_custom_agents (
        id             UUID        PRIMARY KEY,
        owner_id       TEXT        NOT NULL CHECK (length(owner_id) > 0),
        name           TEXT        NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
        description    TEXT        NOT NULL DEFAULT '' CHECK (length(description) <= 280),
        base_agent_id  TEXT        NOT NULL CHECK (length(base_agent_id) > 0 AND base_agent_id <> 'engineer'),
        instruction    TEXT        NOT NULL DEFAULT '' CHECK (length(instruction) <= 4000),
        allowed_tools  JSONB,
        risk_ceiling   TEXT        CHECK (risk_ceiling IN ('PUBLIC', 'PERSONAL', 'CONFIDENTIAL', 'SYSTEM')),
        created_at     TIMESTAMPTZ NOT NULL,
        updated_at     TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_aura_custom_agents_owner_name ON aura_custom_agents (owner_id, lower(name))",
    "CREATE INDEX IF NOT EXISTS ix_aura_custom_agents_owner_created ON aura_custom_agents (owner_id, created_at)",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_custom_agents")
