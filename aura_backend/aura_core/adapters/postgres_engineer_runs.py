"""Postgres implementation of the ``EngineerRunStore`` port, and of the ``AgentRunStore``
extension that generalises it to every persona's run (GAP-024).

Its own table, its own schema management, sharing only the connection pool -- the same pattern
``postgres_personal_os.py`` uses. See ``aura_core/domain/engineering.py`` for why this is a
plaintext, server-owned table rather than an extension of the zero-knowledge-encrypted legacy
tables.

Deliberately its own table (``aura_engineer_runs``), not a row in the v2 ``SYNCABLE_MODELS``
schema (``infrastructure/db/models.py``): those tables key ``user_id`` to the v2 relational
``users.id`` UUID, while every ``aura_core`` domain object (this one included) is keyed by the
`UserId` value object's string form (the auth-subject claim) -- the same reason
``PersonalOsRecord`` isn't in ``SYNCABLE_MODELS`` either. The client reads this data through a
dedicated endpoint (``GET /api/v3/engineering/runs``) instead.

Agent runs (``aura_agent_runs``) and their steps (``aura_agent_steps``) live beside it in the same
adapter -- same pool, same lazy schema bootstrap -- and are mirrored by Alembic revision
``0007_agent_runs``, kept textually in step with :data:`AGENT_RUNS_SCHEMA` below so that a gateway
that already created the tables on first use does not make ``alembic upgrade head`` fail. Every
read filters on ``user_id`` in SQL, not after the fetch.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.agent_runs import AgentBudget, AgentRun, AgentStep
from ..domain.engineering import EngineerRun
from ..domain.errors import DependencyUnavailableError
from ..domain.identifiers import AgentRunId, AgentStepId, EngineerRunId, UserId
from .postgres_store import PostgresPool

logger = logging.getLogger(__name__)

__all__ = ["PostgresEngineerRunStore", "PostgresAgentRunStore", "AGENT_RUNS_SCHEMA"]

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_engineer_runs (
        id                  TEXT        PRIMARY KEY,
        user_id             TEXT        NOT NULL,
        turn_id             TEXT        NOT NULL,
        correlation_id      TEXT        NOT NULL,
        persona             TEXT        NOT NULL,
        tool                TEXT        NOT NULL,
        call_id             TEXT        NOT NULL,
        status              TEXT        NOT NULL,
        pr_url              TEXT        NULL,
        branch              TEXT        NULL,
        commit_sha          TEXT        NULL,
        sandbox_transcript  TEXT        NULL,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # The shape `list_for_user` asks for: this user, optionally since some time, newest first.
    """
    CREATE INDEX IF NOT EXISTS ix_aura_engineer_runs_user_created
        ON aura_engineer_runs (user_id, created_at DESC)
    """,
)


#: Mirrored by ``alembic/versions/0007_agent_runs.py`` -- keep the two in step.
AGENT_RUNS_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS aura_agent_runs (
        id                   TEXT             PRIMARY KEY,
        user_id              TEXT             NOT NULL,
        agent_id             TEXT             NOT NULL,
        turn_id              TEXT             NOT NULL,
        correlation_id       TEXT             NOT NULL,
        status               TEXT             NOT NULL,
        goal_digest          TEXT             NOT NULL DEFAULT '',
        goal_chars           INTEGER          NOT NULL DEFAULT 0,
        budget               JSONB            NOT NULL DEFAULT '{}'::jsonb,
        iterations           INTEGER          NOT NULL DEFAULT 0,
        replans              INTEGER          NOT NULL DEFAULT 0,
        replans_recovered    INTEGER          NOT NULL DEFAULT 0,
        tokens_used          INTEGER          NOT NULL DEFAULT 0,
        tool_calls           INTEGER          NOT NULL DEFAULT 0,
        tool_calls_ok        INTEGER          NOT NULL DEFAULT 0,
        risk_spent           INTEGER          NOT NULL DEFAULT 0,
        elapsed_s            DOUBLE PRECISION NOT NULL DEFAULT 0,
        unsafe_prevented     INTEGER          NOT NULL DEFAULT 0,
        plan_steps_total     INTEGER          NOT NULL DEFAULT 0,
        plan_steps_succeeded INTEGER          NOT NULL DEFAULT 0,
        stop_reason          TEXT             NULL,
        exhausted_budget     TEXT             NULL,
        created_at           TIMESTAMPTZ      NOT NULL,
        updated_at           TIMESTAMPTZ      NOT NULL,
        finished_at          TIMESTAMPTZ      NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_aura_agent_runs_user_created
        ON aura_agent_runs (user_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS aura_agent_steps (
        id                TEXT        PRIMARY KEY,
        run_id            TEXT        NOT NULL REFERENCES aura_agent_runs (id) ON DELETE CASCADE,
        user_id           TEXT        NOT NULL,
        seq               INTEGER     NOT NULL,
        iteration         INTEGER     NOT NULL,
        phase             TEXT        NOT NULL,
        kind              TEXT        NOT NULL,
        status            TEXT        NOT NULL DEFAULT '',
        tool              TEXT        NULL,
        call_id           TEXT        NULL,
        error_kind        TEXT        NULL,
        arguments_digest  TEXT        NULL,
        risk_cost         INTEGER     NOT NULL DEFAULT 0,
        detail            JSONB       NOT NULL DEFAULT '{}'::jsonb,
        created_at        TIMESTAMPTZ NOT NULL,
        UNIQUE (run_id, seq)
    )
    """,
)


def _json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return dict(json.loads(value))
    return dict(value)


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PostgresEngineerRunStore:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                for statement in (*_SCHEMA, *AGENT_RUNS_SCHEMA):
                    await conn.execute(statement)
        except Exception as exc:
            raise _unavailable(f"could not create the engineer-runs schema: {exc}") from exc
        self._schema_ready = True

    @staticmethod
    def _run(row: Mapping[str, Any]) -> EngineerRun:
        return EngineerRun(
            id=EngineerRunId(row["id"]),
            user_id=UserId(row["user_id"]),
            turn_id=row["turn_id"],
            correlation_id=row["correlation_id"],
            persona=row["persona"],
            tool=row["tool"],
            call_id=row["call_id"],
            status=row["status"],
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
            pr_url=row["pr_url"],
            branch=row["branch"],
            commit_sha=row["commit_sha"],
            sandbox_transcript=row["sandbox_transcript"],
        )

    async def record(self, run: EngineerRun) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_engineer_runs
                        (id, user_id, turn_id, correlation_id, persona, tool, call_id, status,
                         pr_url, branch, commit_sha, sandbox_transcript, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status, pr_url = EXCLUDED.pr_url,
                        branch = EXCLUDED.branch, commit_sha = EXCLUDED.commit_sha,
                        sandbox_transcript = EXCLUDED.sandbox_transcript,
                        updated_at = EXCLUDED.updated_at
                    """,
                    run.id.value, run.user_id.value, run.turn_id, run.correlation_id,
                    run.persona, run.tool, run.call_id, run.status,
                    run.pr_url, run.branch, run.commit_sha, run.sandbox_transcript,
                    run.created_at, run.updated_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not write engineer run: {exc}") from exc

    async def list_for_user(
        self, user_id: UserId, *, since: datetime | None = None, limit: int = 200,
    ) -> Sequence[EngineerRun]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                if since is not None:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_engineer_runs
                        WHERE user_id = $1 AND created_at >= $2
                        ORDER BY created_at DESC LIMIT $3
                        """,
                        user_id.value, since, limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM aura_engineer_runs
                        WHERE user_id = $1
                        ORDER BY created_at DESC LIMIT $2
                        """,
                        user_id.value, limit,
                    )
        except Exception as exc:
            raise _unavailable(f"could not list engineer runs: {exc}") from exc
        return [self._run(row) for row in rows]

    # -- agent runs (any persona, GAP-024) -------------------------------------------------

    @staticmethod
    def _agent_run(row: Mapping[str, Any]) -> AgentRun:
        budget = _json(row["budget"])
        return AgentRun(
            id=AgentRunId(row["id"]),
            user_id=UserId(row["user_id"]),
            agent_id=row["agent_id"],
            turn_id=row["turn_id"],
            correlation_id=row["correlation_id"],
            status=row["status"],
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
            goal_digest=row["goal_digest"],
            goal_chars=int(row["goal_chars"]),
            budget=AgentBudget(
                max_iterations=int(budget.get("max_iterations") or 0),
                token_budget=budget.get("token_budget"),
                time_budget_s=budget.get("time_budget_s"),
                tool_budget=budget.get("tool_budget"),
                risk_budget=budget.get("risk_budget"),
            ),
            iterations=int(row["iterations"]),
            replans=int(row["replans"]),
            replans_recovered=int(row["replans_recovered"]),
            tokens_used=int(row["tokens_used"]),
            tool_calls=int(row["tool_calls"]),
            tool_calls_ok=int(row["tool_calls_ok"]),
            risk_spent=int(row["risk_spent"]),
            # float(): a DOUBLE PRECISION column comes back as float already, but an aggregate
            # or a NUMERIC migration would hand back Decimal -- normalise at the boundary.
            elapsed_s=float(row["elapsed_s"]),
            unsafe_prevented=int(row["unsafe_prevented"]),
            plan_steps_total=int(row["plan_steps_total"]),
            plan_steps_succeeded=int(row["plan_steps_succeeded"]),
            stop_reason=row["stop_reason"],
            exhausted_budget=row["exhausted_budget"],
            finished_at=_utc(row["finished_at"]),
        )

    @staticmethod
    def _agent_step(row: Mapping[str, Any]) -> AgentStep:
        return AgentStep(
            id=AgentStepId(row["id"]),
            run_id=AgentRunId(row["run_id"]),
            user_id=UserId(row["user_id"]),
            seq=int(row["seq"]),
            iteration=int(row["iteration"]),
            phase=row["phase"],
            kind=row["kind"],
            created_at=_utc(row["created_at"]),
            status=row["status"],
            tool=row["tool"],
            call_id=row["call_id"],
            error_kind=row["error_kind"],
            arguments_digest=row["arguments_digest"],
            risk_cost=int(row["risk_cost"]),
            detail=_json(row["detail"]),
        )

    async def save_run(self, run: AgentRun) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    """
                    INSERT INTO aura_agent_runs
                        (id, user_id, agent_id, turn_id, correlation_id, status, goal_digest,
                         goal_chars, budget, iterations, replans, replans_recovered, tokens_used,
                         tool_calls, tool_calls_ok, risk_spent, elapsed_s, unsafe_prevented,
                         plan_steps_total, plan_steps_succeeded, stop_reason, exhausted_budget,
                         created_at, updated_at, finished_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13, $14,
                            $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status, budget = EXCLUDED.budget,
                        iterations = EXCLUDED.iterations, replans = EXCLUDED.replans,
                        replans_recovered = EXCLUDED.replans_recovered,
                        tokens_used = EXCLUDED.tokens_used, tool_calls = EXCLUDED.tool_calls,
                        tool_calls_ok = EXCLUDED.tool_calls_ok, risk_spent = EXCLUDED.risk_spent,
                        elapsed_s = EXCLUDED.elapsed_s,
                        unsafe_prevented = EXCLUDED.unsafe_prevented,
                        plan_steps_total = EXCLUDED.plan_steps_total,
                        plan_steps_succeeded = EXCLUDED.plan_steps_succeeded,
                        stop_reason = EXCLUDED.stop_reason,
                        exhausted_budget = EXCLUDED.exhausted_budget,
                        updated_at = EXCLUDED.updated_at, finished_at = EXCLUDED.finished_at
                    WHERE aura_agent_runs.user_id = EXCLUDED.user_id
                    """,
                    run.id.value, run.user_id.value, run.agent_id, run.turn_id,
                    run.correlation_id, run.status, run.goal_digest, run.goal_chars,
                    json.dumps(run.budget.as_dict()), run.iterations, run.replans,
                    run.replans_recovered, run.tokens_used, run.tool_calls, run.tool_calls_ok,
                    run.risk_spent, float(run.elapsed_s), run.unsafe_prevented,
                    run.plan_steps_total, run.plan_steps_succeeded, run.stop_reason,
                    run.exhausted_budget, run.created_at, run.updated_at, run.finished_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not write agent run: {exc}") from exc
        # The conflict clause's WHERE makes an upsert from a different user a no-op ("INSERT 0
        # 0"), never a cross-user overwrite; surface it the same way the in-memory store does.
        if str(result).strip().endswith(" 0"):
            raise ValueError("agent run id already belongs to another user")

    async def append_step(self, step: AgentStep) -> None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aura_agent_steps
                        (id, run_id, user_id, seq, iteration, phase, kind, status, tool, call_id,
                         error_kind, arguments_digest, risk_cost, detail, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb,
                            $15)
                    """,
                    step.id.value, step.run_id.value, step.user_id.value, step.seq,
                    step.iteration, step.phase, step.kind, step.status, step.tool, step.call_id,
                    step.error_kind, step.arguments_digest, step.risk_cost,
                    json.dumps(dict(step.detail)), step.created_at,
                )
        except Exception as exc:
            raise _unavailable(f"could not write agent step: {exc}") from exc

    async def get_run(self, run_id: AgentRunId, *, user_id: UserId) -> AgentRun | None:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM aura_agent_runs WHERE id = $1 AND user_id = $2",
                    run_id.value, user_id.value,
                )
        except Exception as exc:
            raise _unavailable(f"could not read agent run: {exc}") from exc
        return self._agent_run(row) if row is not None else None

    async def list_runs(
        self,
        user_id: UserId,
        *,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> Sequence[AgentRun]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM aura_agent_runs
                    WHERE user_id = $1
                      AND ($2::text IS NULL OR agent_id = $2::text)
                      AND ($3::text IS NULL OR status = $3::text)
                    ORDER BY created_at DESC, id DESC
                    LIMIT $4
                    """,
                    user_id.value, agent_id, status, limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not list agent runs: {exc}") from exc
        return [self._agent_run(row) for row in rows]

    async def list_steps(
        self, run_id: AgentRunId, *, user_id: UserId, limit: int = 500,
    ) -> Sequence[AgentStep]:
        await self._ensure_schema()
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT s.* FROM aura_agent_steps s
                    JOIN aura_agent_runs r ON r.id = s.run_id
                    WHERE s.run_id = $1 AND r.user_id = $2 AND s.user_id = $2
                    ORDER BY s.seq ASC
                    LIMIT $3
                    """,
                    run_id.value, user_id.value, limit,
                )
        except Exception as exc:
            raise _unavailable(f"could not list agent steps: {exc}") from exc
        return [self._agent_step(row) for row in rows]


#: The same adapter under the name of the port it now fully implements.
PostgresAgentRunStore = PostgresEngineerRunStore

