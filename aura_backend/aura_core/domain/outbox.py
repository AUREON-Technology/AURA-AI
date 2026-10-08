"""The durable-delivery envelope (Phase E1).

An outbox entry is deliberately *not* a live :class:`~aura_core.domain.events.DomainEvent` --
it is what one looks like after being written down so it can survive the process that published
it dying before any handler ran, or a handler failing for a reason retrying would fix (a
Postgres blip, a timeout). ``payload`` is the event's own ``as_dict()`` -- the same shape
audit/telemetry already render events to -- so decoding it back to a typed event is an explicit,
narrow step (see ``aura_core/triggers/decoders.py``), not a generic, easy-to-get-wrong
deserializer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

__all__ = ["OutboxEntry"]


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    id: str
    event_name: str
    payload: Mapping[str, Any]
    created_at: datetime
    attempts: int
