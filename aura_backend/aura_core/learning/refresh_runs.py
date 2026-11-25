"""Run state for an operator-triggered knowledge refresh, shared by every gateway worker.

The gateway runs several uvicorn workers, and the refresh pipeline keeps its last report in the
memory of whichever worker ran it, so a status request landing on another worker saw nothing. A
refresh can also outlast a proxy's request timeout, so triggering one cannot hold the HTTP request
open. This keeps the two things the operator needs in the shared cache:

* a **lock**, so two refreshes never run at once (a real run embeds into the live corpus);
* a **record** of the latest run (started, running, finished, its report or its error).

The cache is best-effort by contract: when it is down the lock cannot be taken reliably and the
record is lost, which degrades to "status unknown", never to a wrong refresh. Depends on the
``Cache`` port only.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

__all__ = ["RefreshRun", "RefreshRunTracker"]

_RECORD_KEY = "aura:knowledge:refresh:last"
_LOCK_KEY = "aura:knowledge:refresh:lock"


@dataclass(frozen=True, slots=True)
class RefreshRun:
    run_id: str
    dry_run: bool
    triggered_by: str
    started_at: datetime
    status: str = "running"  # running | succeeded | failed
    finished_at: datetime | None = None
    report: Mapping[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "triggered_by": self.triggered_by,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "report": dict(self.report) if self.report is not None else None,
            "error": self.error,
        }


def _parse(raw: bytes) -> RefreshRun | None:
    try:
        data = json.loads(raw)
        return RefreshRun(
            run_id=data["run_id"],
            dry_run=bool(data["dry_run"]),
            triggered_by=data["triggered_by"],
            started_at=datetime.fromisoformat(data["started_at"]),
            status=data["status"],
            finished_at=datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None,
            report=data.get("report"),
            error=data.get("error"),
        )
    except (ValueError, KeyError, TypeError):
        return None


class RefreshRunTracker:
    def __init__(
        self, cache: Any, *, lock_ttl_s: int = 1800, record_ttl_s: int = 7 * 24 * 3600
    ) -> None:
        self._cache = cache
        self._lock_ttl = lock_ttl_s
        self._record_ttl = record_ttl_s

    async def start(self, *, dry_run: bool, triggered_by: str) -> RefreshRun | None:
        """Take the lock and record a running run; ``None`` when another run holds the lock."""
        if await self._cache.incr(_LOCK_KEY, ttl_s=self._lock_ttl) > 1:
            return None
        run = RefreshRun(
            run_id=uuid.uuid4().hex[:12], dry_run=dry_run, triggered_by=triggered_by,
            started_at=datetime.now(timezone.utc),
        )
        await self._store(run)
        return run

    async def finish(
        self, run: RefreshRun, *, report: Mapping[str, Any] | None = None, error: str | None = None
    ) -> RefreshRun:
        """Record the outcome and release the lock, whichever way the run ended."""
        done = RefreshRun(
            run_id=run.run_id, dry_run=run.dry_run, triggered_by=run.triggered_by,
            started_at=run.started_at, status="failed" if error else "succeeded",
            finished_at=datetime.now(timezone.utc), report=report, error=error,
        )
        try:
            await self._store(done)
        finally:
            await self._cache.delete(_LOCK_KEY)
        return done

    async def latest(self) -> RefreshRun | None:
        raw = await self._cache.get(_RECORD_KEY)
        run = _parse(raw) if raw else None
        if run is not None and run.status == "running":
            # The worker that started it died (a deploy, a crash): the lock expires by TTL, and
            # the record must not claim a run that can no longer finish.
            if datetime.now(timezone.utc) - run.started_at >= timedelta(seconds=self._lock_ttl):
                return RefreshRun(
                    run_id=run.run_id, dry_run=run.dry_run, triggered_by=run.triggered_by,
                    started_at=run.started_at, status="failed",
                    error="abandoned: the worker that started this run stopped before it finished",
                )
        return run

    async def _store(self, run: RefreshRun) -> None:
        await self._cache.set(
            _RECORD_KEY, json.dumps(run.as_dict(), default=str).encode("utf-8"),
            ttl_s=self._record_ttl,
        )
