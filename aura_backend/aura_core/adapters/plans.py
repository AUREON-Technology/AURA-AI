"""In-memory plan stores (SaaS P1-C): the substrate for tests and a single-process local run;
production uses ``postgres_plans.py``. One lock makes ``try_consume`` atomic here."""

from __future__ import annotations

import asyncio

from ..domain.plans import Entitlement

__all__ = ["InMemoryEntitlementStore", "InMemoryUsageMeter"]


class InMemoryEntitlementStore:
    def __init__(self) -> None:
        self._rows: dict[str, Entitlement] = {}

    async def get(self, workspace_id: str) -> Entitlement | None:
        return self._rows.get(workspace_id)

    async def put(self, entitlement: Entitlement) -> None:
        self._rows[entitlement.workspace_id] = entitlement


class InMemoryUsageMeter:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], int] = {}
        self._lock = asyncio.Lock()

    async def try_consume(self, workspace_id: str, metric: str, period: str, amount: int, limit: int) -> tuple[bool, int]:
        key = (workspace_id, metric, period)
        async with self._lock:
            current = self._values.get(key, 0)
            if current + amount > limit:
                return False, current
            self._values[key] = current + amount
            return True, current + amount

    async def release(self, workspace_id: str, metric: str, period: str, amount: int) -> int:
        return await self.add(workspace_id, metric, period, -abs(amount))

    async def add(self, workspace_id: str, metric: str, period: str, amount: int) -> int:
        key = (workspace_id, metric, period)
        async with self._lock:
            value = max(self._values.get(key, 0) + amount, 0)
            self._values[key] = value
            return value

    async def get(self, workspace_id: str, metric: str, period: str) -> int:
        return self._values.get((workspace_id, metric, period), 0)
