"""The agent task queue: priorities, concurrency caps, scheduled starts, fair ordering (ADR-041).

``AIKernel`` is the only execution path and stays so: the queue never runs anything itself. It
decides *when* a user's task may start and hands it to a ``runner`` (``KernelTaskRunner``), which
builds a kernel turn from the request the owner submitted. This is the layer ``llm/admission.py``
is not: admission queues individual *generations* inside the router; this queues *tasks* -- whole
agent turns that may make many generations -- so a person's backlog cannot hold every slot.

Ordering, for the slots that are free right now:

1. **Priority** (``high`` < ``normal`` < ``low``), with *aging*: a waiting task gains one level per
   ``aging_s`` seconds, so a ``low`` task is not starved by a steady stream of ``normal`` ones.
2. **Fairness**: among equals, the user running the fewest tasks goes first, so one person's twenty
   queued tasks do not push another's single one behind all of them.
3. **Arrival order**.

Two caps apply to what *runs*: ``max_concurrent`` overall and ``max_concurrent_per_user``. Two
bound what *waits*: ``max_queued`` and ``max_queued_per_user``; past either, :meth:`submit` refuses
with :class:`TaskQueueFullError` (429 + ``Retry-After``) rather than accepting work it will not get
to. A task may carry ``run_at``; it is invisible to the picker until then.

All of this is per process (see ``AgentTaskSettings``). A task's *record* is shared through the
store, so any worker can show its status; only the owning worker can run or cancel it directly, and
a cancel that reaches another worker is picked up from the record by the owner's heartbeat loop.

Lifecycle: :meth:`start` is called when the app starts and :meth:`stop` when it shuts down; ``stop``
cancels running work and marks every task it still holds, so none is left reading ``running`` for
a process that is gone. A *crash* cannot do that, which is what the heartbeat is for: a queued or
running task whose heartbeat has gone silent is reported ``failed`` (``interrupted``).
"""

from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping

from ..domain.errors import RateLimitedError
from ..observability.logging import get_logger
from .models import PRIORITIES, AgentTask, TaskOutcome, TaskStatus

__all__ = ["AgentTaskQueue", "TaskQueueFullError", "is_stale"]

logger = get_logger("tasks.queue")

Runner = Callable[[AgentTask, Mapping[str, Any]], Awaitable[TaskOutcome]]


class TaskQueueFullError(RateLimitedError):
    """Nothing was accepted: the queue is at a bound. 429 with ``Retry-After``; the same request
    may be sent again later (its idempotency key, if any, is released)."""

    kind = "task_queue_full"


@dataclass(eq=False)
class _Entry:
    task_id: str
    owner: str
    rank: int
    seq: int
    eligible_at: datetime
    reservation: Any = None
    release: Callable[[Any], Awaitable[None]] | None = None
    ready: bool = False
    runner_task: "asyncio.Task[Any] | None" = None
    cancel_reason: str | None = None
    #: Serialises this task's record writes within the process: a heartbeat that read the record
    #: just before the final state was saved must not write the old state back over it.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finished: bool = False


def is_stale(task: AgentTask, *, now: datetime, stale_after_s: float) -> bool:
    """A queued or running task whose process stopped vouching for it."""
    if task.status not in TaskStatus.LIVE:
        return False
    beat = task.heartbeat_at or task.updated_at
    return (now - beat).total_seconds() > stale_after_s


class AgentTaskQueue:
    def __init__(self, *, store: Any, runner: Runner, settings: Any, clock: Any = None) -> None:
        self._store = store
        self._runner = runner
        self._s = settings
        self._clock = clock
        self._pending: dict[str, _Entry] = {}
        self._running: dict[str, _Entry] = {}
        self._running_by_user: dict[str, int] = defaultdict(int)
        self._seq = itertools.count(1)
        self._wake = asyncio.Event()
        self._loop_task: "asyncio.Task[None] | None" = None
        self._beat_task: "asyncio.Task[None] | None" = None
        self._stopping = False

    # -- time ------------------------------------------------------------------------------

    def _now(self) -> datetime:
        if self._clock is not None and hasattr(self._clock, "now"):
            return self._clock.now()
        return datetime.now(timezone.utc)

    # -- lifecycle -------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> None:
        """Idempotent. Safe to call again after :meth:`stop` (a test client re-entering its
        lifespan)."""
        if self.running:
            return
        self._stopping = False
        self._wake = asyncio.Event()
        loop = asyncio.get_running_loop()
        self._loop_task = loop.create_task(self._run_loop(), name="agent-task-queue")
        self._beat_task = loop.create_task(self._heartbeat_loop(), name="agent-task-heartbeat")
        logger.info("agent task queue started", extra={
            "max_concurrent": self._s.max_concurrent, "max_queued": self._s.max_queued,
        })

    async def stop(self) -> None:
        """Cancel running work, mark every task still held, and stop the loops. Never raises."""
        if self._loop_task is None and not self._pending and not self._running:
            return
        self._stopping = True
        for task in (self._loop_task, self._beat_task):
            if task is not None:
                task.cancel()
        for task in (self._loop_task, self._beat_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._loop_task = self._beat_task = None

        running = list(self._running.values())
        for entry in running:
            entry.cancel_reason = "shutdown"
            if entry.runner_task is not None:
                entry.runner_task.cancel()
        runners = [e.runner_task for e in running if e.runner_task is not None]
        if runners:
            # Inside the container's 5 s per-resource shutdown bound.
            await asyncio.wait(runners, timeout=3.5)

        for entry in list(self._pending.values()):
            self._pending.pop(entry.task_id, None)
            await self._mark_unfinished(entry, "server_shutdown",
                                        "the server shut down before this task started")
        logger.info("agent task queue stopped")

    async def _mark_unfinished(self, entry: _Entry, kind: str, message: str) -> None:
        try:
            task = await self._store.get(entry.owner, entry.task_id)
            if task is not None and not task.is_terminal:
                now = self._now()
                await self._store.save(task.with_(
                    status=TaskStatus.FAILED, finished_at=now, updated_at=now,
                    error={"kind": kind, "message": message, "retryable": True, "details": {}},
                ))
        except Exception:
            logger.warning("could not mark a task on shutdown", exc_info=True)
        await self._give_back(entry)

    # -- submission ------------------------------------------------------------------------

    def _retry_after(self) -> int:
        # A rough, honest hint: roughly one task-slot's worth of waiting, bounded.
        return max(5, min(300, int(self._s.task_timeout_s // max(1, self._s.max_concurrent) // 10)))

    async def submit(
        self, task: AgentTask, spec: Mapping[str, Any], *, reservation: Any = None,
        release: Callable[[Any], Awaitable[None]] | None = None,
    ) -> AgentTask:
        """Accept ``task`` (status ``queued``) or refuse it. Creates its record and spec."""
        await self.start()
        self._admit(task.owner)
        entry = self._enter(task, reservation, release)
        try:
            await self._store.create(task, spec)
        except BaseException:
            self._pending.pop(task.id, None)
            raise
        entry.ready = True
        self._wake.set()
        return task

    async def requeue(
        self, task: AgentTask, spec: Mapping[str, Any], *, reservation: Any = None,
        release: Callable[[Any], Awaitable[None]] | None = None,
    ) -> AgentTask:
        """Queue an existing task again (an approval resumes it). Same bounds as :meth:`submit`."""
        await self.start()
        self._admit(task.owner)
        entry = self._enter(task, reservation, release)
        try:
            await self._store.set_spec(task.owner, task.id, spec)
            await self._store.save(task)
        except BaseException:
            self._pending.pop(task.id, None)
            raise
        entry.ready = True
        self._wake.set()
        return task

    def _admit(self, owner: str) -> None:
        waiting = len(self._pending)
        mine = sum(1 for e in self._pending.values() if e.owner == owner)
        if waiting >= self._s.max_queued:
            raise TaskQueueFullError(
                "the task queue is full; try again shortly", retry_after=self._retry_after(),
                details={"scope": "global", "queued": waiting, "limit": self._s.max_queued},
            )
        if mine >= self._s.max_queued_per_user:
            raise TaskQueueFullError(
                "you have too many tasks waiting; wait for some to start or cancel some",
                retry_after=self._retry_after(),
                details={"scope": "user", "queued": mine, "limit": self._s.max_queued_per_user},
            )

    def _enter(self, task: AgentTask, reservation: Any, release: Any) -> _Entry:
        eligible = max(task.created_at, task.run_at) if task.run_at else task.created_at
        entry = _Entry(
            task_id=task.id, owner=task.owner, rank=PRIORITIES.get(task.priority, 1),
            seq=next(self._seq), eligible_at=eligible, reservation=reservation, release=release,
        )
        self._pending[task.id] = entry
        return entry

    # -- introspection ---------------------------------------------------------------------

    def poke(self) -> None:
        """Ask the loop to look again now (after a clock change a test or an operator made)."""
        self._wake.set()

    def owns(self, task_id: str) -> bool:
        return task_id in self._pending or task_id in self._running

    def stats(self) -> dict[str, Any]:
        return {
            "running": len(self._running), "queued": len(self._pending),
            "max_concurrent": self._s.max_concurrent, "max_queued": self._s.max_queued,
        }

    def position(self, task_id: str) -> int | None:
        """1-based place among this process's waiting tasks by the order the picker would take
        them (ignoring slot limits), or ``None`` if it is not waiting here."""
        entry = self._pending.get(task_id)
        if entry is None:
            return None
        now = self._now()
        order = sorted(self._pending.values(), key=lambda e: self._key(e, now))
        return order.index(entry) + 1

    # -- the loop --------------------------------------------------------------------------

    def _key(self, entry: _Entry, now: datetime) -> tuple[int, int, int]:
        waited = max(0.0, (now - entry.eligible_at).total_seconds())
        rank = max(0, entry.rank - int(waited // self._s.aging_s))
        return (rank, self._running_by_user[entry.owner], entry.seq)

    def _pick(self, now: datetime) -> _Entry | None:
        best: _Entry | None = None
        best_key: tuple[int, int, int] | None = None
        for entry in self._pending.values():
            if not entry.ready or entry.eligible_at > now:
                continue
            if self._running_by_user[entry.owner] >= self._s.max_concurrent_per_user:
                continue
            key = self._key(entry, now)
            if best_key is None or key < best_key:
                best, best_key = entry, key
        return best

    def _dispatch(self) -> None:
        now = self._now()
        while len(self._running) < self._s.max_concurrent:
            entry = self._pick(now)
            if entry is None:
                return
            self._pending.pop(entry.task_id, None)
            self._running[entry.task_id] = entry
            self._running_by_user[entry.owner] += 1
            entry.runner_task = asyncio.get_running_loop().create_task(
                self._execute(entry), name=f"agent-task-{entry.task_id}"
            )

    def _sleep_for(self) -> float:
        now = self._now()
        soonest = None
        for entry in self._pending.values():
            if entry.eligible_at > now:
                delta = (entry.eligible_at - now).total_seconds()
                soonest = delta if soonest is None else min(soonest, delta)
        return max(0.05, min(1.0, soonest)) if soonest is not None else 1.0

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                self._dispatch()
            except Exception:  # pragma: no cover - the loop must outlive one bad entry
                logger.exception("agent task dispatch failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._sleep_for())
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _execute(self, entry: _Entry) -> None:
        outcome: TaskOutcome | None = None
        task: AgentTask | None = None
        try:
            task = await self._store.get(entry.owner, entry.task_id)
            if task is None:
                logger.warning("a queued task's record is gone; dropping it")
                outcome = TaskOutcome(status=TaskStatus.FAILED, release_reservation=True)
                return
            if task.status != TaskStatus.QUEUED or task.cancel_requested:
                # Cancelled (or otherwise settled) through another worker while it waited.
                if not task.is_terminal:
                    await self._store.save(task.with_(
                        status=TaskStatus.CANCELLED, finished_at=self._now(), updated_at=self._now(),
                    ))
                outcome = TaskOutcome(status=TaskStatus.CANCELLED, release_reservation=True)
                task = None
                return
            spec = await self._store.spec(entry.owner, entry.task_id)
            if spec is None:
                outcome = TaskOutcome(
                    status=TaskStatus.FAILED, release_reservation=True,
                    error={"kind": "task_expired", "retryable": True, "details": {},
                           "message": "the task's request is no longer stored; submit it again"},
                )
            else:
                now = self._now()
                task = task.with_(status=TaskStatus.RUNNING, started_at=now, updated_at=now,
                                  heartbeat_at=now)
                await self._store.save(task)
                outcome = await asyncio.wait_for(
                    self._runner(task, spec), timeout=self._s.task_timeout_s
                )
        except asyncio.TimeoutError:
            outcome = TaskOutcome(
                status=TaskStatus.FAILED, release_reservation=True,
                error={"kind": "timeout", "retryable": True, "details": {},
                       "message": f"the task ran longer than {int(self._s.task_timeout_s)} seconds"},
            )
        except asyncio.CancelledError:
            if entry.cancel_reason == "shutdown":
                outcome = TaskOutcome(
                    status=TaskStatus.FAILED, release_reservation=True,
                    error={"kind": "server_shutdown", "retryable": True, "details": {},
                           "message": "the server shut down while this task was running"},
                )
            else:
                outcome = TaskOutcome(status=TaskStatus.CANCELLED, release_reservation=True)
        except Exception as exc:
            logger.exception("agent task runner crashed", extra={"task": entry.task_id})
            outcome = TaskOutcome(
                status=TaskStatus.FAILED, release_reservation=True,
                error={"kind": "internal_error", "retryable": False, "details": {},
                       "message": f"the task failed unexpectedly ({type(exc).__name__})"},
            )
        finally:
            self._running.pop(entry.task_id, None)
            self._running_by_user[entry.owner] -= 1
            if self._running_by_user[entry.owner] <= 0:
                self._running_by_user.pop(entry.owner, None)
            self._wake.set()
            if outcome is not None:
                await self._finish(entry, task, outcome)

    async def _finish(self, entry: _Entry, task: AgentTask | None, outcome: TaskOutcome) -> None:
        try:
            async with entry.lock:
                entry.finished = True
                latest = await self._store.get(entry.owner, entry.task_id)
                base = latest or task
                if base is not None:
                    now = self._now()
                    status = outcome.status
                    if latest is not None and latest.status == TaskStatus.CANCELLED:
                        status = TaskStatus.CANCELLED  # a cancel that landed first stays a cancel
                    finished = None if status in (
                        TaskStatus.AWAITING_APPROVAL, TaskStatus.QUEUED) else now
                    await self._store.save(base.with_(
                        status=status, updated_at=now, finished_at=finished, heartbeat_at=now,
                        result=outcome.result if outcome.result is not None else base.result,
                        error=outcome.error, run_id=outcome.run_id or base.run_id,
                        turn_id=outcome.turn_id or base.turn_id,
                        cancel_requested=base.cancel_requested and status != TaskStatus.CANCELLED,
                    ))
        except Exception:
            logger.warning("could not record a task's outcome", exc_info=True)
        if outcome.release_reservation or outcome.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            await self._give_back(entry)

    async def _give_back(self, entry: _Entry) -> None:
        if entry.release is not None and entry.reservation is not None:
            reservation, entry.reservation = entry.reservation, None
            try:
                await entry.release(reservation)
            except Exception:
                logger.warning("could not give back a task's reserved message", exc_info=True)

    # -- heartbeat and cross-worker cancel -------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._s.heartbeat_s)
            try:
                await self._beat()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                logger.warning("agent task heartbeat failed", exc_info=True)

    async def _beat(self) -> None:
        now = self._now()
        for entry in [*self._pending.values(), *self._running.values()]:
            async with entry.lock:
                if entry.finished:
                    continue
                task = await self._store.get(entry.owner, entry.task_id)
                if task is None:
                    continue
                running = entry.task_id in self._running
                if task.status == TaskStatus.CANCELLED or (task.cancel_requested and running):
                    # Cancelled through another worker: honour it here, where the work is.
                    if running and entry.runner_task is not None and not entry.runner_task.done():
                        entry.cancel_reason = "cancelled"
                        entry.runner_task.cancel()
                    elif not running:
                        self._pending.pop(entry.task_id, None)
                        await self._give_back(entry)
                    continue
                if task.status in TaskStatus.LIVE:
                    await self._store.save(task.with_(heartbeat_at=now))

    # -- cancel ----------------------------------------------------------------------------

    async def cancel(self, task: AgentTask) -> AgentTask:
        """Cancel ``task`` (already authorised as the caller's). Returns its new state.

        Waiting or approving: cancelled at once. Running here: its turn is cancelled and the
        task reads ``cancelled`` once that unwinds. Running on another worker: flagged
        ``cancel_requested``; the owner's heartbeat loop stops it within ``heartbeat_s``."""
        if task.is_terminal:
            return task
        now = self._now()
        entry = self._running.get(task.id)
        if entry is not None and entry.runner_task is not None:
            entry.cancel_reason = "cancelled"
            entry.runner_task.cancel()
            await asyncio.wait({entry.runner_task}, timeout=5.0)
            return await self._store.get(task.owner, task.id) or task.with_(status=TaskStatus.CANCELLED)
        pending = self._pending.pop(task.id, None)
        if pending is not None or task.status in (TaskStatus.QUEUED, TaskStatus.AWAITING_APPROVAL):
            cancelled = task.with_(status=TaskStatus.CANCELLED, finished_at=now, updated_at=now,
                                   cancel_requested=True)
            await self._store.save(cancelled)
            if pending is not None:
                await self._give_back(pending)
            return cancelled
        # Running, but not here.
        flagged = task.with_(cancel_requested=True, updated_at=now)
        await self._store.save(flagged)
        return flagged
