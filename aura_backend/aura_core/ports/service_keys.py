"""Port for issued service keys (AR-4). Domain types only; stdlib only (ADR-001)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain.service_keys import ServiceKey

__all__ = ["ServiceKeyStore"]


class ServiceKeyStore(Protocol):
    """Durable record of issued keys.

    ``get`` is read on **every** request a service key makes, with no cache in front of it, so a
    revocation takes effect on the very next call. ``revoke`` is idempotent and returns the key
    as stored afterwards (None if no such key).
    """

    async def put(self, key: ServiceKey) -> None: ...

    async def get(self, key_id: str) -> ServiceKey | None: ...

    async def list(self) -> list[ServiceKey]: ...

    async def revoke(self, key_id: str, *, at: datetime, by: str) -> ServiceKey | None: ...
