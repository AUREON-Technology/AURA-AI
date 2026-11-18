"""subscriptions and payments (SaaS phase 1, plan D1)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-17

Read and written by ``aura_core/adapters/postgres_billing.py``, which issues no DDL of its own.

* ``aura_subscriptions``: one per workspace. AURA owns the lifecycle; the payment provider only
  collects each payment. ``card_token`` is the provider's token for renewals (never returned by
  any route).
* ``aura_payments``: one row per payment AURA asked for. ``id`` is sent to Fawry as
  ``merchantRefNum`` and is the idempotency key for its notifications; a payment leaves
  ``pending`` exactly once (a conditional UPDATE).

Both cascade with the workspace.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_subscriptions (
        workspace_id        TEXT        PRIMARY KEY REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        plan_id             TEXT        NOT NULL,
        seats               INTEGER     NOT NULL CHECK (seats >= 1),
        status              TEXT        NOT NULL CHECK (status IN
                                ('pending', 'active', 'past_due', 'grace', 'canceled', 'expired')),
        provider            TEXT        NOT NULL,
        period_start        TIMESTAMPTZ NOT NULL,
        period_end          TIMESTAMPTZ NOT NULL,
        grace_until         TIMESTAMPTZ,
        last_payment_id     TEXT,
        card_token          TEXT,
        customer_profile_id TEXT,
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_payments (
        id               TEXT        PRIMARY KEY,
        workspace_id     TEXT        NOT NULL REFERENCES aura_workspaces (id) ON DELETE CASCADE,
        plan_id          TEXT        NOT NULL,
        seats            INTEGER     NOT NULL CHECK (seats >= 1),
        amount_piastres  BIGINT      NOT NULL CHECK (amount_piastres > 0),
        method           TEXT        NOT NULL,
        purpose          TEXT        NOT NULL CHECK (purpose IN ('initial', 'renewal')),
        status           TEXT        NOT NULL CHECK (status IN
                             ('pending', 'received', 'paid', 'expired', 'failed', 'mismatch', 'refunded')),
        provider_ref     TEXT,
        reference_number TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at       TIMESTAMPTZ,
        paid_at          TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_aura_payments_status_created ON aura_payments (status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_aura_payments_workspace ON aura_payments (workspace_id, created_at)",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_payments")
    op.execute("DROP TABLE IF EXISTS aura_subscriptions")
