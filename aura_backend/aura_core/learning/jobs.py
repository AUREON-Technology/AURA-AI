"""Scheduled Loop A / memory-maintenance entry points.

Celery (and tests) call these with an already-built container so the worker
process can memoize construction. An empty source registry is a successful
no-op — that is the production default until an administrator registers sources.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable, Sequence

from ..domain.events import DomainEvent
from ..domain.identifiers import UserId
from ..domain.outbox import OutboxEntry
from ..observability.logging import get_logger

__all__ = ["run_knowledge_refresh", "run_memory_consolidate", "run_outbox_drain"]

logger = get_logger("learning.jobs")


async def run_knowledge_refresh(pipeline: Any, *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "reason": "refresh_disabled"}
    report = await pipeline.run()
    return {
        "status": "success",
        # Whether the alias moved. Normally false: with an unchanged embedder a refresh writes
        # into the live index, so ``index_rebuild`` is ``not_needed``.
        "promoted": report.promoted,
        "index_rebuild": report.index_rebuild,
        "ingested": report.documents_ingested,
        "skipped": report.documents_skipped,
        "quarantined": report.quarantined,
        "failed": report.failures,
        "held": len(report.held_sources),
        # Index-phase failures plus each held source's reasons, prefixed ``source:<id>:``.
        "gate_failures": list(report.all_gate_failures),
    }


async def run_memory_consolidate(
    memory: Any,
    owner_ids: Sequence[UserId] | Iterable[UserId],
    *,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled or memory is None:
        return {"status": "skipped", "reason": "consolidation_disabled"}
    owners = errors = 0
    for owner_id in owner_ids:
        owners += 1
        try:
            await memory.consolidate(owner_id)
        except Exception as exc:
            # One owner's failure must not halt the nightly run for every owner after it (F28).
            # Counted and logged; the next night retries it with everyone else.
            errors += 1
            logger.warning(
                "memory consolidation failed for one owner",
                extra={"owner": owner_id.value, "error": type(exc).__name__},
                exc_info=True,
            )
    return {"status": "partial" if errors else "success", "owners": owners, "errors": errors}


async def run_outbox_drain(
    outbox: Any,
    decode: Callable[[OutboxEntry], DomainEvent],
    dispatch: Callable[[DomainEvent], Awaitable[Any]],
    *,
    batch_size: int = 50,
    max_attempts: int = 5,
) -> dict[str, Any]:
    """Claim a batch of durable events (Phase E1) and hand each to ``dispatch`` (Phase E2's
    trigger engine, in production) once ``decode`` reconstructs a typed event from its stored
    envelope. A decode or dispatch failure marks the entry failed rather than raising -- one bad
    entry must not strand the rest of the batch unprocessed, the same reasoning as every event
    handler and trigger action in this codebase swallowing its own exceptions."""
    entries = await outbox.claim_batch(limit=batch_size)
    processed = failed = dead_lettered = 0
    for entry in entries:
        try:
            event = decode(entry)
            await dispatch(event)
        except Exception as exc:
            await outbox.mark_failed(entry.id, error=str(exc), max_attempts=max_attempts)
            failed += 1
            if entry.attempts + 1 >= max_attempts:
                dead_lettered += 1
                logger.warning(
                    "outbox entry dead-lettered", extra={"entry": entry.id, "event": entry.event_name},
                )
            continue
        await outbox.mark_processed(entry.id)
        processed += 1
    return {
        "status": "success", "claimed": len(entries), "processed": processed,
        "failed": failed, "dead_lettered": dead_lettered,
    }
