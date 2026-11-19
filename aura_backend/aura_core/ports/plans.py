"""Ports for plans and usage metering (SaaS P1-C). Domain types only; stdlib only (ADR-001)."""

from __future__ import annotations

from typing import Protocol

from ..domain.plans import Entitlement

__all__ = ["EntitlementStore", "UsageMeter"]


class EntitlementStore(Protocol):
    """Which plan, and how many seats, a workspace has. No row means the default plan."""

    async def get(self, workspace_id: str) -> Entitlement | None: ...

    async def put(self, entitlement: Entitlement) -> None:
        """Insert or replace."""


class UsageMeter(Protocol):
    """Per-workspace counters. ``try_consume`` checks the limit and adds in one atomic step in
    every adapter, so two requests can never both take the last unit."""

    async def try_consume(
        self, workspace_id: str, metric: str, period: str, amount: int, limit: int
    ) -> tuple[bool, int]:
        """Add ``amount`` only if the total stays ``<= limit``. Returns ``(admitted, value)``:
        the new total when admitted, the unchanged current total when not."""

    async def release(self, workspace_id: str, metric: str, period: str, amount: int) -> int:
        """Give ``amount`` back; never below 0. Returns the new total."""

    async def add(self, workspace_id: str, metric: str, period: str, amount: int) -> int:
        """Unconditional (``amount`` may be negative); never below 0. Returns the new total."""

    async def get(self, workspace_id: str, metric: str, period: str) -> int: ...
