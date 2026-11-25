"""Runtime adapters: event bus, clock, audit sink.

Small, but two of them carry a rule worth stating explicitly.

**The event bus never propagates a handler failure.** A broken subscriber must not raise into
the publisher -- that would turn an observability problem into an availability problem. (Audit
writes and metric updates, in this codebase, actually happen via direct calls threaded through
each engine, not by subscribing to this bus -- Phase E2's `aura_core/triggers/` is the bus's
first real subscriber. This rule matters anyway, for that subscriber and the next one.)

**The clock is injected.** Memory decay, TTLs, budget windows, and scheduling all depend on
time; injecting it is what makes those testable without ``sleep`` or monkey-patching.
"""

from __future__ import annotations

import asyncio
import hmac
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Sequence

from ..domain.events import DomainEvent
from ..domain.outbox import OutboxEntry
from ..domain.telemetry import TurnTelemetry
from ..observability.logging import get_logger
from ..ports.runtime import AuditEvent

__all__ = [
    "InMemoryEventBus",
    "InMemoryEventOutbox",
    "DurableEventBus",
    "SystemClock",
    "FrozenClock",
    "InMemoryAuditSink",
    "InMemoryTelemetrySink",
]

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


class InMemoryEventOutbox:
    """Reference implementation of :class:`~aura_core.ports.storage.EventOutbox` (Phase E1).

    Test/local-dev only, same reasoning as every other ``InMemory*`` adapter here: correct, but
    loses everything on restart, which is the one property an *outbox* cannot have in production.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, event: DomainEvent) -> str:
        entry_id = uuid.uuid4().hex
        async with self._lock:
            self._entries[entry_id] = {
                "event_name": event.name,
                "payload": event.as_dict(),
                "created_at": event.occurred_at,
                "attempts": 0,
                "claimed": False,
                "dead_lettered": False,
            }
        return entry_id

    async def claim_batch(self, *, limit: int = 50) -> Sequence[OutboxEntry]:
        async with self._lock:
            claimable = [
                (entry_id, row) for entry_id, row in self._entries.items()
                if not row["claimed"] and not row["dead_lettered"]
            ]
            claimable.sort(key=lambda pair: pair[1]["created_at"])
            batch = claimable[:limit]
            for entry_id, row in batch:
                row["claimed"] = True
            return [
                OutboxEntry(
                    id=entry_id, event_name=row["event_name"], payload=row["payload"],
                    created_at=row["created_at"], attempts=row["attempts"],
                )
                for entry_id, row in batch
            ]

    async def mark_processed(self, entry_id: str) -> None:
        async with self._lock:
            self._entries.pop(entry_id, None)

    async def mark_failed(self, entry_id: str, *, error: str, max_attempts: int = 5) -> None:
        async with self._lock:
            row = self._entries.get(entry_id)
            if row is None:
                return
            row["attempts"] += 1
            row["claimed"] = False
            row["last_error"] = error
            if row["attempts"] >= max_attempts:
                row["dead_lettered"] = True

    async def stats(self) -> dict[str, float]:
        """Backlog shape for the outbox gauges: pending, dead-lettered, oldest pending age."""
        async with self._lock:
            pending = [row for row in self._entries.values() if not row["dead_lettered"]]
            oldest = min((row["created_at"] for row in pending), default=None)
            return {
                "pending": len(pending),
                "dead_lettered": len(self._entries) - len(pending),
                "oldest_age_s": (
                    max(0.0, (datetime.now(timezone.utc) - oldest).total_seconds())
                    if oldest is not None else 0.0
                ),
            }

    def __len__(self) -> int:
        """Pending (claimable or claimed, not yet processed/dead-lettered) entry count."""
        return sum(1 for row in self._entries.values() if not row["dead_lettered"])


class DurableEventBus:
    """Wraps an event bus so a chosen subset of event types survive a handler failure or a crash
    between publish and dispatch (Phase E1).

    ``durable_event_types`` opts specific event types *out* of the inner bus's live, best-effort
    dispatch and into the outbox instead -- one delivery path per event, not two racing ones (see
    :class:`~aura_core.ports.storage.EventOutbox`). Every other event type is published to
    ``inner`` exactly as before: this wrapper changes nothing about the event bus's existing
    behaviour for the reactions it already serves.
    """

    def __init__(self, *, inner, outbox, durable_event_types: frozenset[type[DomainEvent]]) -> None:
        self._inner = inner
        self._outbox = outbox
        self._durable_types = durable_event_types

    def subscribe(self, event_type: type[DomainEvent], handler) -> None:
        self._inner.subscribe(event_type, handler)

    def subscribe_all(self, handler) -> None:
        self._inner.subscribe_all(handler)

    async def publish(self, event: DomainEvent) -> None:
        if type(event) in self._durable_types:
            await self._outbox.enqueue(event)
            return
        await self._inner.publish(event)

    @property
    def published_count(self) -> int:
        return self._inner.published_count


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

    async def purge_before(self, cutoff: datetime) -> int:
        kept = [e for e in self._events if e.at is None or e.at >= cutoff]
        purged = len(self._events) - len(kept)
        self._events.clear()
        self._events.extend(kept)
        return purged

    async def pseudonymise_actor(self, actor: str, replacement: str) -> int:
        changed = 0
        for event in self._events:
            if event.actor == actor:
                event.actor = replacement
                event.source_ip = None
                changed += 1
        return changed

    def __len__(self) -> int:
        return len(self._events)


class InMemoryTelemetrySink:
    """Bounded ring-buffer implementation of ``TelemetrySink`` (Phase G1).

    Reference implementation and test substrate, same reasoning as :class:`InMemoryAuditSink`:
    production wants :class:`~aura_core.adapters.postgres_telemetry.PostgresTelemetrySink`, whose
    history must survive a restart, which a ring buffer cannot.
    """

    def __init__(self, *, max_turns: int = 10_000) -> None:
        self._turns: deque[TurnTelemetry] = deque(maxlen=max_turns)

    async def record_turn(self, telemetry: TurnTelemetry) -> None:
        self._turns.append(telemetry)

    async def purge_before(self, cutoff: datetime) -> int:
        kept = [t for t in self._turns if t.started_at >= cutoff]
        purged = len(self._turns) - len(kept)
        self._turns.clear()
        self._turns.extend(kept)
        return purged

    async def purge_sessions(self, session_ids) -> int:
        doomed = {str(s) for s in session_ids}
        kept = [t for t in self._turns if t.session_id is None or t.session_id.value not in doomed]
        purged = len(self._turns) - len(kept)
        self._turns.clear()
        self._turns.extend(kept)
        return purged

    async def get_turn_usage(self, turn_id: str, owner_ref: str) -> dict | None:
        if not owner_ref:
            return None
        for t in self._turns:
            if t.turn_id.value == turn_id and t.owner_ref and hmac.compare_digest(t.owner_ref, owner_ref):
                return t.usage_view()
        return None

    async def purge_owner(self, owner_ref: str) -> int:
        if not owner_ref:
            return 0
        kept = [t for t in self._turns if t.owner_ref != owner_ref]
        purged = len(self._turns) - len(kept)
        self._turns.clear()
        self._turns.extend(kept)
        return purged

    async def recent_summary_by_model(self, *, since: datetime, min_turns: int = 1) -> dict[str, dict]:
        """:meth:`recent_summary`'s figures per ``model`` (models with fewer than ``min_turns`` turns left out)."""
        groups: dict[str, list] = {}
        for t in self._turns:
            if t.started_at >= since and t.model:
                groups.setdefault(str(t.model), []).append(t)
        return {m: self._summarise(ts, since) for m, ts in groups.items() if len(ts) >= max(1, min_turns)}

    async def recent_summary(self, *, since: datetime) -> dict:
        return self._summarise([t for t in self._turns if t.started_at >= since], since)

    @staticmethod
    def _summarise(turns: list, since: datetime) -> dict:
        if not turns:
            return {
                "turns": 0, "since": since.astimezone(timezone.utc).isoformat(),
                "error_rate": None, "degraded_rate": None, "refusal_rate": None, "cache_hit_rate": None,
                "p50_latency_ms": None, "p95_latency_ms": None, "avg_prompt_tokens": None,
                "avg_completion_tokens": None, "avg_cost_usd": None, "tool_success_rate": None,
                "avg_groundedness": None,
            }
        latencies = sorted(t.total_ms for t in turns)
        groundedness = [t.groundedness for t in turns if t.groundedness is not None]
        tool_calls = sum(len(t.tools) for t in turns)
        tool_failures = sum(1 for t in turns for tc in t.tools if not tc.ok)
        return {
            "turns": len(turns),
            "since": since.astimezone(timezone.utc).isoformat(),
            "error_rate": round(sum(1 for t in turns if str(t.outcome) != "success") / len(turns), 4),
            "degraded_rate": round(sum(1 for t in turns if t.degraded) / len(turns), 4),
            "refusal_rate": round(sum(1 for t in turns if str(t.outcome) == "blocked") / len(turns), 4),
            "cache_hit_rate": round(sum(1 for t in turns if t.cache.hit) / len(turns), 4),
            "p50_latency_ms": round(_percentile(latencies, 0.5), 2),
            "p95_latency_ms": round(_percentile(latencies, 0.95), 2),
            "avg_prompt_tokens": round(sum(t.prompt_tokens for t in turns) / len(turns), 2),
            "avg_completion_tokens": round(sum(t.completion_tokens for t in turns) / len(turns), 2),
            "avg_cost_usd": round(sum(t.cost_usd for t in turns) / len(turns), 6),
            "tool_success_rate": (
                round(1 - tool_failures / tool_calls, 4) if tool_calls else None
            ),
            "avg_groundedness": (
                round(sum(groundedness) / len(groundedness), 4) if groundedness else None
            ),
        }

    def __len__(self) -> int:
        return len(self._turns)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]
