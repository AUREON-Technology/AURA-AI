"""Postgres :class:`~aura_core.ports.approvals.ApprovalLedger`.

Reads and writes ``aura_tool_approvals``, which Alembic revision 0038 creates; the adapter issues no
DDL (one schema authority, CB-20). Shares the container's ``PostgresPool``. Every statement carries
the owner. ``expired`` is never stored: it is a ``pending`` row whose ``expires_at`` has passed, so
the status filter compares against the caller's ``now`` instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from ..domain.approvals import ApprovalStatus, ToolApproval
from ..domain.identifiers import UserId
from .postgres_store import PostgresPool

__all__ = ["PostgresApprovalLedger"]

_COLUMNS = (
    "id, owner_id, tool, call_id, digest, session_id, turn_id, status, created_at, expires_at, "
    "decided_at, decided_via"
)
_INSERT = (
    "INSERT INTO aura_tool_approvals (id, owner_id, tool, call_id, digest, session_id, turn_id, "
    "status, created_at, expires_at) VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', $8, $9) "
    "ON CONFLICT (id) DO NOTHING"
)
_DECIDE = (
    "UPDATE aura_tool_approvals SET status = $3, decided_at = $4, decided_via = $5 "
    "WHERE owner_id = $1 AND id = ANY($2::text[]) AND status = 'pending'"
)
_GET = f"SELECT {_COLUMNS} FROM aura_tool_approvals WHERE owner_id = $1 AND id = $2"
_DELETE_OWNER = "DELETE FROM aura_tool_approvals WHERE owner_id = $1"


def _row(row: Any) -> ToolApproval:
    return ToolApproval(
        id=row["id"], owner_id=UserId(row["owner_id"]), tool=row["tool"], call_id=row["call_id"],
        digest=row["digest"], session_id=row["session_id"], turn_id=row["turn_id"],
        status=ApprovalStatus(row["status"]), created_at=row["created_at"], expires_at=row["expires_at"],
        decided_at=row["decided_at"], decided_via=row["decided_via"],
    )


class PostgresApprovalLedger:
    def __init__(self, dsn: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(dsn)

    async def record_held(self, approvals: Sequence[ToolApproval]) -> None:
        if not approvals:
            return
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                _INSERT,
                [(a.id, a.owner_id.value, a.tool, a.call_id, a.digest, a.session_id, a.turn_id,
                  a.created_at, a.expires_at) for a in approvals],
            )

    async def decide(
        self, owner_id: UserId, ids: Sequence[str], status: ApprovalStatus, *, at: datetime, via: str,
    ) -> int:
        unique = list(dict.fromkeys(ids))
        if not unique:
            return 0
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(_DECIDE, owner_id.value, unique, status.value, at, via)
        return int(result.rsplit(" ", 1)[-1])

    async def get(self, owner_id: UserId, approval_id: str) -> ToolApproval | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_GET, owner_id.value, approval_id)
        return _row(row) if row else None

    async def list(
        self,
        owner_id: UserId,
        *,
        now: datetime,
        status: ApprovalStatus | None = None,
        tool: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> Sequence[ToolApproval]:
        clauses, args = ["owner_id = $1"], [owner_id.value]

        def add(clause: str, value: Any) -> None:
            args.append(value)
            clauses.append(clause.replace("?", f"${len(args)}"))

        if status is ApprovalStatus.PENDING:
            add("status = 'pending' AND expires_at > ?", now)
        elif status is ApprovalStatus.EXPIRED:
            add("status = 'pending' AND expires_at <= ?", now)
        elif status is not None:
            add("status = ?", status.value)
        if tool is not None:
            add("tool = ?", tool)
        if since is not None:
            add("created_at >= ?", since)
        if until is not None:
            add("created_at < ?", until)
        if before is not None:
            args.extend(before)
            clauses.append(f"(created_at, id) < (${len(args) - 1}, ${len(args)})")
        args.append(limit)
        sql = (
            f"SELECT {_COLUMNS} FROM aura_tool_approvals WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at DESC, id DESC LIMIT ${len(args)}"
        )
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row(r) for r in rows]

    async def delete_for_owner(self, owner_id: UserId) -> int:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(_DELETE_OWNER, owner_id.value)
        return int(result.rsplit(" ", 1)[-1])
