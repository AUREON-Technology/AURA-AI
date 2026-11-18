"""workspace entitlements and usage counters (SaaS phase 1, plan C)

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17

Read and written by ``aura_core/adapters/postgres_plans.py``, which issues no DDL of its own.

* ``aura_workspace_entitlements``: a workspace's plan and seats. No row means the default (free)
  plan. ``source`` says who set it: an operator (``manual``) or billing (``subscription``, Plan D).
* ``aura_usage_counters``: one row per (workspace, metric, period); ``value`` never negative. The
  monthly ``messages`` counter uses period ``YYYY-MM``; ``storage_bytes`` uses ``all``.

Both cascade with the workspace.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_workspace_entitlements (
        workspace_id TEXT        PRIMARY KEY REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        plan_id      TEXT        NOT NULL,
        seats        INTEGER     NOT NULL CHECK (seats >= 1),
        source       TEXT        NOT NULL CHECK (source IN ('manual', 'subscription')),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_by   TEXT        NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_usage_counters (
        workspace_id TEXT   NOT NULL REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        metric       TEXT   NOT NULL,
        period       TEXT   NOT NULL,
        value        BIGINT NOT NULL DEFAULT 0 CHECK (value >= 0),
        PRIMARY KEY (workspace_id, metric, period)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_usage_counters")
    op.execute("DROP TABLE IF EXISTS aura_workspace_entitlements")
