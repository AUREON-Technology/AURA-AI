"""Concrete triggers (Phase E2) and the durable-event decode registry (Phase E1).

One real trigger ships here, not a framework's worth of examples: ``DocumentIngested`` with
``quarantined=True`` is genuinely published today (``aura_core/knowledge/engine.py``, both the
fully- and partially-quarantined paths) and genuinely never consumed by anything -- a document a
user uploaded can be silently short of the content they expect, with no record anywhere that
tells them so. That is exactly the "reaction that must not be lost" this phase exists for.

``DURABLE_EVENT_TYPES`` and the decoders below are kept in lockstep deliberately (see
``tests/unit/test_triggers.py``'s fitness check): a durable event type with no decoder would
enqueue into the outbox and then fail every drain attempt forever.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from ..domain.events import DocumentIngested, DomainEvent
from ..domain.identifiers import CorrelationId, DocumentId, UserId, deterministic_uuid
from ..domain.outbox import OutboxEntry
from .engine import TriggerContext, TriggerSpec

__all__ = ["DEFAULT_TRIGGER_SPECS", "DURABLE_EVENT_TYPES", "decode_durable_event"]


async def _surface_quarantine_insight(event: DomainEvent, context: TriggerContext) -> None:
    assert isinstance(event, DocumentIngested)
    if event.owner_id is None:
        return  # a global (no-owner) ingestion has no user's Today screen to surface on
    doc_key = event.document_id.value if event.document_id else "unknown"
    insight_id = str(
        deterministic_uuid("insight", "document_quarantined", doc_key, str(event.version))
    )
    if event.chunks == 0:
        message = "A document you added was fully excluded from your knowledge base."
    else:
        message = "Part of a document you added was excluded from your knowledge base."
    await context.insight_sink.surface(
        event.owner_id,
        insight_id=insight_id,
        kind="document_quarantined",
        message=message,
        explanation=(
            f"{event.chunks} chunk(s) from this upload (version {event.version}) passed AURA's "
            "safety review and were indexed; the rest looked like embedded instructions rather "
            "than your own content and were left out."
        ),
    )


DEFAULT_TRIGGER_SPECS: tuple[TriggerSpec, ...] = (
    TriggerSpec(
        name="document_quarantined",
        event_types=(DocumentIngested,),
        predicate=lambda e: bool(e.quarantined),
        action=_surface_quarantine_insight,
    ),
)


def _decode_document_ingested(payload: Mapping[str, Any]) -> DocumentIngested:
    return DocumentIngested(
        correlation_id=CorrelationId(payload["correlation_id"]) if payload.get("correlation_id") else None,
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        document_id=DocumentId(payload["document_id"]) if payload.get("document_id") else None,
        version=payload.get("version", 1),
        chunks=payload.get("chunks", 0),
        source_type=payload.get("source_type", "upload"),
        owner_id=UserId(payload["owner_id"]) if payload.get("owner_id") else None,
        quarantined=bool(payload.get("quarantined", False)),
    )


_DECODERS: dict[str, Callable[[Mapping[str, Any]], DomainEvent]] = {
    "DocumentIngested": _decode_document_ingested,
}

DURABLE_EVENT_TYPES: frozenset[type[DomainEvent]] = frozenset({DocumentIngested})


def decode_durable_event(entry: OutboxEntry) -> DomainEvent:
    decoder = _DECODERS.get(entry.event_name)
    if decoder is None:
        raise ValueError(f"no decoder registered for durable event {entry.event_name!r}")
    return decoder(entry.payload)
