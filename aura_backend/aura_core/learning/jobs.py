"""Scheduled Loop A / memory-maintenance entry points.

Celery (and tests) call these with an already-built container so the worker
process can memoize construction. An empty source registry is a successful
no-op — that is the production default until an administrator registers sources.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Awaitable, Callable, Iterable, Sequence

from ..domain.events import DomainEvent
from ..domain.identifiers import UserId
from ..domain.lessons import ENGINEER_SCOPE, LESSON_SOURCE, LESSON_SUBJECT, LessonTurn, pending_scope
from ..domain.memory import MemoryKind, MemoryQuery
from ..domain.outbox import OutboxEntry
from ..domain.safety import GuardAction, GuardStage
from ..observability.logging import get_logger
from ..safety.guards import SecretGuard
from .lessons import LessonCandidate, extract_lessons, normalise_lesson

__all__ = [
    "run_knowledge_refresh", "run_memory_consolidate", "run_outbox_drain",
    "run_engineer_lesson_extraction",
]

#: How long an unreviewed lesson stays in the pending scope before consolidation expires it.
PENDING_LESSON_TTL = timedelta(days=30)
#: A candidate whose nearest existing memory is at least this similar is a duplicate.
LESSON_DUPLICATE_SIMILARITY = 0.9

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


async def run_engineer_lesson_extraction(
    memory: Any,
    turn: LessonTurn,
    *,
    checkpoint: Any = None,
    enabled: bool,
    screen: Callable[[str], Awaitable[bool]] | None = None,
    scope: str = ENGINEER_SCOPE,
    model_extractor: Callable[[LessonTurn], Awaitable[list[LessonCandidate] | None]] | None = None,
) -> dict[str, Any]:
    """Derive lessons from one finished Engineer turn and queue them for the owner's review.

    The ADR-008 boundary, as code: lessons are written to ``pending_scope(scope)`` -- a scope no
    persona's recall reads -- with ``source="auto_lesson"`` and ``review="pending"``, and become
    part of the Engineer's memory only when the owner approves them
    (``MemoryEngine.approve_lesson``). Nothing here writes to trusted long-term memory.

    Per candidate, in order: a secret in it (``SecretGuard``) drops it -- redacting and keeping a
    lesson about a credential is not worth the risk of a missed variant; ``screen`` (the context
    guards, when the deployment has them) drops one that reads as an injection; an existing memory
    of the owner's, pending or approved, that says the same thing makes it a duplicate. A failure
    on one candidate never stops the next, and nothing raises: this runs after the turn has already
    been answered.
    """
    if not enabled or memory is None:
        return {"status": "skipped", "reason": "lessons_disabled"}
    if turn.tainted:
        return {"status": "skipped", "reason": "tainted_turn"}
    if turn.agent_id != "engineer":
        return {"status": "skipped", "reason": "not_engineer"}

    # ADR-047 (row 60): an optional model-written path. ``None`` or an empty list -- unavailable,
    # refused, not valid JSON for the contract, or "nothing worth keeping" from a small model --
    # falls back to the keyword extractor, so enabling it can only add lessons, never lose the
    # ones the heuristic would have found. Its candidates take the same road below.
    extractor = "heuristic"
    candidates: list[LessonCandidate] = []
    if model_extractor is not None:
        try:
            candidates = list(await model_extractor(turn) or [])
        except Exception:
            logger.warning("model lesson extraction failed; using the heuristic", exc_info=True)
            candidates = []
        extractor = "model" if candidates else "heuristic_fallback"
    if not candidates:
        candidates = extract_lessons(turn, checkpoint)
    report = {
        "status": "success", "extractor": extractor, "outcome": turn.outcome,
        "candidates": len(candidates), "stored": 0,
        "duplicates": 0, "rejected_secret": 0, "rejected_unsafe": 0, "errors": 0,
    }
    secrets = SecretGuard()
    pending = pending_scope(scope)
    for candidate in candidates:
        try:
            if secrets.evaluate(candidate.text, stage=GuardStage.OUTPUT).action is GuardAction.BLOCK:
                report["rejected_secret"] += 1
                continue
            if screen is not None and not await screen(candidate.text):
                report["rejected_unsafe"] += 1
                continue
            if await _is_duplicate(memory, turn.user_id, candidate.text, (scope, pending)):
                report["duplicates"] += 1
                continue
            await memory.remember(
                owner_id=turn.user_id,
                content=candidate.text,
                kind=MemoryKind.PROCEDURAL,
                subject=LESSON_SUBJECT,
                confidence=0.5,
                source=LESSON_SOURCE,
                # No ``session_id``: recall treats a record with one as that conversation's own, so a
                # lesson stamped with its session would never be seen by the next one. Where it came
                # from is kept in the metadata instead.
                extractor="engineer_lessons_model" if extractor == "model" else "engineer_lessons",
                extractor_version="2" if extractor == "model" else "1",
                metadata={
                    "review": "pending", "lesson_kind": candidate.kind,
                    "turn_id": turn.turn_id, "turn_outcome": turn.outcome,
                    "source_session": turn.session_id,
                },
                scope=pending,
                ttl=PENDING_LESSON_TTL,
            )
            report["stored"] += 1
        except Exception:
            report["errors"] += 1
            logger.warning("lesson extraction failed for one candidate", exc_info=True)
    if report["errors"] and not report["stored"]:
        report["status"] = "partial"
    return report


async def _is_duplicate(memory: Any, owner: UserId, text: str, scopes: tuple[str, ...]) -> bool:
    key = normalise_lesson(text)
    hits = await memory.recall(MemoryQuery(
        owner_id=owner, text=text, kinds=(MemoryKind.PROCEDURAL,), scopes=scopes,
        limit=5, min_score=0.0,
    ))
    return any(
        hit.similarity >= LESSON_DUPLICATE_SIMILARITY or normalise_lesson(hit.record.content) == key
        for hit in hits
    )
