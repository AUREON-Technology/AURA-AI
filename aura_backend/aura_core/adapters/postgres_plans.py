"""Postgres plan stores (SaaS P1-C). Tables owned by Alembic revision 0024; no DDL here (CB-20).

``try_consume`` is a single ``INSERT ... ON CONFLICT DO UPDATE ... WHERE`` statement: the limit check
and the increment happen under the row lock Postgres takes for the upsert, so concurrent requests
on every gateway worker can never both take the last unit.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any

from ..domain.plans import Entitlement
from .postgres_store import PostgresPool

__all__ = ["PostgresEntitlementStore", "PostgresUsageMeter"]


class PostgresEntitlementStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def get(self, workspace_id: str) -> Entitlement | None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT workspace_id, plan_id, seats, source, updated_at, updated_by "
                "FROM aura_workspace_entitlements WHERE workspace_id = $1",
                workspace_id,
            )
        if row is None:
            return None
        at = row["updated_at"]
        return Entitlement(
            workspace_id=row["workspace_id"], plan_id=row["plan_id"], seats=int(row["seats"]),
            source=row["source"], updated_at=at if at.tzinfo else at.replace(tzinfo=timezone.utc),
            updated_by=row["updated_by"],
        )

    async def put(self, e: Entitlement) -> None:
        pool = await self._pool._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO aura_workspace_entitlements (workspace_id, plan_id, seats, source, updated_at, updated_by) "
                "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (workspace_id) DO UPDATE SET "
                "plan_id = EXCLUDED.plan_id, seats = EXCLUDED.seats, source = EXCLUDED.source, "
                "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by",
                e.workspace_id, e.plan_id, e.seats, e.source, e.updated_at, e.updated_by,
            )


class PostgresUsageMeter:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def _conn(self) -> Any:
        return await self._pool._get_pool()

    async def try_consume(self, workspace_id: str, metric: str, period: str, amount: int, limit: int) -> tuple[bool, int]:
        pool = await self._conn()
        async with pool.acquire() as conn:
            admitted = await conn.fetchval(
                "INSERT INTO aura_usage_counters AS c (workspace_id, metric, period, value) "
                "SELECT $1::text, $2::text, $3::text, $4::bigint WHERE $4::bigint <= $5::bigint "
                "ON CONFLICT (workspace_id, metric, period) DO UPDATE SET value = c.value + EXCLUDED.value "
                "WHERE c.value + EXCLUDED.value <= $5::bigint RETURNING value",
                workspace_id, metric, period, amount, limit,
            )
            if admitted is not None:
                return True, int(admitted)
            current = await conn.fetchval(
                "SELECT value FROM aura_usage_counters WHERE workspace_id = $1 AND metric = $2 AND period = $3",
                workspace_id, metric, period,
            )
        return False, int(current or 0)

    async def release(self, workspace_id: str, metric: str, period: str, amount: int) -> int:
        pool = await self._conn()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "UPDATE aura_usage_counters SET value = GREATEST(value - $4::bigint, 0) "
                "WHERE workspace_id = $1 AND metric = $2 AND period = $3 RETURNING value",
                workspace_id, metric, period, abs(amount),
            )
        return int(value or 0)

    async def add(self, workspace_id: str, metric: str, period: str, amount: int) -> int:
        pool = await self._conn()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "INSERT INTO aura_usage_counters AS c (workspace_id, metric, period, value) "
                "VALUES ($1, $2, $3, GREATEST($4::bigint, 0)) "
                "ON CONFLICT (workspace_id, metric, period) DO UPDATE SET value = GREATEST(c.value + $4::bigint, 0) "
                "RETURNING value",
                workspace_id, metric, period, amount,
            )
        return int(value)

    async def get(self, workspace_id: str, metric: str, period: str) -> int:
        pool = await self._conn()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM aura_usage_counters WHERE workspace_id = $1 AND metric = $2 AND period = $3",
                workspace_id, metric, period,
            )
        return int(value or 0)
