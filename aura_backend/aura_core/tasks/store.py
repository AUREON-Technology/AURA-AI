"""Where agent tasks live: the cache port, namespaced by owner (ADR-043).

No table, by decision (Alembic is the only schema authority and a new table needs a real-Postgres
round trip). A task is small, short-lived and its history is a convenience, so the shared cache --
Redis in production, so every gateway worker sees the same record -- is the right size:

* ``aura:task:v1:<owner digest>:<task id>`` -- the record (status, result), TTL refreshed on every
  save, so a task is kept ``record_ttl_s`` after its last change;
* ``aura:task:spec:v1:...`` -- what is needed to (re)run the turn; never returned by any route;
* a per-owner :class:`CacheLog` of task ids, which is what ``GET /agents/tasks`` lists;
* idempotency claims and values (see :meth:`claim_idempotency`).

Every key carries a digest of the owner, and every read takes the owner as an argument and checks
it against the stored record as well, so a task id is never enough to read someone else's task.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping

from ..history.cache_log import CacheLog, owner_digest
from ..observability.logging import get_logger
from .models import AgentTask

__all__ = ["AgentTaskStore"]

logger = get_logger("tasks.store")


class AgentTaskStore:
    def __init__(
        self, cache: Any, *, record_ttl_s: int = 7 * 86_400, idempotency_ttl_s: int = 86_400,
        window: int = 200,
    ) -> None:
        self._cache = cache
        self._ttl = int(record_ttl_s)
        self._idem_ttl = int(idempotency_ttl_s)
        self._index = CacheLog(cache, namespace="aura:task:idx:v1", ttl_s=self._ttl, window=window)

    # -- keys ----------------------------------------------------------------------------

    @staticmethod
    def _record_key(owner: str, task_id: str) -> str:
        return f"aura:task:v1:{owner_digest(owner)}:{task_id}"

    @staticmethod
    def _spec_key(owner: str, task_id: str) -> str:
        return f"aura:task:spec:v1:{owner_digest(owner)}:{task_id}"

    @staticmethod
    def _idem_key(owner: str, scope: str, key: str) -> str:
        digest = hashlib.sha256(f"{scope}\x00{key}".encode("utf-8")).hexdigest()[:32]
        return f"aura:task:idem:v1:{owner_digest(owner)}:{digest}"

    # -- records -------------------------------------------------------------------------

    async def create(self, task: AgentTask, spec: Mapping[str, Any]) -> None:
        await self._write(task)
        await self._cache.set(
            self._spec_key(task.owner, task.id), json.dumps(dict(spec)).encode("utf-8"),
            ttl_s=self._ttl,
        )
        await self._index.append(task.owner, {"id": task.id})

    async def _write(self, task: AgentTask) -> None:
        await self._cache.set(
            self._record_key(task.owner, task.id),
            json.dumps(task.to_record(), separators=(",", ":")).encode("utf-8"),
            ttl_s=self._ttl,
        )

    async def save(self, task: AgentTask) -> None:
        await self._write(task)

    async def get(self, owner: str, task_id: str) -> AgentTask | None:
        blob = await self._cache.get(self._record_key(owner, task_id))
        return self._decode(owner, blob)

    @staticmethod
    def _decode(owner: str, blob: bytes | None) -> AgentTask | None:
        if blob is None:
            return None
        try:
            task = AgentTask.from_record(json.loads(blob))
        except (ValueError, KeyError, AssertionError, TypeError):
            logger.warning("unreadable task record")
            return None
        # The key is already owner-scoped; this makes a wrong-owner record unreadable even if a
        # key were ever built wrongly.
        return task if task.owner == owner else None

    async def spec(self, owner: str, task_id: str) -> dict[str, Any] | None:
        blob = await self._cache.get(self._spec_key(owner, task_id))
        if blob is None:
            return None
        try:
            decoded = json.loads(blob)
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None

    async def set_spec(self, owner: str, task_id: str, spec: Mapping[str, Any]) -> None:
        await self._cache.set(
            self._spec_key(owner, task_id), json.dumps(dict(spec)).encode("utf-8"), ttl_s=self._ttl
        )

    async def list_tasks(
        self, owner: str, *, status: str | None = None, persona: str | None = None,
        since: datetime | None = None, limit: int = 50,
    ) -> list[AgentTask]:
        """The owner's tasks, newest first. ``limit`` bounds the result, not the search: the
        index holds the newest ``window`` tasks and older ones are not listed."""
        entries = await self._index.recent(owner)
        ids = [e["id"] for e in entries if isinstance(e.get("id"), str)]
        keys = {self._record_key(owner, i): i for i in ids}
        found = await self._cache.get_many(list(keys))
        out: list[AgentTask] = []
        for task_id in ids:
            task = self._decode(owner, found.get(self._record_key(owner, task_id)))
            if task is None:
                continue
            if status is not None and task.status != status:
                continue
            if persona is not None and task.persona != persona:
                continue
            if since is not None and task.created_at < since:
                continue
            out.append(task)
            if len(out) >= limit:
                break
        return out

    # -- idempotency ---------------------------------------------------------------------

    async def idempotency_value(self, owner: str, scope: str, key: str) -> dict[str, Any] | None:
        blob = await self._cache.get(self._idem_key(owner, scope, key) + ":val")
        if blob is None:
            return None
        try:
            decoded = json.loads(blob)
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None

    async def claim_idempotency(self, owner: str, scope: str, key: str) -> bool:
        """``True`` for the one caller that first presents this ``(owner, scope, key)`` in the
        TTL window. Atomic: it is an ``incr``, which is atomic across gateway workers on Redis
        (a get-then-set could let two retries both win)."""
        n = await self._cache.incr(self._idem_key(owner, scope, key) + ":claim", ttl_s=self._idem_ttl)
        return int(n) == 1

    async def store_idempotency(
        self, owner: str, scope: str, key: str, value: Mapping[str, Any]
    ) -> None:
        await self._cache.set(
            self._idem_key(owner, scope, key) + ":val", json.dumps(dict(value)).encode("utf-8"),
            ttl_s=self._idem_ttl,
        )

    async def release_idempotency(self, owner: str, scope: str, key: str) -> None:
        """Forget a claim whose request was refused before it created anything, so the same key
        can be sent again once the cause (validation, a full queue) is fixed."""
        base = self._idem_key(owner, scope, key)
        await self._cache.delete(base + ":claim")
        await self._cache.delete(base + ":val")
