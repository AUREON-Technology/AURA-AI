"""Postgres adapter for the approval store (ADR-008 Loop B).

Approvals must survive a gateway restart — an in-memory store loses the
audit trail that makes the human gate structural rather than decorative.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError
from ..learning.approval import ApprovalKind, ApprovalRequest, ApprovalStatus
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresApprovalStore"]


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostgresApprovalStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def put(self, request: ApprovalRequest) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_approvals (
                        id, kind, summary, artefact_digest, payload, status,
                        requested_at, expires_at, decided_by, decided_at, note
                    ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (id) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        summary = EXCLUDED.summary,
                        artefact_digest = EXCLUDED.artefact_digest,
                        payload = EXCLUDED.payload,
                        status = EXCLUDED.status,
                        requested_at = EXCLUDED.requested_at,
                        expires_at = EXCLUDED.expires_at,
                        decided_by = EXCLUDED.decided_by,
                        decided_at = EXCLUDED.decided_at,
                        note = EXCLUDED.note
                    """,
                    request.id,
                    str(request.kind),
                    request.summary,
                    request.artefact_digest,
                    json.dumps(dict(request.payload)),
                    str(request.status),
                    request.requested_at,
                    request.expires_at,
                    request.decided_by,
                    request.decided_at,
                    request.note,
                )
        except Exception as exc:
            raise _unavailable(
                f"could not write approval {request.id!r}: {exc}", approval=request.id
            ) from exc

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, kind, summary, artefact_digest, payload, status,
                           requested_at, expires_at, decided_by, decided_at, note
                    FROM aura_approvals WHERE id = $1
                    """,
                    approval_id,
                )
        except Exception as exc:
            raise _unavailable(f"could not read approval: {exc}") from exc
        return self._row(row) if row else None

    async def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        kind: ApprovalKind | None = None,
        limit: int = 50,
    ) -> Sequence[ApprovalRequest]:
        pool = await self._pool._get_pool()
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            params.append(str(status))
            clauses.append(f"status = ${len(params)}")
        if kind is not None:
            params.append(str(kind))
            clauses.append(f"kind = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT id, kind, summary, artefact_digest, payload, status,
                           requested_at, expires_at, decided_by, decided_at, note
                    FROM aura_approvals
                    {where}
                    ORDER BY requested_at DESC
                    LIMIT ${len(params)}
                    """,
                    *params,
                )
        except Exception as exc:
            raise _unavailable(f"could not list approvals: {exc}") from exc
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: Mapping[str, Any]) -> ApprovalRequest:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return ApprovalRequest(
            id=row["id"],
            kind=ApprovalKind(row["kind"]),
            summary=row["summary"],
            artefact_digest=row["artefact_digest"],
            payload=dict(payload or {}),
            status=ApprovalStatus(row["status"]),
            requested_at=_utc(row["requested_at"]) or datetime.now(timezone.utc),
            expires_at=_utc(row["expires_at"]),
            decided_by=row["decided_by"],
            decided_at=_utc(row["decided_at"]),
            note=row["note"] or "",
        )
