"""Admission control and backpressure for generation capacity (roadmap P2.4).

Phase A4 measured the failure this exists for: with ``OLLAMA_NUM_PARALLEL=1`` throughput is flat
from 1 to 8 concurrent turns and p95 reaches 70 s, because every request past the first piles up
*inside* the model server, invisible to AURA, until the provider timeout cancels it mid-generation.
Admission moves that queue into the gateway, where it can be bounded, ordered, timed out, and
measured:

* **Per-group concurrency cap.** A *capacity group* is one serving process (an Ollama instance,
  one vLLM server). Providers default to a group of their own; two model names served by one
  process declare the same ``capacity_group`` and share one cap.
* **Bounded priority queue.** Waiters are admitted lowest priority number first (interactive
  chat/voice = 1, engineer = 2, background insight = 3, RAG indexing = 4, training = 5), FIFO
  within a priority. When the queue is full, a new request evicts the least urgent waiter if it
  is strictly more urgent than that waiter; otherwise it is refused.
* **Queue timeout.** A waiter not admitted within ``queue_timeout_s`` is refused with
  :class:`~aura_core.domain.errors.OverloadedError` -- a fast, honest 503 with ``Retry-After``
  instead of a 180 s wait that ends in a provider timeout anyway.
* **Per-user concurrent quota.** One user may hold at most ``user_max_concurrent`` slots across all
  groups, and queue at most as many more; beyond that the request is refused with
  :class:`~aura_core.domain.errors.UserConcurrencyLimitError` (429). A user at quota waits rather
  than failing, so a reasoner's parallel self-consistency samples still complete.
* **No leaks on cancellation.** A waiter cancelled while queued leaves the queue; a waiter
  cancelled in the instant between being granted a slot and resuming gives the slot back. Every
  granted ticket is released exactly once (``release`` is idempotent).

Limits are **per process**. The gateway runs ``AURA_WORKERS`` uvicorn workers and each Celery
worker builds its own container, so the effective cap on a model server is this cap times the
number of processes that call it. A cross-process (Redis-backed) controller would close that gap;
it is not built. Until it is, size the per-process cap as ``server_parallelism / processes``
where that is >= 1, and accept that the model server's own queue absorbs the remainder.

Timing uses an injected monotonic clock for everything it *measures* and for
:meth:`AdmissionController.expire_overdue`, so tests drive queue timeouts with a ``FrozenClock``
and no sleeping. In production the clock is ``time.monotonic`` and a real event-loop timer
enforces each waiter's deadline as well.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..domain.errors import OverloadedError, UserConcurrencyLimitError, ValidationError
from .circuit import LatencyTracker

__all__ = ["AdmissionController", "AdmissionTicket", "PoolLimits"]

#: Retry-After bounds, seconds. The floor keeps clients from hammering; the ceiling keeps a
#: stale latency estimate from telling a client to go away for an hour.
_MIN_RETRY_AFTER_S = 1
_MAX_RETRY_AFTER_S = 300
#: Service-time guess before any generation in the group has been measured.
_DEFAULT_SERVICE_MS = 5000.0


@dataclass(frozen=True, slots=True)
class PoolLimits:
    """Admission limits for one capacity group.

    ``max_queue``: ``-1`` = unbounded (the pre-P2.4 behaviour), ``0`` = never wait (refuse when
    every slot is busy), ``n`` = at most ``n`` waiters. ``queue_timeout_s``: ``0`` = wait without
    bound (the pre-P2.4 behaviour).
    """

    max_concurrent: int
    max_queue: int = -1
    queue_timeout_s: float = 0.0

    def __post_init__(self) -> None:
        if self.max_concurrent < 1:
            raise ValidationError("max_concurrent must be >= 1")
        if self.max_queue < -1:
            raise ValidationError("max_queue must be -1 (unbounded) or >= 0")
        if self.queue_timeout_s < 0:
            raise ValidationError("queue_timeout_s cannot be negative")


@dataclass(eq=False, slots=True)
class AdmissionTicket:
    """Proof of one held slot. Hand it back to :meth:`AdmissionController.release`."""

    group: str
    priority: int
    user_key: str | None
    granted_at: float
    queue_wait_ms: float
    released: bool = False


@dataclass(eq=False, slots=True)
class _Waiter:
    group: str
    priority: int
    seq: int
    user_key: str | None
    future: asyncio.Future
    enqueued_at: float
    deadline: float | None = None
    timer: Any = None
    granted: bool = False
    ticket: AdmissionTicket | None = None


@dataclass(slots=True)
class _Group:
    name: str
    limits: PoolLimits
    inflight: int = 0
    waiters: list[_Waiter] = field(default_factory=list)
    service: LatencyTracker = field(default_factory=LatencyTracker)


class AdmissionController:
    """Priority-ordered, bounded admission across capacity groups."""

    def __init__(
        self,
        limits: Mapping[str, PoolLimits],
        *,
        default_limits: PoolLimits | None = None,
        user_max_concurrent: int = 0,
        clock: Callable[[], float] | None = None,
        metrics: Any = None,
    ) -> None:
        if user_max_concurrent < 0:
            raise ValidationError("user_max_concurrent cannot be negative")
        self._clock = clock or time.monotonic
        self._metrics = metrics
        # Unknown groups get a generous default rather than an error: a provider added at runtime
        # must not be unroutable because nobody declared its limits.
        self._default_limits = default_limits or PoolLimits(max_concurrent=64)
        self._groups: dict[str, _Group] = {}
        for name, lim in limits.items():
            self._group(name, lim)
        self._user_limit = user_max_concurrent
        self._user_inflight: dict[str, int] = {}
        self._seq = 0

    # -- introspection -----------------------------------------------------------------

    def limits(self, group: str) -> PoolLimits:
        return self._group(group).limits

    def capacity(self, group: str) -> int:
        return self._group(group).limits.max_concurrent

    def inflight(self, group: str) -> int:
        return self._group(group).inflight

    def queued(self, group: str) -> int:
        return len(self._group(group).waiters)

    def user_inflight(self, user_key: str) -> int:
        return self._user_inflight.get(user_key, 0)

    @property
    def user_max_concurrent(self) -> int:
        return self._user_limit

    def snapshot(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, g in self._groups.items():
            by_priority: dict[int, int] = {}
            for w in g.waiters:
                by_priority[w.priority] = by_priority.get(w.priority, 0) + 1
            out[name] = {
                "capacity": g.limits.max_concurrent,
                "inflight": g.inflight,
                "queued": len(g.waiters),
                "queued_by_priority": dict(sorted(by_priority.items())),
                "max_queue": g.limits.max_queue,
                "queue_timeout_s": g.limits.queue_timeout_s,
                "service_ms": round(g.service.value_ms, 1) if g.service.value_ms else None,
            }
        return out

    # -- admission ---------------------------------------------------------------------

    async def acquire(
        self, group: str, *, priority: int = 1, user_key: str | None = None
    ) -> AdmissionTicket:
        """Wait for a slot in ``group``; raise ``OverloadedError`` if one cannot be had in time."""
        self.expire_overdue()
        g = self._group(group)
        now = self._clock()

        if g.inflight < g.limits.max_concurrent and self._user_ok(user_key):
            # Any waiter still queued while a slot is free is blocked on its own user's quota
            # (dispatch runs on every release), so admitting this request jumps nobody.
            ticket = self._grant(g, priority=priority, user_key=user_key, wait_ms=0.0)
            self._count(g, "admitted", priority)
            return ticket

        if user_key is not None and self._user_limit and not self._user_ok(user_key):
            if self._user_queued(user_key) >= self._user_limit:
                self._count(g, "user_quota", priority)
                raise UserConcurrencyLimitError(
                    "too many concurrent generations for this user",
                    retry_after=self._retry_after(g, backlog=False),
                    pool=g.name,
                    reason="user_quota",
                    details={"limit": self._user_limit},
                )

        if g.limits.max_queue >= 0 and len(g.waiters) >= g.limits.max_queue:
            victim = self._least_urgent(g)
            if victim is None or victim.priority <= priority:
                self._count(g, "queue_full", priority)
                raise OverloadedError(
                    f"generation capacity for {g.name!r} is saturated",
                    retry_after=self._retry_after(g),
                    pool=g.name,
                    reason="queue_full",
                    details={"max_queue": g.limits.max_queue, "capacity": g.limits.max_concurrent},
                )
            self._evict(g, victim, reason="preempted")

        loop = asyncio.get_running_loop()
        self._seq += 1
        waiter = _Waiter(
            group=g.name,
            priority=priority,
            seq=self._seq,
            user_key=user_key,
            future=loop.create_future(),
            enqueued_at=now,
        )
        if g.limits.queue_timeout_s > 0:
            waiter.deadline = now + g.limits.queue_timeout_s
            waiter.timer = loop.call_later(
                g.limits.queue_timeout_s, self._evict_if_queued, waiter, "queue_timeout"
            )
        g.waiters.append(waiter)
        self._publish_depth(g)

        try:
            return await waiter.future
        except BaseException:
            # Covers cancellation (client went away) and the waiter's own refusal. Either way the
            # waiter must end up neither queued nor holding a slot.
            self._abandon(waiter)
            raise

    def release(self, ticket: AdmissionTicket) -> None:
        """Return a slot. Idempotent, so a ``finally`` that races a cancel cannot double-free."""
        if ticket.released:
            return
        ticket.released = True
        g = self._group(ticket.group)
        g.inflight = max(0, g.inflight - 1)
        g.service.observe((self._clock() - ticket.granted_at) * 1000.0)
        if ticket.user_key is not None and self._user_limit:
            remaining = self._user_inflight.get(ticket.user_key, 0) - 1
            if remaining > 0:
                self._user_inflight[ticket.user_key] = remaining
            else:
                self._user_inflight.pop(ticket.user_key, None)
        self._publish_inflight(g)
        if self._user_limit:
            # A freed user slot can unblock that user's waiter in any group.
            for other in self._groups.values():
                self._dispatch(other)
        else:
            self._dispatch(g)

    def expire_overdue(self) -> int:
        """Refuse every waiter whose deadline (by the injected clock) has passed.

        Called on every acquire; also the hook tests use to fire a queue timeout with a
        ``FrozenClock`` instead of sleeping.
        """
        now = self._clock()
        expired = 0
        for g in self._groups.values():
            for waiter in [w for w in g.waiters if w.deadline is not None and now >= w.deadline]:
                self._evict(g, waiter, reason="queue_timeout")
                expired += 1
        return expired

    # -- internals ---------------------------------------------------------------------

    def _group(self, name: str, limits: PoolLimits | None = None) -> _Group:
        g = self._groups.get(name)
        if g is None:
            g = _Group(name=name, limits=limits or self._default_limits)
            self._groups[name] = g
            if self._metrics is not None:
                self._metrics.llm_group_capacity.labels(group=name).set(g.limits.max_concurrent)
        return g

    def _user_ok(self, user_key: str | None) -> bool:
        if user_key is None or not self._user_limit:
            return True
        return self._user_inflight.get(user_key, 0) < self._user_limit

    def _user_queued(self, user_key: str) -> int:
        return sum(1 for g in self._groups.values() for w in g.waiters if w.user_key == user_key)

    def _grant(
        self, g: _Group, *, priority: int, user_key: str | None, wait_ms: float
    ) -> AdmissionTicket:
        g.inflight += 1
        if user_key is not None and self._user_limit:
            self._user_inflight[user_key] = self._user_inflight.get(user_key, 0) + 1
        self._publish_inflight(g)
        return AdmissionTicket(
            group=g.name,
            priority=priority,
            user_key=user_key,
            granted_at=self._clock(),
            queue_wait_ms=wait_ms,
        )

    def _dispatch(self, g: _Group) -> None:
        """Hand free slots to the most urgent eligible waiters."""
        changed = False
        while g.inflight < g.limits.max_concurrent:
            best: _Waiter | None = None
            for w in list(g.waiters):
                if w.future.done():
                    # Cancelled while queued (nothing else completes a queued waiter's future);
                    # its own `_abandon` finds it already gone when its task resumes.
                    g.waiters.remove(w)
                    if w.timer is not None:
                        w.timer.cancel()
                    self._count(g, "cancelled", w.priority)
                    changed = True
                    continue
                if not self._user_ok(w.user_key):
                    continue
                if best is None or (w.priority, w.seq) < (best.priority, best.seq):
                    best = w
            if best is None:
                break
            g.waiters.remove(best)
            changed = True
            if best.timer is not None:
                best.timer.cancel()
            wait_ms = max(0.0, (self._clock() - best.enqueued_at) * 1000.0)
            best.ticket = self._grant(
                g, priority=best.priority, user_key=best.user_key, wait_ms=wait_ms
            )
            best.granted = True
            best.future.set_result(best.ticket)
            self._count(g, "admitted", best.priority)
        if changed:
            self._publish_depth(g)

    def _least_urgent(self, g: _Group) -> _Waiter | None:
        live = [w for w in g.waiters if not w.future.done()]
        if not live:
            return None
        # Highest priority number, newest first among equals: the one that has waited least.
        return max(live, key=lambda w: (w.priority, w.seq))

    def _evict_if_queued(self, waiter: _Waiter, reason: str) -> None:
        g = self._groups.get(waiter.group)
        if g is not None and waiter in g.waiters:
            self._evict(g, waiter, reason=reason)

    def _evict(self, g: _Group, waiter: _Waiter, *, reason: str) -> None:
        if waiter in g.waiters:
            g.waiters.remove(waiter)
        if waiter.timer is not None:
            waiter.timer.cancel()
        self._publish_depth(g)
        if waiter.future.done():
            return
        self._count(g, reason, waiter.priority)
        message = (
            f"no generation slot in {g.name!r} within {g.limits.queue_timeout_s:g}s"
            if reason == "queue_timeout"
            else f"queued generation for {g.name!r} displaced by more urgent work"
        )
        waiter.future.set_exception(
            OverloadedError(
                message,
                retry_after=self._retry_after(g),
                pool=g.name,
                reason=reason,
                details={"capacity": g.limits.max_concurrent},
            )
        )

    def _abandon(self, waiter: _Waiter) -> None:
        g = self._group(waiter.group)
        if waiter.granted:
            if waiter.ticket is not None and not waiter.ticket.released:
                # Granted and then cancelled before the task could use the slot.
                self.release(waiter.ticket)
            return
        if waiter in g.waiters:
            g.waiters.remove(waiter)
            self._count(g, "cancelled", waiter.priority)
            self._publish_depth(g)
        if waiter.timer is not None:
            waiter.timer.cancel()

    def _retry_after(self, g: _Group, *, backlog: bool = True) -> int:
        service_ms = g.service.value_ms or (
            g.limits.queue_timeout_s * 1000.0 if g.limits.queue_timeout_s else _DEFAULT_SERVICE_MS
        )
        rounds = (len(g.waiters) + 1) / g.limits.max_concurrent if backlog else 1.0
        seconds = math.ceil(service_ms / 1000.0 * max(1.0, rounds))
        return int(min(_MAX_RETRY_AFTER_S, max(_MIN_RETRY_AFTER_S, seconds)))

    # -- metrics -----------------------------------------------------------------------

    def _count(self, g: _Group, outcome: str, priority: int) -> None:
        if self._metrics is not None:
            self._metrics.llm_admission_total.labels(
                group=g.name, outcome=outcome, priority=str(priority)
            ).inc()

    def _publish_depth(self, g: _Group) -> None:
        if self._metrics is not None:
            self._metrics.llm_queue_depth.labels(provider=g.name).set(len(g.waiters))

    def _publish_inflight(self, g: _Group) -> None:
        if self._metrics is not None:
            self._metrics.llm_group_inflight.labels(group=g.name).set(g.inflight)
