"""In-memory :class:`~aura_core.ports.service_keys.ServiceKeyStore` (AR-4).

The real substrate for tests and a single-process local run; production uses
``postgres_service_keys.py`` so a revocation reaches every gateway worker.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

from ..domain.errors import ConflictError
from ..domain.service_keys import ServiceKey

__all__ = ["InMemoryServiceKeyStore"]


class InMemoryServiceKeyStore:
    def __init__(self) -> None:
        self._keys: dict[str, ServiceKey] = {}
        self._lock = asyncio.Lock()

    async def put(self, key: ServiceKey) -> None:
        async with self._lock:
            if key.key_id in self._keys:
                raise ConflictError("a service key with that id already exists")
            self._keys[key.key_id] = key

    async def get(self, key_id: str) -> ServiceKey | None:
        return self._keys.get(key_id)

    async def list(self) -> list[ServiceKey]:
        return sorted(self._keys.values(), key=lambda k: (k.created_at, k.key_id))

    async def revoke(self, key_id: str, *, at: datetime, by: str) -> ServiceKey | None:
        async with self._lock:
            key = self._keys.get(key_id)
            if key is None or key.revoked:
                return key
            revoked = replace(key, revoked_at=at, revoked_by=by)
            self._keys[key_id] = revoked
            return revoked
