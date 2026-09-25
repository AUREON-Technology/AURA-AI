"""agent runs (Agent Platform 2.0, GAP-024)

Revision ID: 0007_agent_runs
Revises: 0006
Create Date: 2026-09-25

Adds `aura_agent_runs` (one row per bounded agent loop, any persona) and `aura_agent_steps` (its
append-only observations, decisions, tool calls, approvals, evaluations, failure and completion).
Both are written by `aura_core/adapters/postgres_engineer_runs.py` -- the engineer-runs adapter,
extended rather than duplicated -- and the SQL below is kept textually in step with that module's
`AGENT_RUNS_SCHEMA` (`tests/unit/test_agent_run_store.py` asserts it). Like every other `aura_*`
table the adapter also creates these on first use, so this migration is `IF NOT EXISTS`
throughout: a gateway that recorded a run before the migration ran must not make
`alembic upgrade head` fail.

Named `0007_agent_runs` rather than a bare `0007` because a knowledge-graph migration is being
added after `0006` in parallel; whichever lands second re-chains its `down_revision`.

No raw prompt or response content is stored: the goal is a digest plus a length, a tool call's
arguments are a digest, and `detail` holds only short structured facts (see
`aura_core/domain/agent_runs.py`). `user_id` is the v3 `UserId` string, not a foreign key to the
v2 `users.id` UUID -- the same convention as `aura_engineer_runs.user_id`.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007_agent_runs"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_agent_runs (
        id                   TEXT             PRIMARY KEY,
        user_id              TEXT             NOT NULL,
        agent_id             TEXT             NOT NULL,
        turn_id              TEXT             NOT NULL,
        correlation_id       TEXT             NOT NULL,
        status               TEXT             NOT NULL,
        goal_digest          TEXT             NOT NULL DEFAULT '',
        goal_chars           INTEGER          NOT NULL DEFAULT 0,
        budget               JSONB            NOT NULL DEFAULT '{}'::jsonb,
        iterations           INTEGER          NOT NULL DEFAULT 0,
        replans              INTEGER          NOT NULL DEFAULT 0,
        replans_recovered    INTEGER          NOT NULL DEFAULT 0,
        tokens_used          INTEGER          NOT NULL DEFAULT 0,
        tool_calls           INTEGER          NOT NULL DEFAULT 0,
        tool_calls_ok        INTEGER          NOT NULL DEFAULT 0,
        risk_spent           INTEGER          NOT NULL DEFAULT 0,
        elapsed_s            DOUBLE PRECISION NOT NULL DEFAULT 0,
        unsafe_prevented     INTEGER          NOT NULL DEFAULT 0,
        plan_steps_total     INTEGER          NOT NULL DEFAULT 0,
        plan_steps_succeeded INTEGER          NOT NULL DEFAULT 0,
        stop_reason          TEXT             NULL,
        exhausted_budget     TEXT             NULL,
        created_at           TIMESTAMPTZ      NOT NULL,
        updated_at           TIMESTAMPTZ      NOT NULL,
        finished_at          TIMESTAMPTZ      NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_agent_runs_user_created
        ON aura_agent_runs (user_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_agent_steps (
        id                TEXT        PRIMARY KEY,
        run_id            TEXT        NOT NULL REFERENCES aura_agent_runs (id) ON DELETE CASCADE,
        user_id           TEXT        NOT NULL,
        seq               INTEGER     NOT NULL,
        iteration         INTEGER     NOT NULL,
        phase             TEXT        NOT NULL,
        kind              TEXT        NOT NULL,
        status            TEXT        NOT NULL DEFAULT '',
        tool              TEXT        NULL,
        call_id           TEXT        NULL,
        error_kind        TEXT        NULL,
        arguments_digest  TEXT        NULL,
        risk_cost         INTEGER     NOT NULL DEFAULT 0,
        detail            JSONB       NOT NULL DEFAULT '{}'::jsonb,
        created_at        TIMESTAMPTZ NOT NULL,
        UNIQUE (run_id, seq)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aura_agent_steps")
    op.execute("DROP INDEX IF EXISTS ix_aura_agent_runs_user_created")
    op.execute("DROP TABLE IF EXISTS aura_agent_runs")
