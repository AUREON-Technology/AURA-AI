"""Runtime adapters: event bus, clock, audit sink.

Small, but two of them carry a rule worth stating explicitly.

**The event bus never propagates a handler failure.** Audit logging, metric updates, and cache
invalidation subscribe to domain events. If a broken subscriber could raise into the publisher,
a failing audit sink would fail a user's request -- turning an observability problem into an
availability problem.

**The clock is injected.** Memory decay, TTLs, budget windows, and scheduling all depend on
time; injecting it is what makes those testable without ``sleep`` or monkey-patching.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Sequence

from ..domain.events import DomainEvent
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent

__all__ = ["InMemoryEventBus", "SystemClock", "FrozenClock", "InMemoryAuditSink"]

logger = get_logger("adapters.runtime")

Handler = Callable[[DomainEvent], Awaitable[None]]


class InMemoryEventBus:
    """Direct in-process dispatch.

    Handlers run concurrently and every exception is caught, logged, and counted. Delivery is
    at-most-once and not durable -- adequate for the reactions this system subscribes
    (metrics, audit, cache invalidation), none of which are correctness-critical. Anything that
    must not be lost belongs in a Celery task, not an event handler.
    """

    def __init__(self, *, metrics=None) -> None:
        self._handlers: dict[type[DomainEvent], list[Handler]] = {}
        self._metrics = metrics
        self._published = 0

    def subscribe(self, event_type: type[DomainEvent], handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        """Receive every event. Used by the audit and telemetry projections."""
        self._handlers.setdefault(DomainEvent, []).append(handler)

    async def publish(self, event: DomainEvent) -> None:
        self._published += 1
        # Subclass handlers plus catch-all handlers, so a subscriber to DomainEvent sees
        # everything without registering for each concrete type.
        handlers: list[Handler] = []
        for registered_type, registered in self._handlers.items():
            if registered_type is DomainEvent or isinstance(event, registered_type):
                handlers.extend(registered)
        if not handlers:
            return

        results = await asyncio.gather(
            *(handler(event) for handler in handlers), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(
                    "event handler failed",
                    extra={"event": event.name, "error": type(result).__name__},
                )
                if self._metrics is not None:
                    self._metrics.event_handler_failures_total.labels(event=event.name).inc()

    @property
    def published_count(self) -> int:
        return self._published


class SystemClock:
    """Wall clock plus a monotonic source.

    Durations use ``time.monotonic``: wall-clock deltas go backwards across an NTP step, which
    produces negative latencies in metrics and nonsensical circuit-breaker behaviour.
    """

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FrozenClock:
    """Controllable clock for tests.

    Lets a test assert that a memory decayed over six months, or that a circuit breaker
    half-opened after 30 seconds, without waiting.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


class InMemoryAuditSink:
    """Bounded ring-buffer audit sink.

    Reference implementation and test substrate. Production uses the Postgres sink: audit
    records have their own retention policy and must survive a restart, which a ring buffer
    does not.
    """

    def __init__(self, *, max_events: int = 10_000, clock=None) -> None:
        self._events: deque[AuditEvent] = deque(maxlen=max_events)
        self._clock = clock or SystemClock()

    async def record(self, event: AuditEvent) -> None:
        if event.at is None:
            event.at = self._clock.now()
        self._events.append(event)

    async def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Sequence[AuditEvent]:
        matches = [
            e for e in reversed(self._events)
            if (actor is None or e.actor == actor)
            and (action is None or e.action.startswith(action))
            and (since is None or (e.at is not None and e.at >= since))
        ]
        return matches[:limit]

    def __len__(self) -> int:
        return len(self._events)
