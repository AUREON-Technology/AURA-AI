"""Postgres implementation of the ``InsightSink`` port (Phase E2).

Writes into the legacy ``insights`` table (``infrastructure/db/models.py``'s ``Insight``) the
same way ``postgres_goals.py`` reads ``goals``: raw ``asyncpg`` SQL against a table owned by that
module's Alembic migration, never a ``CREATE TABLE`` of its own, and joined through
``users.auth_subject`` because :class:`~aura_core.domain.identifiers.UserId` is the auth-subject
claim, not the table's internal UUID primary key. ``insights.payload_json`` already stores a JSON
*string* (``services/insights.py`` writes ``json.dumps(...)``) rather than a JSONB column, so this
matches that exactly -- a trigger-generated insight must render identically to a rule-generated
one on the client's Today screen, which reads this table without caring which process wrote a row.
"""

from __future__ import annotations

import json
import logging
import uuid

from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import UserId
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresInsightSink"]


def _unavailable(message: str, **details) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


class PostgresInsightSink:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def surface(
        self, owner_id: UserId, *, insight_id: str, kind: str, message: str, explanation: str,
    ) -> None:
        pool = await self._pool._get_pool()
        payload = json.dumps({"message": message, "explanation": explanation})
        try:
            async with pool.acquire() as conn:
                # SELECT ... FROM users silently inserts zero rows for an unknown auth_subject
                # rather than raising -- a stale/deleted user must not fail the whole drain batch.
                await conn.execute(
                    """
                    INSERT INTO insights (id, user_id, kind, payload_json, created_at)
                    SELECT $1, u.id, $3, $4, now()
                    FROM users u WHERE u.auth_subject = $2
                    ON CONFLICT (id) DO NOTHING
                    """,
                    uuid.UUID(insight_id), owner_id.value, kind, payload,
                )
        except Exception as exc:
            raise _unavailable(f"could not surface insight {insight_id}: {exc}") from exc
