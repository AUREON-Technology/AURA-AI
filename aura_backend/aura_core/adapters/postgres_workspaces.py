"""Postgres implementation of the ``WorkspaceStore`` port.

Its own table (Alembic's ``0016`` baseline), sharing only the connection pool -- the same pattern
``postgres_engineer_runs.py`` and ``postgres_personal_os.py`` use, and keyed by the ``UserId``
value object's string form (the auth-subject claim) rather than the v2 relational ``users.id``
UUID, for the reason spelled out in ``postgres_engineer_runs.py``'s module docstring.

Unlike the engineer-run ledger next to it, these rows are mutable and are *not* synced to the
client through a cursor: a workspace is live host state, and a client replica of it would be
stale the moment the reaper ran. The Flutter app reads it on demand through
``GET /api/v3/engineering/workspaces`` instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from ..domain.engineering import AgentWorkspace
from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import UserId, WorkspaceId
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresWorkspaceStore"]


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime) -> datetime:
    """asyncpg hands back naive datetimes for a TIMESTAMPTZ column in some configurations, and a
    naive value compared against an aware ``now()`` raises rather than expiring anything -- so
    normalise on the way out, the same way ``postgres_engineer_runs`` does."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PostgresWorkspaceStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    @staticmethod
    def _workspace(row: Mapping[str, Any]) -> AgentWorkspace:
        return AgentWorkspace(
            id=WorkspaceId(row["id"]),
            user_id=UserId(row["user_id"]),
            repo=row["repo"],
            workspace_path=row["workspace_path"],
            status=row["status"],
            container_id=row["container_id"],
            branch=row["branch"],
            created_at=_utc(row["created_at"]),
            last_active_at=_utc(row["last_active_at"]),
        )

    async def upsert(self, workspace: AgentWorkspace) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_agent_workspaces
                        (id, user_id, repo, workspace_path, status, container_id, branch,
                         created_at, last_active_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (id) DO UPDATE SET
                        workspace_path = EXCLUDED.workspace_path,
                        status = EXCLUDED.status,
                        container_id = EXCLUDED.container_id,
                        branch = EXCLUDED.branch,
                        last_active_at = EXCLUDED.last_active_at
                    """,
                    workspace.id.value, workspace.user_id.value, workspace.repo,
                    workspace.workspace_path, workspace.status, workspace.container_id,
                    workspace.branch, workspace.created_at, workspace.last_active_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not write agent workspace: {exc}") from exc

    async def get(self, user_id: UserId, repo: str) -> AgentWorkspace | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM aura_agent_workspaces WHERE user_id = $1 AND repo = $2",
                    user_id.value, repo,
                )
        except Exception as exc:
            raise _unavailable(f"could not read agent workspace: {exc}") from exc
        return self._workspace(row) if row else None

    async def get_by_id(self, workspace_id: WorkspaceId) -> AgentWorkspace | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM aura_agent_workspaces WHERE id = $1", workspace_id.value
                )
        except Exception as exc:
            raise _unavailable(f"could not read agent workspace: {exc}") from exc
        return self._workspace(row) if row else None

    async def list_for_user(self, user_id: UserId) -> list[AgentWorkspace]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM aura_agent_workspaces
                    WHERE user_id = $1
                    ORDER BY last_active_at DESC
                    """,
                    user_id.value,
                )
        except Exception as exc:
            raise _unavailable(f"could not list agent workspaces: {exc}") from exc
        return [self._workspace(row) for row in rows]

    async def list_all(self) -> list[AgentWorkspace]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM aura_agent_workspaces ORDER BY last_active_at DESC"
                )
        except Exception as exc:
            raise _unavailable(f"could not list agent workspaces: {exc}") from exc
        return [self._workspace(row) for row in rows]

    async def list_idle_since(self, cutoff: datetime) -> list[AgentWorkspace]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM aura_agent_workspaces
                    WHERE last_active_at <= $1
                    ORDER BY last_active_at ASC
                    """,
                    cutoff,
                )
        except Exception as exc:
            raise _unavailable(f"could not list idle agent workspaces: {exc}") from exc
        return [self._workspace(row) for row in rows]

    async def touch(
        self, workspace_id: WorkspaceId, *, at: datetime, status: str | None = None
    ) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                # One statement, and it writes only the two columns activity owns. A
                # read-modify-write here would let two concurrent tool calls in the same
                # workspace clobber each other's status with a stale value.
                await conn.execute(
                    """
                    UPDATE aura_agent_workspaces
                    SET last_active_at = $2,
                        status = COALESCE($3, status)
                    WHERE id = $1
                    """,
                    workspace_id.value, at, status,
                )
        except Exception as exc:
            raise _unavailable(f"could not touch agent workspace: {exc}") from exc

    async def delete(self, workspace_id: WorkspaceId) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM aura_agent_workspaces WHERE id = $1", workspace_id.value
                )
        except Exception as exc:
            raise _unavailable(f"could not delete agent workspace: {exc}") from exc
