"""V2 relational schema (Doc/V2_BLUEPRINT.md §6).

Every syncable table shares the same (user_id, sync_txid, deleted_at)
pattern — that is the entire cursor-sync protocol implemented in
api/v2/sync.py: `WHERE user_id = ? AND sync_txid >= ?`. This single
generic query replaces V1's five hand-written per-table Firestore
listeners/LWW comparators.

Text fields suffixed `_enc` store AES-256-GCM ciphertext: `gcm2:<kid>:<iv>:<ct>`
under the user's data key (ADR-032), or a not-yet-migrated device's legacy
`gcm:<iv>:<ct>`. The sync endpoint stores and returns them as-is, but this
is NOT zero-knowledge any more: the server holds each user's data key
(`aura_user_data_keys`, wrapped by AURA_DATA_ENCRYPTION_KEYS) so the agent's
personal-OS tools can read and write these rows.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    cast,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


#: The writing transaction's id as a bigint -- the pull cursor's unit (see SyncableMixin).
SYNC_TXID_SQL = "(pg_current_xact_id()::text)::bigint"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # The Google "sub" / Firebase uid claim from verify_token() — the stable
    # external identity. Kept distinct from `id` so the internal primary key
    # never has to change if a user's auth provider/claim format changes.
    auth_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    photo_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="User")
    # Set only when an operator assigns ``role`` on the Access page; only then does ``role``
    # count in role resolution (services/rbac.py). NULL rows hold whatever an earlier sign-in
    # wrote there and are display-only -- see migration 0015 for why.
    role_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    role_assigned_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # The user's IANA zone ("Asia/Riyadh"), sent by the client in X-Client-Timezone. Day and hour
    # boundaries in insights and the daily briefing are computed in it (services/user_time.py);
    # NULL, or anything that no longer resolves, means UTC. Migration 0018.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The language the app shows ("ar" or "en"), sent in X-Client-Language. Server-written text
    # with no request behind it (the daily briefing, scheduled insights) is written in it;
    # NULL means never reported and reads as English (services/user_language.py). Migration 0022.
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncableMixin:
    """Adds the columns every table needs to participate in cursor-based sync.

    `deleted_at` is a soft delete — tombstones are returned to clients on the
    next pull instead of a separate pending-deletes table, unlike V1's Drift
    `PendingDeletes`.

    **Three columns, three different jobs.**

    `sync_txid` drives the pull cursor (`WHERE sync_txid >= ?`). It is the id
    of the Postgres transaction that last wrote the row, assigned by the
    database (`pg_current_xact_id()`), never by the application. It replaced
    `updated_at` as the cursor because a clock cannot order commits: Postgres
    `now()` is the *transaction start* time, so a push that began before a
    concurrent pull and committed after it wrote rows stamped earlier than
    that pull's cursor, and no later pull ever returned them (audit F-12).
    Transaction ids pair with a snapshot's `xmin` instead: every transaction
    below a snapshot's xmin has finished, so a cursor taken as that xmin can
    never pass over a row still waiting to commit. See `api/v2/sync.py`.

    `updated_at` is the *server's* wall clock: when the row was last written.
    Informational only now; nothing orders by it.

    `client_updated_at` is the *client's* clock and drives conflict detection.
    It exists because those two questions were previously answered by the same
    column, which is wrong for an offline-first API: comparing a client's
    "I edited this on Jan 1" against the server's "I wrote this on Aug 10"
    rejects the edit as stale whenever a device syncs later than it edited —
    the normal case this API exists to serve. Nullable so rows written before
    the column existed keep working; `api/v2/sync.py` falls back to
    `updated_at` for them.
    """

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    client_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Database-assigned on every INSERT (server default) and UPDATE (`onupdate`, which SQLAlchemy
    # applies to ORM flushes and to Core `update()` alike). `xid8` has no direct cast to bigint;
    # going through text is the documented route, and a 64-bit xid8 never wraps.
    sync_txid: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text(SYNC_TXID_SQL),
        onupdate=cast(cast(func.pg_current_xact_id(), Text), BigInteger),
    )


class Task(Base, SyncableMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_user_updated", "user_id", "updated_at"),
        Index("ix_tasks_user_sync_txid", "user_id", "sync_txid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # The Flutter client already encrypts task titles (see aura_client's
    # `EncryptedTextConverter` on Tasks.title) — title_enc matches that
    # reality rather than assuming only descriptions are sensitive.
    title_enc: Mapped[str] = mapped_column(Text)
    description_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_task_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class CalendarEvent(Base, SyncableMixin):
    __tablename__ = "calendar_events"
    __table_args__ = (
        Index("ix_calendar_events_user_updated", "user_id", "updated_at"),
        Index("ix_calendar_events_user_sync_txid", "user_id", "sync_txid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title_enc: Mapped[str] = mapped_column(Text)
    description_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    google_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class FinanceRecord(Base, SyncableMixin):
    __tablename__ = "finance_records"
    __table_args__ = (
        Index("ix_finance_records_user_updated", "user_id", "updated_at"),
        Index("ix_finance_records_user_sync_txid", "user_id", "sync_txid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(String(16))  # "income" | "expense"
    amount: Mapped[float] = mapped_column(Float)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HealthRecord(Base, SyncableMixin):
    __tablename__ = "health_records"
    __table_args__ = (
        Index("ix_health_records_user_updated", "user_id", "updated_at"),
        Index("ix_health_records_user_sync_txid", "user_id", "sync_txid"),
        UniqueConstraint("user_id", "record_date", name="uq_health_records_user_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    record_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    water_ml: Mapped[int] = mapped_column(Integer, default=0)
    sleep_minutes: Mapped[int] = mapped_column(Integer, default=0)
    calories: Mapped[int] = mapped_column(Integer, default=0)
    exercise_minutes: Mapped[int] = mapped_column(Integer, default=0)


class Project(Base, SyncableMixin):
    """A container for tasks toward some outcome (Phase D1). ``status``/``target_date`` are
    plaintext -- unlike ``title_enc``/``description_enc`` -- so the kernel can reason about
    deadlines and state without ever decrypting a project's name (ADR: see
    ``aura_core/domain/personal_os.py`` for why the same split exists there)."""

    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_updated", "user_id", "updated_at"),
        Index("ix_projects_user_sync_txid", "user_id", "sync_txid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title_enc: Mapped[str] = mapped_column(Text)
    description_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|completed|archived|on_hold
    target_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Goal(Base, SyncableMixin):
    """A user's aspiration, optionally under a :class:`Project` (Phase D1/D2). ``category``,
    ``status``, ``target_date``, ``progress_pct`` and ``priority`` are plaintext by the same
    reasoning as ``Project``'s -- structural fields the context engine and the prioritisation
    tool (Phase D2/D3) can read without decrypting a goal's actual text."""

    __tablename__ = "goals"
    __table_args__ = (
        Index("ix_goals_user_updated", "user_id", "updated_at"),
        Index("ix_goals_user_sync_txid", "user_id", "sync_txid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title_enc: Mapped[str] = mapped_column(Text)
    description_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|achieved|abandoned
    priority: Mapped[str] = mapped_column(String(16), default="medium")  # low|medium|high
    target_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)


class GitHubIdentity(Base):
    """One connected GitHub account per user (per-user OAuth connect flow,
    ``api/v2/github_integration.py``) -- distinct from the deployment-wide admin token in
    ``aura_core/config/settings.py``'s ``GitHubSettings``. Not a :class:`SyncableMixin` table:
    nothing here is client-visible or cursor-synced, it is a server-only OAuth credential.
    ``*_enc`` columns hold ciphertext from ``aura_core/adapters/secrets_crypto.py``'s own key,
    which the server decrypts on use. The synced tables' ``*_enc`` columns use a different key
    (each user's data key, ADR-032).
    """

    __tablename__ = "github_identities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True,
    )
    github_user_id: Mapped[int] = mapped_column(BigInteger)
    github_login: Mapped[str] = mapped_column(String(255))
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scopes: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Memory(Base, SyncableMixin):
    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_user_updated", "user_id", "updated_at"),
        Index("ix_memories_user_sync_txid", "user_id", "sync_txid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    fact_enc: Mapped[str] = mapped_column(Text)
    # References the corresponding embedding point in Qdrant (§6 — vectors
    # stay in Qdrant, only metadata + ciphertext live in Postgres).
    qdrant_point_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Message(Base, SyncableMixin):
    """Chat turns in v2 cursor sync (AURA-014).

    Ciphertext in ``*_enc`` matches Drift ``Messages`` (content, citations,
    tool calls). ``persona`` is Drift ``agentId``.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_user_updated", "user_id", "updated_at"),
        Index("ix_messages_user_sync_txid", "user_id", "sync_txid"),
        Index("ix_messages_user_conversation", "user_id", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    content_enc: Mapped[str] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citations_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Insight(Base):
    """AURA Insights feed (blueprint §8) — not part of the Phase 0 sync
    protocol (insights are generated server-side, not user-editable), but
    the schema is defined now so Phase 3's Celery Beat job has a home."""

    __tablename__ = "insights"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    surfaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


SYNCABLE_MODELS: dict[str, type[Base]] = {
    "tasks": Task,
    "calendar_events": CalendarEvent,
    "finance_records": FinanceRecord,
    "health_records": HealthRecord,
    "memories": Memory,
    "messages": Message,
    "projects": Project,
    "goals": Goal,
}
