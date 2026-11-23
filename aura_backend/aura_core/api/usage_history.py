"""Usage dashboard and run-history routes (ADR-041, roadmap rows 72 and 71).

* ``GET /api/v3/usage/summary`` and ``GET /api/v3/usage/turns`` -- the caller's own tokens, cost
  and time, from the per-turn records the kernel keeps (``history/usage.py``).
* ``GET /api/v3/agents/runs/search`` and ``GET /api/v3/agents/runs/timeline`` -- search and a
  day-grouped history over the caller's own agent runs (``history/run_search.py``).

Every read is scoped to ``caller.user_id`` by construction: the owner is an argument of each store
call, never a filter applied afterwards, so another person's data cannot be named by any parameter.
The runs routes are registered *before* ``GET /agents/runs/{run_id}`` in ``v3.py`` -- a path
parameter would otherwise swallow ``search`` and ``timeline`` as run ids.
"""

# No `from __future__ import annotations`: FastAPI resolves handler annotations at decoration time.
from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.identifiers import UserId

__all__ = ["register_usage_history"]


def register_usage_history(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    enforce_rate_limit: Callable,
    Depends: Any,
    JSONResponse: Any,
    Query: Any,
    Request: Any,
) -> None:
    from ..history.run_search import parse_bound

    def _now() -> datetime:
        clock = getattr(container, "clock", None)
        return clock.now() if clock is not None and hasattr(clock, "now") else datetime.now(timezone.utc)

    @router.get("/usage/summary")
    async def usage_summary(
        request: Request,
        caller: Any = Depends(principal),
        period: str = Query(default="week", pattern="^(day|week|month)$"),
        group_by: str = Query(default="day", pattern="^(day|agent|task)$"),
    ) -> JSONResponse:
        """Totals of tokens in/out, cost, elapsed time and turns over the trailing ``period``
        (``day`` = 24 h, ``week`` = 7 days, ``month`` = 30 days), plus the same per ``group_by``
        (UTC ``day``, ``agent`` persona, or agent ``task``; turns outside any task group under
        ``(none)``).

        Read from the usage records the kernel keeps per turn (cache port, TTL'd, newest
        ``window_turns`` per user). ``truncated`` is true when that window does not reach back to
        the start of the period, in which case the totals are a floor, not the total."""
        await enforce_rate_limit(request, caller, "read")
        ledger = getattr(container, "usage_ledger", None)
        if ledger is None or not ledger.enabled:
            return JSONResponse(content={
                "enabled": False, "period": period, "group_by": group_by,
                "totals": None, "groups": [], "source": "usage_ledger",
                "note": "usage recording is off on this deployment (AURA_USAGE_LEDGER_ENABLED)",
            })
        body = await ledger.summary(caller.user_id.value, period=period, group_by=group_by)
        return JSONResponse(content={"enabled": True, **body})

    @router.get("/usage/turns")
    async def usage_turns(
        request: Request,
        caller: Any = Depends(principal),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> JSONResponse:
        """The caller's most recent turns with their usage, newest first. No message or answer
        text: ids, persona, provider/model, token counts, cost, duration and outcome."""
        await enforce_rate_limit(request, caller, "read")
        ledger = getattr(container, "usage_ledger", None)
        if ledger is None or not ledger.enabled:
            return JSONResponse(content={"enabled": False, "turns": []})
        turns = await ledger.turns(caller.user_id.value, limit=limit)
        return JSONResponse(content={
            "enabled": True,
            "turns": [
                {
                    "turn_id": t.get("turn_id"), "at": t.get("ts"), "agent": t.get("agent"),
                    "route": t.get("route"), "outcome": t.get("outcome"),
                    "error_kind": t.get("error_kind"), "provider": t.get("provider"),
                    "model": t.get("model"), "task_id": t.get("task_id"),
                    "cached": bool(t.get("cached")),
                    "usage": {
                        "prompt_tokens": int(t.get("prompt_tokens") or 0),
                        "completion_tokens": int(t.get("completion_tokens") or 0),
                        "cost_usd": float(t.get("cost_usd") or 0.0),
                        "elapsed_ms": float(t.get("total_ms") or 0.0),
                    },
                }
                for t in turns
            ],
            "window_turns": ledger.window,
            "retention_days": ledger.retention_days,
        })

    @router.get("/agents/runs/search")
    async def search_agent_runs(
        request: Request,
        caller: Any = Depends(principal),
        q: str | None = Query(default=None, max_length=200),
        agent: str | None = Query(default=None, max_length=64),
        status: str | None = Query(default=None, max_length=32),
        since: str | None = Query(default=None, max_length=40),
        until: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> JSONResponse:
        """Search the caller's own agent runs. ``q`` is a space-separated AND of words matched
        against agent, status, stop reason, tool names, run id and -- only when the operator
        enabled ``AURA_AGENT_RUNS_STORE_PREVIEW`` -- a stored 200-character redacted preview of
        the goal. ``search_scope`` in the response states exactly what was searched; with
        previews off, goal text is *not* searchable and the response says so. ``since`` and
        ``until`` are ISO-8601 dates or timestamps (a bare ``until`` date includes that day)."""
        await enforce_rate_limit(request, caller, "read")
        index = getattr(container, "run_index", None)
        if index is None or getattr(container, "agent_runs", None) is None:
            return JSONResponse(content={
                "query": q or "", "total_matches": 0, "runs": [],
                "search_scope": {"fields": [], "goal_text_searchable": False,
                                 "note": "agent run history is not recorded on this deployment"},
            })
        body = await index.search(
            UserId(caller.user_id.value), q=q, agent=agent, status=status,
            since=parse_bound(since, name="since"), until=parse_bound(until, name="until", end=True),
            limit=limit,
        )
        return JSONResponse(content=body)

    @router.get("/agents/runs/timeline")
    async def agent_runs_timeline(
        request: Request,
        caller: Any = Depends(principal),
        agent: str | None = Query(default=None, max_length=64),
        status: str | None = Query(default=None, max_length=32),
        since: str | None = Query(default=None, max_length=40),
        until: str | None = Query(default=None, max_length=40),
    ) -> JSONResponse:
        """The caller's agent runs grouped by UTC day, newest day first, each day with run count,
        counts by status, plan-step and tool-call counts, iterations, tokens and elapsed seconds,
        and the day's runs (id, agent, status, time, duration; ``goal_preview`` only when the
        operator enabled previews). Defaults to the last 14 days."""
        await enforce_rate_limit(request, caller, "read")
        index = getattr(container, "run_index", None)
        if index is None or getattr(container, "agent_runs", None) is None:
            return JSONResponse(content={"days": [], "totals": {"runs": 0}, "search_scope": {}})
        body = await index.timeline(
            UserId(caller.user_id.value), agent=agent, status=status,
            since=parse_bound(since, name="since"), until=parse_bound(until, name="until", end=True),
            now=_now(),
        )
        return JSONResponse(content=body)
