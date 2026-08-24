"""The trigger engine (Phase E2): the first real subscriber the event bus has ever had.

Every engine in this codebase is wired with an ``event_bus`` and publishes to it, but until this
package nothing ever called ``subscribe``/``subscribe_all`` outside a test fixture -- domain
events were facts nobody was listening for. A :class:`TriggerSpec` is data (which event types,
which predicate, which action), the same "grant is data, not code" shape Phase C1 gave agents, so
a deployment adds or narrows a trigger without touching the dispatch loop.
"""

from .engine import TriggerContext, TriggerEngine, TriggerSpec
from .specs import DEFAULT_TRIGGER_SPECS, DURABLE_EVENT_TYPES, decode_durable_event

__all__ = [
    "TriggerSpec",
    "TriggerEngine",
    "TriggerContext",
    "DEFAULT_TRIGGER_SPECS",
    "DURABLE_EVENT_TYPES",
    "decode_durable_event",
]
