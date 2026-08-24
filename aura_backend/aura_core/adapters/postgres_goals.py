"""Postgres implementation of the ``GoalsPort`` (Phase D2).

Reads the ``goals`` table Alembic migration ``0004_projects_and_goals`` creates
(``infrastructure/db/models.py``'s ``Goal``) -- that table is owned by the legacy sync layer, not
by this adapter, so unlike the other ``postgres_*.py`` adapters this one never issues
``CREATE TABLE``. It selects only the plaintext columns: ``title_enc``/``description_enc`` never
leave Postgres through this port.
"""

from __future__ import annotations

import logging
from typing import Sequence

from ..domain.errors import DependencyUnavailableError
from ..domain.goals import GoalSnapshot
from ..domain.identifiers import UserId
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresGoalsPort"]


def _unavailable(message: str, **details) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


class PostgresGoalsPort:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def active_summaries(
        self, owner_id: UserId, *, limit: int = 5
    ) -> Sequence[GoalSnapshot]:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                # `UserId` is the auth-subject claim (see domain/identifiers.py); `goals.user_id`
                # is a UUID FK to the legacy `users.id` primary key, so the two must be joined
                # through `users.auth_subject` rather than compared directly.
                rows = await conn.fetch(
                    """
                    SELECT g.id, g.category, g.status, g.priority, g.target_date, g.progress_pct
                    FROM goals g
                    JOIN users u ON u.id = g.user_id
                    WHERE u.auth_subject = $1 AND g.deleted_at IS NULL AND g.status = 'active'
                    ORDER BY
                        CASE g.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                        g.target_date ASC NULLS LAST
                    LIMIT $2
                    """,
                    owner_id.value,
                    limit,
                )
        except Exception as exc:
            logger.warning("goal summary read failed; continuing without goals: %s", exc)
            return []
        return [
            GoalSnapshot(
                id=str(row["id"]),
                category=row["category"],
                status=row["status"],
                priority=row["priority"],
                target_date=row["target_date"],
                progress_pct=row["progress_pct"],
            )
            for row in rows
        ]
