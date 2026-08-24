"""The trigger dispatch loop.

Mirrors :class:`~aura_core.adapters.runtime.InMemoryEventBus`'s own rule: one broken reaction
must never break another, or break the publisher that caused it. A trigger's ``action`` runs
inside a try/except exactly like an event-bus handler does, and for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..domain.events import DomainEvent
from ..observability.logging import get_logger
from ..ports.storage import InsightSink

__all__ = ["TriggerSpec", "TriggerEngine", "TriggerContext"]

logger = get_logger("triggers")


@dataclass(frozen=True, slots=True)
class TriggerContext:
    """What a trigger's action is allowed to do. Currently one capability -- surfacing a
    proactive insight -- because that is the one durable reaction this phase actually needs;
    widening this is a deliberate future decision, not a default to grow into."""

    insight_sink: InsightSink


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    name: str
    event_types: tuple[type[DomainEvent], ...]
    predicate: Callable[[DomainEvent], bool]
    action: Callable[[DomainEvent, TriggerContext], Awaitable[None]]


class TriggerEngine:
    def __init__(self, specs: tuple[TriggerSpec, ...]) -> None:
        self._specs = specs

    async def handle(self, event: DomainEvent, *, context: TriggerContext) -> list[str]:
        fired: list[str] = []
        for spec in self._specs:
            if not isinstance(event, spec.event_types):
                continue
            try:
                if not spec.predicate(event):
                    continue
            except Exception:
                logger.warning("trigger predicate failed", extra={"trigger": spec.name})
                continue
            try:
                await spec.action(event, context)
                fired.append(spec.name)
            except Exception:
                logger.warning("trigger action failed", extra={"trigger": spec.name})
        return fired

    def __len__(self) -> int:
        return len(self._specs)
