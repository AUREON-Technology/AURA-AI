"""Per-turn usage records and the summaries the dashboard reads (ADR-041, roadmap row 72).

The kernel already computes a turn's tokens, cost and duration (``TurnTelemetry``) and hands them
to metrics, spans and the content-free telemetry sink -- none of which is per *user*. This keeps
one compact record per turn, keyed by the person who ran it, in the cache port:

* no content: ids, names, counts and numbers only (no message, no answer, no goal);
* bounded twice: each user's log is a fixed window of the newest turns, and every entry has a TTL;
* best effort: a cache that is down costs the dashboard a data point, never the user's turn --
  :meth:`UsageLedger.record` swallows its own failures.

A summary is computed from that window on read. When the window does not reach back to the start
of the requested period the response says so (``truncated``) rather than presenting a partial sum
as a total.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ..domain.errors import ValidationError
from ..observability.logging import get_logger
from .cache_log import CacheLog

__all__ = ["UsageLedger", "summarize_usage", "PERIODS", "GROUP_BYS"]

logger = get_logger("history.usage")

#: Trailing windows, in days. Not calendar buckets: "week" is the last seven days, so the number
#: does not drop to zero every Monday morning.
PERIODS: Mapping[str, int] = {"day": 1, "week": 7, "month": 30}
GROUP_BYS = ("day", "agent", "task")

_NO_TASK = "(none)"


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _blank() -> dict[str, Any]:
    return {
        "turns": 0, "failed_turns": 0, "cached_turns": 0,
        "tokens_in": 0, "tokens_out": 0, "total_tokens": 0,
        "cost_usd": 0.0, "elapsed_s": 0.0,
    }


def _add(bucket: dict[str, Any], record: Mapping[str, Any]) -> None:
    bucket["turns"] += 1
    if record.get("outcome") != "success":
        bucket["failed_turns"] += 1
    if record.get("cached"):
        bucket["cached_turns"] += 1
    tin = int(record.get("prompt_tokens") or 0)
    tout = int(record.get("completion_tokens") or 0)
    bucket["tokens_in"] += tin
    bucket["tokens_out"] += tout
    bucket["total_tokens"] += tin + tout
    bucket["cost_usd"] += float(record.get("cost_usd") or 0.0)
    bucket["elapsed_s"] += float(record.get("total_ms") or 0.0) / 1000.0


def _finish(bucket: dict[str, Any]) -> dict[str, Any]:
    bucket["cost_usd"] = round(bucket["cost_usd"], 6)
    bucket["elapsed_s"] = round(bucket["elapsed_s"], 3)
    return bucket


def _group_key(record: Mapping[str, Any], group_by: str, ts: datetime) -> str:
    if group_by == "day":
        return ts.astimezone(timezone.utc).date().isoformat()
    if group_by == "agent":
        return str(record.get("agent") or "unknown")
    return str(record.get("task_id") or _NO_TASK)


def summarize_usage(
    records: Iterable[Mapping[str, Any]], *, period: str, group_by: str, now: datetime,
) -> dict[str, Any]:
    """Totals and per-group totals for the trailing ``period``; pure, so it is tested directly."""
    if period not in PERIODS:
        raise ValidationError("period must be day, week or month", details={"field": "period"})
    if group_by not in GROUP_BYS:
        raise ValidationError("group_by must be day, agent or task", details={"field": "group_by"})
    until = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    since = until - timedelta(days=PERIODS[period])
    totals = _blank()
    groups: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for record in records:
        ts = _parse(record.get("ts"))
        if ts is None or ts < since or ts > until + timedelta(minutes=5):
            continue
        _add(totals, record)
        bucket = groups.setdefault(_group_key(record, group_by, ts), _blank())
        _add(bucket, record)
    rows = [{"key": key, **_finish(bucket)} for key, bucket in groups.items()]
    # Days read oldest-first (a chart's x axis); agents and tasks read heaviest-first.
    if group_by == "day":
        rows.sort(key=lambda r: r["key"])
    else:
        rows.sort(key=lambda r: (-r["cost_usd"], -r["total_tokens"], r["key"]))
    return {
        "period": period,
        "group_by": group_by,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "currency": "USD",
        "totals": _finish(totals),
        "groups": rows,
    }


class UsageLedger:
    """Writes and reads one compact usage record per turn."""

    def __init__(self, cache: Any, *, enabled: bool = True, ttl_days: int = 35,
                 max_per_user: int = 5000, clock: Any = None) -> None:
        self._enabled = enabled
        self._clock = clock
        self._ttl_days = ttl_days
        self._log = CacheLog(
            cache, namespace="aura:usage:v1", ttl_s=ttl_days * 86_400, window=max_per_user
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def window(self) -> int:
        return self._log.window

    @property
    def retention_days(self) -> int:
        return self._ttl_days

    def _now(self) -> datetime:
        if self._clock is not None and hasattr(self._clock, "now"):
            return self._clock.now()
        return datetime.now(timezone.utc)

    async def record(self, user_id: str, telemetry: Any, *, task_id: str | None = None) -> None:
        """Keep one turn. Never raises. A failed turn that spent nothing is not kept: a client
        retrying a refused request in a loop must not push real turns out of the window."""
        if not self._enabled or not user_id:
            return
        try:
            tokens = int(telemetry.prompt_tokens) + int(telemetry.completion_tokens)
            outcome = str(telemetry.outcome)
            if outcome != "success" and tokens == 0 and not float(telemetry.cost_usd):
                return
            started = telemetry.started_at
            entry: dict[str, Any] = {
                "turn_id": telemetry.turn_id.value,
                "ts": started.isoformat() if started is not None else self._now().isoformat(),
                "agent": telemetry.agent,
                "route": telemetry.route,
                "outcome": outcome,
                "error_kind": telemetry.error_kind,
                "provider": telemetry.provider,
                "model": telemetry.model,
                "prompt_tokens": int(telemetry.prompt_tokens),
                "completion_tokens": int(telemetry.completion_tokens),
                "cost_usd": round(float(telemetry.cost_usd), 6),
                "total_ms": round(float(telemetry.total_ms), 1),
                "cached": bool(telemetry.cache.hit),
                "task_id": task_id,
            }
            await self._log.append(user_id, entry)
        except Exception:
            logger.debug("usage record not stored", exc_info=True)

    async def turns(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        try:
            return await self._log.recent(user_id, limit)
        except Exception:
            logger.warning("usage log unreadable", exc_info=True)
            return []

    async def summary(self, user_id: str, *, period: str, group_by: str) -> dict[str, Any]:
        now = self._now()
        try:
            records = await self._log.recent(user_id)
        except Exception:
            logger.warning("usage log unreadable", exc_info=True)
            records = []
        body = summarize_usage(records, period=period, group_by=group_by, now=now)
        oldest = _parse(records[-1].get("ts")) if records else None
        body["truncated"] = bool(
            len(records) >= self._log.window and oldest is not None
            and oldest > now - timedelta(days=PERIODS[period])
        )
        body["window_turns"] = self._log.window
        body["retention_days"] = self._ttl_days
        body["source"] = "usage_ledger"
        return body
