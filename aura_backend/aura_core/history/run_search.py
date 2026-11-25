"""Search and timeline over a user's agent runs (ADR-043, roadmap row 71).

A stored run keeps a digest of its goal, never the goal (``domain/agent_runs.py``: the content
rule), so a stored run cannot be searched by what the person asked. This module adds what can be
added without a new table and without relaxing that rule by default:

* **Always**: filter and match on what a run *did* -- agent, status, stop reason, the names of the
  tools it called, its id, dates.
* **Opt in** (``AURA_AGENT_RUNS_STORE_PREVIEW``, default off): each run also keeps a short
  secret-redacted preview of its goal in the cache port, under a key derived from the owner. The
  Postgres run store has no column for it, and the schema is Alembic's alone, so the preview lives
  beside the run rather than in it, expires on its own TTL, and is simply absent for runs from
  before it was switched on. Text search then matches the preview as well.

Every response says which of these it searched (``search_scope``), so an empty result can never be
mistaken for "nothing matched" when the goal text was never stored.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ..domain.agent_runs import run_metrics
from ..domain.errors import ValidationError
from ..domain.identifiers import AgentRunId, UserId
from ..observability.logging import get_logger
from ..safety.guards import GuardAction, GuardStage, SecretGuard
from .cache_log import owner_digest

__all__ = ["RunSearchIndex", "parse_bound", "PREVIEW_CHARS"]

logger = get_logger("history.run_search")

PREVIEW_CHARS = 200
#: Step lookups one request may make (each is a store read). Matching on tool names is exact for
#: the newest runs up to this many and is reported as capped beyond it.
MAX_STEP_LOOKUPS = 60
_STEP_CONCURRENCY = 8
_SECRETS = SecretGuard(block=False)


def redact_preview(goal: str) -> str:
    """The first :data:`PREVIEW_CHARS` characters of ``goal``, whitespace collapsed, with any
    credential-shaped text replaced. Redaction runs on the *whole* goal before truncating, so a
    key that straddles the cut cannot leave its first half behind."""
    text = " ".join(str(goal).split())
    verdict = _SECRETS.evaluate(text, stage=GuardStage.OUTPUT)
    if verdict.action == GuardAction.REDACT and verdict.sanitised is not None:
        text = verdict.sanitised
    return text[:PREVIEW_CHARS]


def parse_bound(value: str | None, *, name: str, end: bool = False) -> datetime | None:
    """An ISO-8601 date or datetime (naive means UTC). A bare date as an upper bound covers the
    whole of that day."""
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"{name} must be an ISO-8601 date or timestamp", details={"field": name}
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if end and len(raw) <= 10:
        parsed = parsed + timedelta(days=1)
    return parsed


class RunSearchIndex:
    def __init__(
        self, cache: Any, runs: Any, *, store_preview: bool = False,
        preview_ttl_s: int = 30 * 86_400, scan: int = 300,
    ) -> None:
        self._cache = cache
        self._runs = runs
        self._store_preview = bool(store_preview)
        self._ttl = int(preview_ttl_s)
        self._scan = int(scan)

    @property
    def store_preview(self) -> bool:
        return self._store_preview

    def _key(self, user_id: str, run_id: str) -> str:
        return f"aura:runprev:v1:{owner_digest(user_id)}:{run_id}"

    async def note(self, user_id: UserId | str, run_id: str | None, goal: str) -> None:
        """Keep the preview for one run. A no-op unless the operator opted in; never raises."""
        if not self._store_preview or not run_id or not goal:
            return
        owner = user_id.value if hasattr(user_id, "value") else str(user_id)
        try:
            await self._cache.set(
                self._key(owner, run_id), redact_preview(goal).encode("utf-8"), ttl_s=self._ttl
            )
        except Exception:
            logger.debug("run preview not stored", exc_info=True)

    async def _previews(self, user_id: str, run_ids: Sequence[str]) -> dict[str, str]:
        if not self._store_preview or not run_ids:
            return {}
        keys = {self._key(user_id, rid): rid for rid in run_ids}
        try:
            found = await self._cache.get_many(list(keys))
        except Exception:
            logger.debug("run previews unreadable", exc_info=True)
            return {}
        return {keys[k]: v.decode("utf-8", errors="replace") for k, v in found.items() if k in keys}

    async def _tools(self, user_id: UserId, runs: Sequence[Any]) -> dict[str, list[str]]:
        gate = asyncio.Semaphore(_STEP_CONCURRENCY)

        async def one(run: Any) -> tuple[str, list[str]]:
            async with gate:
                try:
                    steps = await self._runs.list_steps(run.id, user_id=user_id)
                except Exception:
                    logger.debug("run steps unreadable", exc_info=True)
                    return run.id.value, []
            names = OrderedDict((s.tool, None) for s in steps if s.tool)
            return run.id.value, list(names)

        return dict(await asyncio.gather(*(one(r) for r in runs)))

    async def _scan_runs(
        self, user_id: UserId, *, agent: str | None, status: str | None,
        since: datetime | None, until: datetime | None,
    ) -> tuple[list[Any], bool]:
        if self._runs is None:
            return [], False
        rows = list(await self._runs.list_runs(
            user_id, agent_id=agent, status=status, limit=self._scan
        ))
        truncated = len(rows) >= self._scan
        out = [
            r for r in rows
            if (since is None or r.created_at >= since) and (until is None or r.created_at < until)
        ]
        return out, truncated

    def _scope(self, *, scanned: int, truncated: bool, lookups: int, capped: bool,
               with_preview: int) -> dict[str, Any]:
        fields = ["agent", "status", "stop_reason", "exhausted_budget", "run_id", "tools"]
        if self._store_preview:
            fields.append("goal_preview")
        scope: dict[str, Any] = {
            "fields": fields,
            "goal_text_searchable": self._store_preview,
            "scanned_runs": scanned,
            "scan_window": self._scan,
            "scan_truncated": truncated,
            "step_lookups": lookups,
            "step_lookups_capped": capped,
        }
        if self._store_preview:
            scope["runs_with_preview"] = with_preview
            scope["runs_without_preview"] = max(0, scanned - with_preview)
            scope["note"] = (
                "Goal text is matched against a stored 200-character, secret-redacted preview. "
                "Runs from before previews were enabled, or older than their TTL, have none."
            )
        else:
            scope["note"] = (
                "Goal text is not stored (AURA_AGENT_RUNS_STORE_PREVIEW is off), so q matches "
                "agent, status, stop reason, tool names and run id only."
            )
        return scope

    async def search(
        self, user_id: UserId, *, q: str | None = None, agent: str | None = None,
        status: str | None = None, since: datetime | None = None, until: datetime | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        runs, truncated = await self._scan_runs(
            user_id, agent=agent, status=status, since=since, until=until
        )
        owner = user_id.value
        previews = await self._previews(owner, [r.id.value for r in runs])
        tokens = [t for t in re.split(r"\s+", (q or "").strip().lower()) if t]

        lookups = 0
        capped = False
        tools: dict[str, list[str]] = {}
        if tokens:
            # Tool names need the steps; look at the newest runs up to the cap and match the rest
            # on what the run row itself holds.
            to_check = runs[:MAX_STEP_LOOKUPS]
            capped = len(runs) > MAX_STEP_LOOKUPS
            lookups = len(to_check)
            tools = await self._tools(user_id, to_check)

        def haystack(run: Any) -> dict[str, str]:
            parts = {
                "agent": run.agent_id, "status": run.status,
                "stop_reason": run.stop_reason or "", "exhausted_budget": run.exhausted_budget or "",
                "run_id": run.id.value, "tools": " ".join(tools.get(run.id.value, ())),
                "goal_preview": previews.get(run.id.value, ""),
            }
            return {k: v.lower() for k, v in parts.items()}

        matched: list[tuple[Any, list[str]]] = []
        for run in runs:
            if not tokens:
                matched.append((run, []))
                continue
            fields = haystack(run)
            joined = " ".join(fields.values())
            if all(t in joined for t in tokens):
                hit = [name for name, text in fields.items() if any(t in text for t in tokens)]
                matched.append((run, hit))
        page = matched[:limit]
        if not tokens:
            tools = await self._tools(user_id, [r for r, _ in page])
        else:
            missing = [r for r, _ in page if r.id.value not in tools]
            if missing:
                tools.update(await self._tools(user_id, missing))

        return {
            "query": q or "",
            "total_matches": len(matched),
            "runs": [
                {
                    **run.as_dict(),
                    "metrics": run_metrics(run),
                    "tools": tools.get(run.id.value, []),
                    "goal_preview": previews.get(run.id.value),
                    "matched_on": hit,
                }
                for run, hit in page
            ],
            "search_scope": self._scope(
                scanned=len(runs), truncated=truncated, lookups=lookups, capped=capped,
                with_preview=len(previews),
            ),
        }

    async def timeline(
        self, user_id: UserId, *, agent: str | None = None, status: str | None = None,
        since: datetime | None = None, until: datetime | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        stamp = now or datetime.now(timezone.utc)
        lower = since or (stamp - timedelta(days=14))
        runs, truncated = await self._scan_runs(
            user_id, agent=agent, status=status, since=lower, until=until
        )
        previews = await self._previews(user_id.value, [r.id.value for r in runs])
        days: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        for run in sorted(runs, key=lambda r: r.created_at, reverse=True):
            day = run.created_at.astimezone(timezone.utc).date().isoformat()
            bucket = days.setdefault(day, {
                "date": day, "runs": 0, "by_status": {}, "iterations": 0, "plan_steps": 0,
                "tool_calls": 0, "tool_calls_ok": 0, "elapsed_s": 0.0, "tokens_used": 0,
                "items": [],
            })
            bucket["runs"] += 1
            bucket["by_status"][run.status] = bucket["by_status"].get(run.status, 0) + 1
            bucket["iterations"] += run.iterations
            bucket["plan_steps"] += run.plan_steps_total
            bucket["tool_calls"] += run.tool_calls
            bucket["tool_calls_ok"] += run.tool_calls_ok
            bucket["elapsed_s"] += run.elapsed_s
            bucket["tokens_used"] += run.tokens_used
            bucket["items"].append({
                "id": run.id.value, "agent": run.agent_id, "status": run.status,
                "created_at": run.created_at.isoformat(), "elapsed_s": round(run.elapsed_s, 3),
                "iterations": run.iterations, "plan_steps": run.plan_steps_total,
                "tool_calls": run.tool_calls, "goal_preview": previews.get(run.id.value),
            })
        rows = []
        for bucket in days.values():
            bucket["elapsed_s"] = round(bucket["elapsed_s"], 3)
            rows.append(bucket)
        return {
            "since": lower.isoformat(),
            "until": until.isoformat() if until else None,
            "days": rows,
            "totals": {
                "runs": len(runs),
                "plan_steps": sum(r.plan_steps_total for r in runs),
                "tool_calls": sum(r.tool_calls for r in runs),
                "elapsed_s": round(sum(r.elapsed_s for r in runs), 3),
            },
            "search_scope": self._scope(
                scanned=len(runs), truncated=truncated, lookups=0, capped=False,
                with_preview=len(previews),
            ),
        }
