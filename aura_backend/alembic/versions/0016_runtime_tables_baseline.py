"""baseline the tables the Postgres adapters create at runtime

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-29

Sixteen ``aura_*`` tables existed only as ``CREATE TABLE IF NOT EXISTS`` inside the adapters
(audit F-27 / CB-20): documents, chunks and memories among them. Nothing in Alembic knew about
them, so ``alembic upgrade head`` on an empty database did not produce the schema the code
queries, and ``alembic revision --autogenerate`` -- which migration 0001 tells you to use --
would have emitted a DROP for each (none is in ``Base.metadata``; ``env.py`` now excludes them).

These are the adapters' own statements, copied verbatim on this date and all ``IF NOT EXISTS``,
so on a database where the adapters already created the tables this migration changes nothing.
The adapters still run their DDL on first use; ``tests/architecture/test_runtime_tables_have_migrations.py``
fails if an adapter creates a table no migration does, and pins these statements to the
adapters' current text.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    'aura_documents',
    'aura_chunks',
    'aura_memories',
    'aura_audit_events',
    'aura_event_outbox',
    'aura_approvals',
    'aura_agent_workspaces',
    'aura_engineer_runs',
    'aura_experiments',
    'aura_experiment_runs',
    'aura_feedback',
    'aura_finetune_jobs',
    'aura_knowledge_sources',
    'aura_model_versions',
    'aura_personal_os_records',
    'aura_turn_telemetry',
)

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_approvals (
        id               TEXT        PRIMARY KEY,
        kind             TEXT        NOT NULL,
        summary          TEXT        NOT NULL,
        artefact_digest  TEXT        NOT NULL,
        payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,
        status           TEXT        NOT NULL,
        requested_at     TIMESTAMPTZ NOT NULL,
        expires_at       TIMESTAMPTZ,
        decided_by       TEXT,
        decided_at       TIMESTAMPTZ,
        note             TEXT        NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_audit_events (
        id              TEXT        PRIMARY KEY,
        at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        actor           TEXT        NOT NULL,
        actor_role      TEXT        NOT NULL DEFAULT '',
        action          TEXT        NOT NULL,
        resource        TEXT        NOT NULL DEFAULT '',
        outcome         TEXT        NOT NULL,
        correlation_id  TEXT        NOT NULL DEFAULT '',
        source_ip       TEXT,
        detail          JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_engineer_runs (
        id                  TEXT        PRIMARY KEY,
        user_id             TEXT        NOT NULL,
        turn_id             TEXT        NOT NULL,
        correlation_id      TEXT        NOT NULL,
        persona             TEXT        NOT NULL,
        tool                TEXT        NOT NULL,
        call_id             TEXT        NOT NULL,
        status              TEXT        NOT NULL,
        pr_url              TEXT        NULL,
        branch              TEXT        NULL,
        commit_sha          TEXT        NULL,
        sandbox_transcript  TEXT        NULL,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_experiments (
        id           TEXT        PRIMARY KEY,
        name         TEXT        NOT NULL UNIQUE,
        description  TEXT        NOT NULL DEFAULT '',
        created_at   TIMESTAMPTZ NOT NULL,
        tags         JSONB       NOT NULL DEFAULT '[]'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_experiment_runs (
        id              TEXT        PRIMARY KEY,
        experiment_id   TEXT        NOT NULL REFERENCES aura_experiments(id),
        status          TEXT        NOT NULL,
        parameters      JSONB       NOT NULL DEFAULT '{}'::jsonb,
        metrics         JSONB       NOT NULL DEFAULT '[]'::jsonb,
        artefacts       JSONB       NOT NULL DEFAULT '{}'::jsonb,
        dataset_digest  TEXT        NOT NULL DEFAULT '',
        base_model      TEXT        NOT NULL DEFAULT '',
        code_version    TEXT        NOT NULL DEFAULT '',
        started_at      TIMESTAMPTZ NOT NULL,
        finished_at     TIMESTAMPTZ,
        error           TEXT,
        notes           TEXT        NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_feedback (
        id          TEXT        PRIMARY KEY,
        owner_id    TEXT        NOT NULL,
        turn_id     TEXT        NOT NULL DEFAULT '',
        rating      INTEGER     NOT NULL,
        correction  TEXT,
        comment     TEXT,
        prompt      TEXT        NOT NULL DEFAULT '',
        response    TEXT        NOT NULL DEFAULT '',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_finetune_jobs (
        id               TEXT        PRIMARY KEY,
        approval_id      TEXT        NOT NULL,
        dataset_digest   TEXT        NOT NULL,
        base_model       TEXT        NOT NULL DEFAULT '',
        status           TEXT        NOT NULL,
        model_id         TEXT        NOT NULL DEFAULT 'aura-ft',
        version          TEXT        NOT NULL DEFAULT '',
        artefact_uri     TEXT        NOT NULL DEFAULT '',
        provider_kind    TEXT        NOT NULL DEFAULT 'ollama',
        run_id           TEXT,
        error            TEXT,
        metrics          JSONB       NOT NULL DEFAULT '{}'::jsonb,
        parameters       JSONB       NOT NULL DEFAULT '{}'::jsonb,
        created_at       TIMESTAMPTZ NOT NULL,
        started_at       TIMESTAMPTZ,
        finished_at      TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_model_versions (
        model_id         TEXT        NOT NULL,
        version          TEXT        NOT NULL,
        provider_kind    TEXT        NOT NULL,
        base_model       TEXT        NOT NULL DEFAULT '',
        stage            TEXT        NOT NULL,
        artefact_uri     TEXT        NOT NULL DEFAULT '',
        dataset_digest   TEXT        NOT NULL DEFAULT '',
        approval_id      TEXT,
        metrics          JSONB       NOT NULL DEFAULT '{}'::jsonb,
        canary_percent   INTEGER     NOT NULL DEFAULT 0,
        created_at       TIMESTAMPTZ NOT NULL,
        promoted_at      TIMESTAMPTZ,
        notes            TEXT        NOT NULL DEFAULT '',
        PRIMARY KEY (model_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_event_outbox (
        id              TEXT        PRIMARY KEY,
        event_name      TEXT        NOT NULL,
        payload         JSONB       NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        claimed_at      TIMESTAMPTZ,
        attempts        INTEGER     NOT NULL DEFAULT 0,
        last_error      TEXT,
        dead_lettered   BOOLEAN     NOT NULL DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_personal_os_records (
        id          TEXT        PRIMARY KEY,
        owner_id    TEXT        NOT NULL,
        kind        TEXT        NOT NULL,
        fields      JSONB       NOT NULL DEFAULT '{}'::jsonb,
        source      TEXT        NOT NULL DEFAULT 'kernel',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_knowledge_sources (
        id                      TEXT        PRIMARY KEY,
        type                    TEXT        NOT NULL,
        uri                     TEXT        NOT NULL,
        owner_id                TEXT,
        trust_tier              INTEGER     NOT NULL DEFAULT 2,
        enabled                 BOOLEAN     NOT NULL DEFAULT TRUE,
        cursor                  TEXT,
        max_documents           INTEGER     NOT NULL DEFAULT 1000,
        max_bytes_per_document  INTEGER     NOT NULL DEFAULT 5242880,
        schedule_cron           TEXT        NOT NULL DEFAULT '0 2 * * *',
        credentials_ref         TEXT,
        last_success_at         TIMESTAMPTZ,
        metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_documents (
        id                TEXT        NOT NULL,
        version           INTEGER     NOT NULL,
        title             TEXT        NOT NULL DEFAULT '',
        content           TEXT        NOT NULL,
        content_hash      TEXT        NOT NULL,
        source_type       TEXT        NOT NULL DEFAULT 'upload',
        source_uri        TEXT,
        owner_id          TEXT,
        trust_tier        INTEGER     NOT NULL DEFAULT 1,
        status            TEXT        NOT NULL DEFAULT 'active',
        language          TEXT,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb,
        PRIMARY KEY (id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_chunks (
        id                TEXT        PRIMARY KEY,
        document_id       TEXT        NOT NULL,
        document_version  INTEGER     NOT NULL,
        idx               INTEGER     NOT NULL,
        content           TEXT        NOT NULL,
        heading_path      JSONB       NOT NULL DEFAULT '[]'::jsonb,
        token_estimate    INTEGER     NOT NULL DEFAULT 0,
        owner_id          TEXT,
        trust_tier        INTEGER     NOT NULL DEFAULT 1,
        metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_memories (
        id                  TEXT        PRIMARY KEY,
        kind                TEXT        NOT NULL,
        owner_id            TEXT        NOT NULL,
        content             TEXT        NOT NULL,
        subject             TEXT,
        confidence          DOUBLE PRECISION NOT NULL DEFAULT 0.7,
        embedding_ref       TEXT,
        session_id          TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_accessed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        access_count        INTEGER     NOT NULL DEFAULT 0,
        superseded_by       TEXT,
        expires_at          TIMESTAMPTZ,
        prov_source         TEXT        NOT NULL,
        prov_turn_id        TEXT,
        prov_session_id     TEXT,
        prov_extractor      TEXT,
        prov_extractor_ver  TEXT,
        prov_document_ref   TEXT,
        prov_recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_turn_telemetry (
        turn_id           TEXT              PRIMARY KEY,
        correlation_id    TEXT              NOT NULL,
        started_at        TIMESTAMPTZ       NOT NULL,
        route             TEXT              NOT NULL,
        agent             TEXT              NOT NULL,
        outcome           TEXT              NOT NULL,
        error_kind        TEXT,
        provider          TEXT,
        model             TEXT,
        total_ms          DOUBLE PRECISION  NOT NULL,
        prompt_tokens     INTEGER           NOT NULL,
        completion_tokens INTEGER           NOT NULL,
        cost_usd          DOUBLE PRECISION  NOT NULL,
        degraded          BOOLEAN           NOT NULL,
        cache_hit         BOOLEAN           NOT NULL,
        tool_calls        INTEGER           NOT NULL,
        tool_failures     INTEGER           NOT NULL,
        groundedness      DOUBLE PRECISION,
        payload           JSONB             NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_agent_workspaces (
        id              TEXT        PRIMARY KEY,
        user_id         TEXT        NOT NULL,
        repo            TEXT        NOT NULL,
        workspace_path  TEXT        NOT NULL,
        status          TEXT        NOT NULL,
        container_id    TEXT        NULL,
        branch          TEXT        NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_active_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    ALTER TABLE aura_documents
        ADD COLUMN IF NOT EXISTS cag_pinned BOOLEAN NOT NULL DEFAULT false
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_approvals_status_kind
        ON aura_approvals (status, kind, requested_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_audit_events_at
        ON aura_audit_events (at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_audit_events_actor_at
        ON aura_audit_events (actor, at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_engineer_runs_user_created
        ON aura_engineer_runs (user_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_experiment_runs_exp
        ON aura_experiment_runs (experiment_id, started_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_feedback_owner
        ON aura_feedback (owner_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_feedback_created
        ON aura_feedback (created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_finetune_jobs_status
        ON aura_finetune_jobs (status, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_model_versions_stage
        ON aura_model_versions (model_id, stage, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_event_outbox_claimable
        ON aura_event_outbox (created_at)
        WHERE NOT dead_lettered
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_personal_os_owner_kind
        ON aura_personal_os_records (owner_id, kind, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_knowledge_sources_enabled
        ON aura_knowledge_sources (enabled)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_documents_active
        ON aura_documents (owner_id, updated_at DESC)
        WHERE status = 'active'
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_documents_hash
        ON aura_documents (content_hash, owner_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_documents_cag_pinned
        ON aura_documents (owner_id)
        WHERE cag_pinned AND status = 'active'
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_chunks_document
        ON aura_chunks (document_id, document_version, idx)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_memories_owner_kind
        ON aura_memories (owner_id, kind, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_memories_provenance
        ON aura_memories (owner_id, prov_source)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_turn_telemetry_started_at
        ON aura_turn_telemetry (started_at)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ix_aura_agent_workspaces_user_repo
        ON aura_agent_workspaces (user_id, repo)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_agent_workspaces_last_active
        ON aura_agent_workspaces (last_active_at)
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Dropping these would delete every document, chunk and memory, so the downgrade only
    # forgets the baseline: the tables stay, as they did before this revision existed.
    pass
