"""Copy the OSV advisories for the dependency scan into the local store (scheduled, off by default).

The scan reads only the store (``tools/open_workspace.py``); this job is the one thing that talks to
the feed. First run per ecosystem downloads the bulk archive; later runs fetch only the advisories the
feed lists as modified since the newest one already held, and fall back to the archive when that list
is long. A run that fails for an ecosystem records the error and leaves that ecosystem's "synced at"
where it was, so the scan reports the data as old rather than current.

Advisories named ``MAL-*`` (the malicious-package reports, which dominate the npm archive) are skipped
unless ``include_malware`` is set: they are not version ranges of a vulnerable library.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..domain.advisories import (
    EcosystemStatus,
    FEED_ECOSYSTEMS,
    StoredAdvisory,
    is_withdrawn,
    parse_osv_time,
    slim_advisory,
)
from ..observability.logging import get_logger

__all__ = ["run_osv_sync"]

logger = get_logger("learning.osv_sync")

#: More modified advisories than this since the last run: download the archive instead of one by one.
MAX_INCREMENTAL = 2000
CONCURRENCY = 8


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def run_osv_sync(
    store: Any,
    feed: Any,
    *,
    ecosystems: Sequence[str],
    include_malware: bool = False,
    clock: Callable[[], datetime] = _utc_now,
    max_incremental: int = MAX_INCREMENTAL,
) -> dict[str, Any]:
    """Refresh each of ``ecosystems`` (``"pypi"``, ``"npm"``); one failing does not stop the others."""
    previous = {s.ecosystem: s for s in await store.status()}
    report: dict[str, Any] = {}
    failed = 0
    for eco in ecosystems:
        if eco not in FEED_ECOSYSTEMS:
            report[eco] = {"status": "skipped", "reason": "unsupported ecosystem"}
            continue
        before = previous.get(eco, EcosystemStatus(eco))
        started = clock()
        try:
            outcome = await _sync_one(store, feed, eco, before, include_malware, max_incremental)
        except Exception as exc:  # recorded, not raised: the scan must be told the data is old
            failed += 1
            message = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning("advisory sync failed for %s: %s", eco, message)
            await store.record_sync(EcosystemStatus(
                eco, before.advisories, before.newest_modified, before.synced_at, message, started))
            report[eco] = {"status": "error", "error": message}
            continue
        finished = clock()
        await store.record_sync(EcosystemStatus(
            eco, 0, outcome.pop("_newest"), finished, None, finished))
        report[eco] = {"status": "success", **outcome}
    return {"status": "success" if not failed else ("error" if failed == len(report) else "partial"),
            "ecosystems": report}


async def _sync_one(store: Any, feed: Any, eco: str, before: EcosystemStatus,
                    include_malware: bool, max_incremental: int) -> dict[str, Any]:
    counts = {"fetched": 0, "stored": 0, "removed": 0, "skipped_malware": 0}
    newest = before.newest_modified
    pending: list[Mapping[str, Any]] = []

    async def ingest(docs: Sequence[Mapping[str, Any]]) -> None:
        nonlocal newest
        items: list[StoredAdvisory] = []
        gone: list[str] = []
        for doc in docs:
            counts["fetched"] += 1
            when = parse_osv_time(doc.get("modified"))
            if when is not None and (newest is None or when > newest):
                newest = when
            adv_id = doc.get("id")
            if not isinstance(adv_id, str):
                continue
            if is_withdrawn(doc):
                gone.append(adv_id)
            elif not include_malware and adv_id.startswith("MAL-"):
                counts["skipped_malware"] += 1
            else:
                items += slim_advisory(doc, eco)
        if gone:
            counts["removed"] += await store.remove(eco, gone)
        if items:
            counts["stored"] += await store.upsert(items)

    mode = "bootstrap"
    if before.newest_modified is not None:
        index = await feed.modified_since(eco)
        fresh = [(when, adv_id) for when, adv_id in index if when > before.newest_modified]
        if not fresh:
            return {"mode": "unchanged", **counts, "_newest": before.newest_modified}
        if len(fresh) <= max_incremental:
            mode = "incremental"
            gate = asyncio.Semaphore(CONCURRENCY)

            async def one(adv_id: str) -> Mapping[str, Any] | None:
                async with gate:
                    return await feed.fetch(eco, adv_id)

            docs = await asyncio.gather(*(one(adv_id) for _, adv_id in fresh))
            missing = [adv_id for (_, adv_id), doc in zip(fresh, docs) if doc is None]
            if missing:  # listed as modified yet no file: it was deleted from the feed
                counts["removed"] += await store.remove(eco, missing)
            pending = [d for d in docs if d is not None]
            await ingest(pending)
            newest = max(newest or before.newest_modified, max(when for when, _ in fresh))
    if mode == "bootstrap":
        async for batch in feed.archive(eco):
            await ingest(batch)
    return {"mode": mode, **counts, "_newest": newest}
