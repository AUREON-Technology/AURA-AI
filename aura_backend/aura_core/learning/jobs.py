"""Scheduled Loop A / memory-maintenance entry points.

Celery (and tests) call these with an already-built container so the worker
process can memoize construction. An empty source registry is a successful
no-op — that is the production default until an administrator registers sources.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from ..domain.identifiers import UserId

__all__ = ["run_knowledge_refresh", "run_memory_consolidate"]


async def run_knowledge_refresh(pipeline: Any, *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "reason": "refresh_disabled"}
    report = await pipeline.run()
    return {
        "status": "success",
        "promoted": report.promoted,
        "ingested": report.documents_ingested,
        "skipped": report.documents_skipped,
        "quarantined": report.quarantined,
        "failed": report.failures,
        "gate_failures": list(report.gate_failures),
    }


async def run_memory_consolidate(
    memory: Any,
    owner_ids: Sequence[UserId] | Iterable[UserId],
    *,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled or memory is None:
        return {"status": "skipped", "reason": "consolidation_disabled"}
    owners = 0
    for owner_id in owner_ids:
        await memory.consolidate(owner_id)
        owners += 1
    return {"status": "success", "owners": owners}
