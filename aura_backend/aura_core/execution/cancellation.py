"""Cancel requests for live agent runs, shared across gateway workers through the cache.

A run executes inside one gateway worker's request, but ``POST /agents/runs/{id}/cancel`` can land
on any worker, so the request is a cache key (``agent_run_cancel:<owner>:<run>``) every worker can
see, plus a direct nudge when the run happens to be in the same process.

**Cooperative.** Nothing here interrupts a step in flight. The run's budget gate consults
:attr:`RunWatch.cancelled` before every tool dispatch and the loop consults it between passes, so a
cancel takes effect at the next step boundary. A model call or tool already running finishes. That
is the honest guarantee: it stops *further* work, not the work being done this instant.

``RunWatch.cancelled`` is a plain attribute, not a coroutine, because the gate's ``admit`` is
synchronous. A small background task per live run polls the cache and sets it; a poll that fails
leaves it as it was (a cache outage must not cancel, or fail to cancel, a run by accident).

The key is scoped by the owner, so naming another person's run id cancels nothing of theirs; the
API also checks ownership before it asks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

__all__ = ["RunCancellation", "RunWatch"]

logger = logging.getLogger(__name__)

_PREFIX = "agent_run_cancel:"


class RunWatch:
    """Whether one live run has been asked to stop."""

    def __init__(self, registry: "RunCancellation", key: str) -> None:
        self._registry = registry
        self._key = key
        self.cancelled = False
        self._task: asyncio.Task | None = None

    async def close(self) -> None:
        """Stop watching. Idempotent; safe from a ``finally`` or an exception handler."""
        self._registry._forget(self._key, self)
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # a poller's failure is not the run's
                pass


class RunCancellation:
    def __init__(self, cache: Any = None, *, poll_interval_s: float = 1.0, ttl_s: int = 3600) -> None:
        self._cache = cache
        self._poll_interval_s = poll_interval_s
        self._ttl_s = ttl_s
        self._live: dict[str, list[RunWatch]] = {}

    @staticmethod
    def _key(user_id: str, run_id: str) -> str:
        return f"{_PREFIX}{user_id}:{run_id}"

    def is_live(self, user_id: str, run_id: str) -> bool:
        """Whether this process is running that run right now."""
        return bool(self._live.get(self._key(user_id, run_id)))

    async def request(self, user_id: str, run_id: str) -> None:
        """Ask for the run to stop. Visible to every worker; instant in this one."""
        key = self._key(user_id, run_id)
        for watch in self._live.get(key, ()):
            watch.cancelled = True
        if self._cache is not None:
            await self._cache.set(key, b"1", ttl_s=self._ttl_s)

    def watch(self, user_id: str, run_id: str) -> RunWatch:
        """Begin watching a run this process is about to execute."""
        key = self._key(user_id, run_id)
        watch = RunWatch(self, key)
        self._live.setdefault(key, []).append(watch)
        if self._cache is not None:
            watch._task = asyncio.get_running_loop().create_task(self._poll(key, watch))
        return watch

    def _forget(self, key: str, watch: RunWatch) -> None:
        watches = self._live.get(key)
        if watches is None:
            return
        try:
            watches.remove(watch)
        except ValueError:
            pass
        if not watches:
            self._live.pop(key, None)

    async def _poll(self, key: str, watch: RunWatch) -> None:
        # Check at once (a cancel may have been requested before the run began) and then on a beat.
        while not watch.cancelled:
            try:
                if await self._cache.get(key):
                    watch.cancelled = True
                    return
            except Exception:  # pragma: no cover - a cache blip changes nothing
                logger.debug("cancel poll failed", exc_info=True)
            await asyncio.sleep(self._poll_interval_s)
