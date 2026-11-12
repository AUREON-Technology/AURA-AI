"""Postgres implementation of the ``TelemetrySink`` port (Phase G1).

Before this phase, ``KernelDependencies.telemetry_sink`` was wired nowhere: every turn built a
complete :class:`~aura_core.domain.telemetry.TurnTelemetry` record and nothing kept it past the
request that produced it, beyond a Prometheus counter or two. The roadmap's own scorecard could
therefore never be more than hand-maintained numbers -- "a live dashboard fed by real evaluation
runs and ``TurnTelemetry``" needs something to feed *from*. This is that something: a real,
queryable history, in its own aura_core-owned table (``CREATE TABLE IF NOT EXISTS``, the same
self-managed-schema pattern as ``postgres_audit.py`` and ``postgres_outbox.py``).

Flat columns carry what the scorecard aggregates over; ``payload`` keeps the full
``TurnTelemetry.as_dict()`` for anything this file's own aggregation queries do not anticipate.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..domain.errors import DependencyUnavailableError
from ..domain.telemetry import TurnTelemetry
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresTelemetrySink"]


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


class PostgresTelemetrySink:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def record_turn(self, telemetry: TurnTelemetry) -> None:
        pool = await self._pool._get_pool()
        payload = telemetry.as_dict()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_turn_telemetry (
                        turn_id, correlation_id, started_at, route, agent, outcome, error_kind,
                        provider, model, total_ms, prompt_tokens, completion_tokens, cost_usd,
                        degraded, cache_hit, tool_calls, tool_failures, groundedness, payload
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::jsonb)
                    ON CONFLICT (turn_id) DO NOTHING
                    """,
                    telemetry.turn_id.value, telemetry.correlation_id.value, telemetry.started_at,
                    telemetry.route, telemetry.agent, str(telemetry.outcome), telemetry.error_kind,
                    telemetry.provider, telemetry.model, telemetry.total_ms,
                    telemetry.prompt_tokens, telemetry.completion_tokens, telemetry.cost_usd,
                    telemetry.degraded, telemetry.cache.hit,
                    len(telemetry.tools), sum(1 for t in telemetry.tools if not t.ok),
                    telemetry.groundedness, json.dumps(payload),
                )
        except Exception as exc:
            # Telemetry must never fail the turn it describes -- log and move on, the same
            # reasoning InMemoryEventBus uses for a handler exception.
            logger.warning("could not record turn telemetry: %s", exc)

    async def purge_before(self, cutoff: datetime) -> int:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute("DELETE FROM aura_turn_telemetry WHERE started_at < $1", cutoff)
        except Exception as exc:
            raise _unavailable(f"could not purge turn telemetry: {exc}") from exc
        # asyncpg reports the command tag ("DELETE 3"); the count is its last word.
        return int(str(status).rsplit(" ", 1)[-1] or 0)

    async def purge_sessions(self, session_ids: Any) -> int:
        doomed = [str(s) for s in session_ids]
        if not doomed:
            return 0
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                status = await conn.execute(
                    "DELETE FROM aura_turn_telemetry WHERE payload->>'session_id' = ANY($1::text[])", doomed
                )
        except Exception as exc:
            raise _unavailable(f"could not purge turn telemetry: {exc}") from exc
        # asyncpg reports the command tag ("DELETE 3"); the count is its last word.
        return int(str(status).rsplit(" ", 1)[-1] or 0)

    async def recent_summary(self, *, since: datetime) -> dict[str, Any]:
        """Real aggregate numbers over every turn recorded since ``since`` -- the query the
        scorecard endpoint reads from, not a hand-maintained figure."""
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        count(*) AS turns,
                        avg(CASE WHEN outcome != 'success' THEN 1.0 ELSE 0.0 END) AS error_rate,
                        avg(CASE WHEN degraded THEN 1.0 ELSE 0.0 END) AS degraded_rate,
                        avg(CASE WHEN cache_hit THEN 1.0 ELSE 0.0 END) AS cache_hit_rate,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) AS p50_latency_ms,
                        percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) AS p95_latency_ms,
                        avg(prompt_tokens) AS avg_prompt_tokens,
                        avg(completion_tokens) AS avg_completion_tokens,
                        avg(cost_usd) AS avg_cost_usd,
                        sum(tool_calls) AS tool_calls,
                        sum(tool_failures) AS tool_failures,
                        avg(groundedness) AS avg_groundedness
                    FROM aura_turn_telemetry
                    WHERE started_at >= $1
                    """,
                    since,
                )
        except Exception as exc:
            raise _unavailable(f"could not summarise turn telemetry: {exc}") from exc

        turns = row["turns"] or 0
        tool_calls = row["tool_calls"] or 0
        tool_failures = row["tool_failures"] or 0
        return {
            "turns": turns,
            "since": since.astimezone(timezone.utc).isoformat(),
            "error_rate": _r(row["error_rate"]),
            "degraded_rate": _r(row["degraded_rate"]),
            "cache_hit_rate": _r(row["cache_hit_rate"]),
            "p50_latency_ms": _r(row["p50_latency_ms"]),
            "p95_latency_ms": _r(row["p95_latency_ms"]),
            "avg_prompt_tokens": _r(row["avg_prompt_tokens"]),
            "avg_completion_tokens": _r(row["avg_completion_tokens"]),
            "avg_cost_usd": _r(row["avg_cost_usd"], digits=6),
            "tool_success_rate": (
                round(1 - tool_failures / tool_calls, 4) if tool_calls else None
            ),
            "avg_groundedness": _r(row["avg_groundedness"]),
        }


def _r(value: Any, *, digits: int = 2) -> float | None:
    """``avg``/``percentile_cont`` come back from asyncpg as ``Decimal``, not ``float`` -- cast
    before rounding so the response is plain-JSON-serialisable (``json.dumps`` cannot encode a
    ``Decimal`` on its own) and shaped identically to :class:`InMemoryTelemetrySink`'s output."""
    return round(float(value), digits) if value is not None else None
