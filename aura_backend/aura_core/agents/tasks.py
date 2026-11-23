"""Background agent tasks (ADR-038, inventory row 57).

``delegate_to_agent`` is a synchronous one-hop sub-turn: the orchestrator waits for the answer
inside its own plan step, so a long sub-task (a research pass, a code review) holds the whole turn
and the user's attention. ``delegate_to_agent(..., background=true)`` hands the same sub-turn to an
:class:`AgentTaskManager` instead and returns an ``agent_task_id`` at once; the orchestrator
carries on, and later reads the answer with ``get_agent_task``.

What this module owns, and what it does not:

* **It owns the lifecycle, not the rules.** It is handed an async ``runner`` that performs the
  sub-turn. Every grant, depth, capability and taint rule of the synchronous path is applied by the
  caller *before* :meth:`AgentTaskManager.submit` -- the manager cannot widen anything because it
  never sees a grant. (``tools/delegation.py`` builds the child ``TurnRequest`` once and either
  awaits it or submits it.)
* **The running task lives in the process that started it.** It is an ``asyncio`` task in that
  worker's event loop. What is shared is the *record* -- status, result, error, timestamps -- in the
  cache port (Redis in production), so any worker can answer ``get_agent_task`` / ``list`` and can
  ask for a cancellation; the owning worker's watcher notices the flag within ``poll_s`` and
  cancels. A worker that dies mid-task takes the task with it: its record is reported ``failed``
  (``lost``) once its deadline plus a grace period has passed, never left "running" forever. Nothing
  here survives a restart, by design -- a durable queue is a different system.
* **Bounds.** ``per_user`` active tasks per owner (counted from the shared records, so it holds
  across workers up to a small race), ``global_cap`` tasks in this process, ``deadline_s`` wall-clock
  per task, ``ttl_s`` for how long a record is kept (every write refreshes it, so a finished task is
  readable for ``ttl_s`` after it finished and then disappears).
* **Owner-scoped.** Records and the per-owner index are keyed by the owner's id; another user's task
  id reads as "not found" for get, cancel and list alike.

Stdlib and the ``Cache`` port only (ADR-001).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping

from ..observability.logging import get_logger

__all__ = ["AgentTask", "AgentTaskManager", "AgentTaskLimitError", "TaskResult"]

logger = get_logger("agents.tasks")

ACTIVE = ("queued", "running")
FINAL = ("succeeded", "failed", "cancelled")
MAX_RESULT_CHARS = 8_000
MAX_SUBTASK_CHARS = 500
#: A task is reported lost this long after its deadline passed without a final record.
GRACE_S = 60.0


class AgentTaskLimitError(Exception):
    """A submit refused for a cap. ``scope`` is ``"user"`` or ``"global"``."""

    def __init__(self, message: str, *, scope: str) -> None:
        super().__init__(message)
        self.scope = scope


@dataclass(frozen=True, slots=True)
class TaskResult:
    """What a runner reports. ``ok`` false means the sub-turn did not produce an answer."""

    ok: bool
    text: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTask:
    id: str
    owner_id: str
    agent_id: str
    subtask: str
    status: str
    created_at: float
    deadline_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: str | None = None
    error: str | None = None
    handoff_id: str | None = None
    cancel_requested: bool = False
    #: ``host:pid`` of the process whose event loop runs it.
    worker: str = ""

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "owner_id": self.owner_id, "agent_id": self.agent_id,
            "subtask": self.subtask, "status": self.status, "created_at": self.created_at,
            "deadline_at": self.deadline_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "result": self.result, "error": self.error,
            "handoff_id": self.handoff_id, "cancel_requested": self.cancel_requested,
            "worker": self.worker,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentTask":
        return cls(
            id=str(data["id"]), owner_id=str(data["owner_id"]), agent_id=str(data.get("agent_id", "")),
            subtask=str(data.get("subtask", "")), status=str(data.get("status", "failed")),
            created_at=float(data.get("created_at", 0.0)), deadline_at=float(data.get("deadline_at", 0.0)),
            started_at=data.get("started_at"), finished_at=data.get("finished_at"),
            result=data.get("result"), error=data.get("error"), handoff_id=data.get("handoff_id"),
            cancel_requested=bool(data.get("cancel_requested", False)), worker=str(data.get("worker", "")),
        )


class AgentTaskManager:
    """Starts, tracks, cancels and expires background sub-turns."""

    _RECORD = "agent-task:v1:u{n}:{owner}:{id}"
    _INDEX = "agent-task-index:v1:u{n}:{owner}"
    _MAX_INDEX = 100

    def __init__(
        self,
        cache: Any,
        *,
        per_user: int = 3,
        global_cap: int = 16,
        ttl_s: int = 3600,
        deadline_s: float = 900.0,
        poll_s: float = 2.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cache = cache
        self._per_user = max(1, per_user)
        self._global_cap = max(1, global_cap)
        self._ttl = max(60, int(ttl_s))
        self._deadline_s = max(1.0, float(deadline_s))
        self._poll_s = max(0.01, float(poll_s))
        self._clock = clock
        self._worker = f"{socket.gethostname()}:{os.getpid()}"
        self._live: dict[str, asyncio.Task[None]] = {}
        self._meta: dict[str, AgentTask] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()  # the index's read-modify-write, within this process

    # -- keys and records -----------------------------------------------------------------

    def _key(self, owner: str, task_id: str) -> str:
        return self._RECORD.format(n=len(owner), owner=owner, id=task_id)

    def _index_key(self, owner: str) -> str:
        return self._INDEX.format(n=len(owner), owner=owner)

    async def _load(self, owner: str, task_id: str) -> AgentTask | None:
        raw = await self._cache.get(self._key(owner, task_id))
        if raw is None:
            return None
        try:
            task = AgentTask.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            return None
        return task if task.owner_id == owner else None

    async def _store(self, task: AgentTask) -> None:
        # Active records outlive their own deadline by the grace period plus the ttl, so a lost
        # task can still be reported as such rather than vanishing while the user is asking.
        ttl = self._ttl + (int(self._deadline_s + GRACE_S) if task.active else 0)
        await self._cache.set(
            self._key(task.owner_id, task.id),
            json.dumps(task.as_dict(), ensure_ascii=False).encode("utf-8"), ttl_s=ttl,
        )

    async def _index_add(self, owner: str, task_id: str) -> None:
        async with self._lock:
            ids = await self._index_read(owner)
            ids = [task_id, *[i for i in ids if i != task_id]][: self._MAX_INDEX]
            await self._cache.set(
                self._index_key(owner), json.dumps(ids).encode("utf-8"),
                ttl_s=self._ttl + int(self._deadline_s + GRACE_S),
            )

    async def _index_read(self, owner: str) -> list[str]:
        raw = await self._cache.get(self._index_key(owner))
        if raw is None:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return [i for i in data if isinstance(i, str)] if isinstance(data, list) else []

    def _reconcile(self, task: AgentTask) -> AgentTask:
        """A task nobody is running any more is not "running": a record still active well past its
        deadline, and not live in this process, belongs to a worker that died."""
        if (
            task.active and task.id not in self._live
            and self._clock() > task.deadline_at + GRACE_S
        ):
            return replace(task, status="failed", error="lost: the worker running this task stopped",
                           finished_at=task.deadline_at)
        return task

    # -- public API -----------------------------------------------------------------------

    async def submit(
        self,
        *,
        owner_id: str,
        agent_id: str,
        subtask: str,
        runner: Callable[[], Awaitable[TaskResult]],
        handoff_id: str | None = None,
    ) -> AgentTask:
        """Start ``runner`` in the background and return its record (status ``running``).

        Raises :class:`AgentTaskLimitError` when the owner already has ``per_user`` active tasks
        or this process already runs ``global_cap``.
        """
        if len(self._live) >= self._global_cap:
            raise AgentTaskLimitError(
                f"this server is already running {self._global_cap} background agent tasks; try again shortly",
                scope="global",
            )
        active = [t for t in await self.list(owner_id, limit=self._MAX_INDEX) if t.active]
        if len(active) >= self._per_user:
            raise AgentTaskLimitError(
                f"you already have {len(active)} background agent tasks running (limit {self._per_user}); "
                "wait for one to finish or cancel one",
                scope="user",
            )
        now = self._clock()
        task = AgentTask(
            id=f"at_{uuid.uuid4().hex[:20]}", owner_id=owner_id, agent_id=agent_id,
            subtask=" ".join(subtask.split())[:MAX_SUBTASK_CHARS], status="running",
            created_at=now, started_at=now, deadline_at=now + self._deadline_s,
            handoff_id=handoff_id, worker=self._worker,
        )
        await self._store(task)
        await self._index_add(owner_id, task.id)
        # A fresh, empty context: the parent turn's per-turn state (its generation deadline, its
        # token budget, its data-classification scope) must not be inherited by work that outlives
        # that turn. ``AIKernel.run`` establishes its own.
        loop = asyncio.get_running_loop()
        runner_task = loop.create_task(self._run(task, runner), context=contextvars.Context())
        self._live[task.id] = runner_task
        self._meta[task.id] = task

        def forget(_t: Any, tid: str = task.id) -> None:
            self._live.pop(tid, None)
            self._meta.pop(tid, None)

        runner_task.add_done_callback(forget)
        self._watchers[task.id] = loop.create_task(
            self._watch(task, runner_task), context=contextvars.Context()
        )
        return task

    async def get(self, owner_id: str, task_id: str) -> AgentTask | None:
        task = await self._load(owner_id, task_id)
        return self._reconcile(task) if task is not None else None

    async def list(self, owner_id: str, *, limit: int = 20) -> list[AgentTask]:
        """The owner's tasks, newest first. Prunes index entries whose record has expired."""
        tasks: list[AgentTask] = []
        live_ids: list[str] = []
        for task_id in await self._index_read(owner_id):
            task = await self._load(owner_id, task_id)
            if task is None:
                continue
            live_ids.append(task_id)
            tasks.append(self._reconcile(task))
        if len(live_ids) != len(await self._index_read(owner_id)):
            async with self._lock:
                await self._cache.set(
                    self._index_key(owner_id), json.dumps(live_ids).encode("utf-8"),
                    ttl_s=self._ttl + int(self._deadline_s + GRACE_S),
                )
        return tasks[:limit]

    async def cancel(self, owner_id: str, task_id: str) -> AgentTask | None:
        """Cancel one of the owner's tasks. ``None`` when it is not theirs (or expired).

        A task running in this process is cancelled directly. One running in another worker gets
        ``cancel_requested`` on its shared record, which that worker's watcher acts on within
        ``poll_s``. A finished task is returned unchanged.
        """
        task = await self.get(owner_id, task_id)
        if task is None:
            return None
        if not task.active:
            return task
        local = self._live.get(task_id)
        if local is not None:
            local.cancel()
            await asyncio.gather(local, return_exceptions=True)
            await self._settle_cancelled(task)
            return await self.get(owner_id, task_id)
        if task.status == "failed":  # reconciled as lost: nothing is running, nothing to cancel
            await self._store(task)
            return task
        flagged = replace(task, cancel_requested=True)
        await self._store(flagged)
        return flagged

    @property
    def running_here(self) -> int:
        return len(self._live)

    async def shutdown(self) -> None:
        """Cancel every task this process runs and wait for their final records. Never raises."""
        meta = dict(self._meta)
        for task in list(self._live.values()):
            task.cancel()
        await asyncio.gather(*self._live.values(), return_exceptions=True)
        for record in meta.values():
            await self._settle_cancelled(record)
        for watcher in list(self._watchers.values()):
            watcher.cancel()
        await asyncio.gather(*self._watchers.values(), return_exceptions=True)
        self._watchers.clear()

    async def drain(self) -> None:
        """Wait for every task this process runs to finish (tests)."""
        while self._live:
            await asyncio.gather(*list(self._live.values()), return_exceptions=True)

    # -- the task itself ------------------------------------------------------------------

    async def _run(self, task: AgentTask, runner: Callable[[], Awaitable[TaskResult]]) -> None:
        final: AgentTask
        try:
            result = await asyncio.wait_for(runner(), timeout=self._deadline_s)
            if result.ok:
                final = replace(task, status="succeeded", result=(result.text or "")[:MAX_RESULT_CHARS])
            else:
                final = replace(task, status="failed", error=(result.error or "the sub-turn failed")[:1000])
        except asyncio.CancelledError:
            final = replace(task, status="cancelled", error="cancelled")
            await self._finish(final)
            raise
        except asyncio.TimeoutError:
            final = replace(task, status="failed", error=f"deadline exceeded ({int(self._deadline_s)}s)")
        except Exception as exc:
            # The message of a domain error is written for the user; the type name is what is safe
            # for anything else.
            message = getattr(exc, "message", None) or type(exc).__name__
            logger.warning("background agent task failed", extra={"task": task.id, "agent": task.agent_id},
                           exc_info=True)
            final = replace(task, status="failed", error=str(message)[:1000])
        await self._finish(final)

    async def _settle_cancelled(self, task: AgentTask) -> None:
        """A task cancelled before its first step never ran ``_run``'s handler, so nothing wrote
        its final record; write it here (a no-op when one already exists)."""
        await self._finish(replace(task, status="cancelled", error="cancelled"))

    async def _finish(self, final: AgentTask) -> None:
        """Write the final record unless one was already written (a cancel that raced a result)."""
        try:
            current = await self._load(final.owner_id, final.id)
            if current is not None and not current.active:
                return
            await self._store(replace(final, finished_at=self._clock(), cancel_requested=False))
        except Exception:  # pragma: no cover - the cache port swallows its own errors
            logger.warning("could not write a background task's final record", exc_info=True)

    async def _watch(self, task: AgentTask, runner_task: "asyncio.Task[None]") -> None:
        """Poll the shared record for a cancel asked from another worker."""
        try:
            while not runner_task.done():
                await asyncio.sleep(self._poll_s)
                current = await self._load(task.owner_id, task.id)
                if current is not None and current.active and current.cancel_requested:
                    runner_task.cancel()
                    break
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.debug("agent task watcher failed", exc_info=True)
        finally:
            self._watchers.pop(task.id, None)
