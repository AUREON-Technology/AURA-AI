"""Postgres implementation of the ``EngineerRunStore`` port.

Its own table, its own schema management, sharing only the connection pool -- the same pattern
``postgres_personal_os.py`` uses. See ``aura_core/domain/engineering.py`` for why this is a
plaintext, server-owned table rather than an extension of the zero-knowledge-encrypted legacy
tables.

Deliberately its own table (``aura_engineer_runs``), not a row in the v2 ``SYNCABLE_MODELS``
schema (``infrastructure/db/models.py``): those tables key ``user_id`` to the v2 relational
``users.id`` UUID, while every ``aura_core`` domain object (this one included) is keyed by the
`UserId` value object's string form (the auth-subject claim) -- the same reason
``PersonalOsRecord`` isn't in ``SYNCABLE_MODELS`` either. The client reads this data through a
dedicated endpoint (``GET /api/v3/engineering/runs``) instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.engineering import EngineerRun
from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import EngineerRunId, UserId
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresEngineerRunStore"]

_SCHEMA = (
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
    # The shape `list_for_user` asks for: this user, optionally since some time, newest first.
    """
    CREATE INDEX IF NOT EXISTS ix_aura_engineer_runs_user_created
        ON aura_engineer_runs (user_id, created_at DESC)
    """,
)


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PostgresEngineerRunStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                for statement in _SCHEMA:
                    await conn.execute(statement)
        except Exception as exc:
            raise _unavailable(f"could not create the engineer-runs schema: {exc}") from exc
        self._schema_ready = True

    @staticmethod
    def _run(row: Mapping[str, Any]) -> EngineerRun:
        return EngineerRun(
            id=EngineerRunId(row["id"]),
            user_id=UserId(row["user_id"]),
            turn_id=row["turn_id"],
            correlation_id=row["correlation_id"],
            persona=row["persona"],
            tool=row["tool"],
            call_id=row["call_id"],
            status=row["status"],
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
            pr_url=row["pr_url"],
            branch=row["branch"],
            commit_sha=row["commit_sha"],
            sandbox_transcript=row["sandbox_transcript"],
        )

    async def record(self, run: EngineerRun) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_engineer_runs
                        (id, user_id, turn_id, correlation_id, persona, tool, call_id, status,
                         pr_url, branch, commit_sha, sandbox_transcript, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status, pr_url = EXCLUDED.pr_url,
                        branch = EXCLUDED.branch, commit_sha = EXCLUDED.commit_sha,
                        sandbox_transcript = EXCLUDED.sandbox_transcript,
                        updated_at = EXCLUDED.updated_at
                    """,
                    run.id.value, run.user_id.value, run.turn_id, run.correlation_id,
                    run.persona, run.tool, run.call_id, run.status,
                    run.pr_url, run.branch, run.commit_sha, run.sandbox_transcript,
                    run.created_at, run.updated_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not write engineer run: {exc}") from exc

    async def list_for_user(
        self, user_id: UserId, *, since: datetime | None = None, limit: int = 200,
    ) -> Sequence[EngineerRun]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if since is not None:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_engineer_runs
                        WHERE user_id = $1 AND created_at >= $2
                        ORDER BY created_at DESC LIMIT $3
                        """,
                        user_id.value, since, limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_engineer_runs
                        WHERE user_id = $1
                        ORDER BY created_at DESC LIMIT $2
                        """,
                        user_id.value, limit,
                    )
        except Exception as exc:
            raise _unavailable(f"could not list engineer runs: {exc}") from exc
        return [self._run(row) for row in rows]
