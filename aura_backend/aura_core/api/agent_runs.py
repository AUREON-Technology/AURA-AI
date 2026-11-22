"""Agent runs as tasks: status, logs, cancel and a paged, filtered history (``/api/v3/agents/runs``).

An agent "task" here is an ``AgentRun`` (``aura_core/domain/agent_runs.py``): the durable record of
one bounded plan-execute loop, written as a chat turn plans. There is no separate "create" or
"start": a run exists because a turn ran, and it runs inside that request. What this adds around
the existing ``GET /agents/runs`` and ``GET /agents/runs/{id}``::

    GET  /agents/runs                  paged (cursor), filtered (agent, status, since, until, retry_of)
    GET  /agents/runs/{id}/status      a small, cheap answer for polling
    GET  /agents/runs/{id}/logs        the run's steps after ``after_seq``, for following a live run
    POST /agents/runs/{id}/cancel      ask a live run to stop (cooperative, at the next step boundary)

and ``retry_of`` on ``/chat``, which links a new run to the one it retries.

**Logs are structured and carry no content.** A step is a phase, a kind, a tool name, a status, an
error kind and a digest of the arguments (``domain/agent_runs.py``'s content rule): never the
user's words or the model's text. That is a property of the ledger, not something this route
chose, and it is why a log line cannot leak a prompt.

**Cancel is cooperative.** It stops *further* work: the run's budget gate refuses the next tool
dispatch and the loop ends at the next pass boundary. A model call or tool already running
finishes first. The turn then answers with what completed; a client that wants the stream gone
also closes it. A run still ``running`` that no worker has touched for ``STALE_RUN_S`` was
orphaned (its worker died): cancelling it closes it directly instead of waiting for a poll that
will never come.

**Ownership.** Every read and write takes the caller's user id; another person's run id is
indistinguishable from one that does not exist (404). A service key has no runs.

No ``from __future__ import annotations``: ``register_run_ops`` defines nothing FastAPI must
resolve by name, but the module follows ``agents.py`` so the two cannot differ.
"""

import base64
import binascii
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..domain.agent_runs import AgentRunStatus
from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..domain.identifiers import AgentRunId

#: A ``running`` run untouched for this long has lost its worker. The loop saves on every step and
#: its own deadline defaults to 600 s, so a live run is rewritten far more often than this.
STALE_RUN_S = 900
DEFAULT_LOGS_LIMIT = 200
MAX_LOGS_LIMIT = 500

__all__ = [
    "STALE_RUN_S",
    "decode_run_cursor",
    "encode_run_cursor",
    "register_run_ops",
    "run_list_fingerprint",
]


def run_list_fingerprint(
    *, agent: str | None, status: str | None, since: datetime | None, until: datetime | None,
    retry_of: str | None,
) -> dict[str, Any]:
    """The list parameters a cursor is only valid for."""
    return {"agent": agent, "status": status, "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None, "retry_of": retry_of}


def encode_run_cursor(fingerprint: dict[str, Any], *, created_at: datetime, run_id: str) -> str:
    raw = json.dumps({"q": fingerprint, "t": created_at.isoformat(), "i": run_id},
                     separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_run_cursor(cursor: str, fingerprint: dict[str, Any]) -> tuple[datetime, str]:
    """The ``(created_at, id)`` position a cursor names, refused unless it was made for *these*
    list parameters: resuming a different list at that position would skip or repeat runs."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(data, dict) or data.get("q") != fingerprint:
            raise ValueError("parameters differ")
        if not isinstance(data.get("i"), str) or not data["i"]:
            raise ValueError("malformed")
        at = datetime.fromisoformat(data["t"])
        return at, data["i"]
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeDecodeError):
        raise ValidationError(
            "not a cursor for this list; start again without one", details={"field": "cursor"},
        ) from None


def register_run_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    idempotent: list,
    Depends: Any,
    JSONResponse: Any,
    enforce_rate_limit: Callable,
    Request: Any,
) -> None:
    from fastapi import Query

    def store() -> Any:
        found = getattr(container, "agent_runs", None)
        if found is None:
            raise NotFoundError("agent runs are not recorded on this deployment")
        return found

    def run_id_of(value: str) -> AgentRunId:
        try:
            return AgentRunId(value)
        except ValueError:
            raise NotFoundError("run not found", details={"run_id": value}) from None

    async def owned_run(caller: Any, run_id: str) -> Any:
        """The caller's run, or the one 404 that also answers another person's id."""
        if caller.is_service:
            raise NotFoundError("run not found", details={"run_id": run_id})
        run = await store().get_run(run_id_of(run_id), user_id=caller.user_id)
        if run is None:
            raise NotFoundError("run not found", details={"run_id": run_id})
        return run

    @router.get("/agents/runs/{run_id}/status")
    async def run_status(request: Request, run_id: str, caller: Any = Depends(principal)) -> Any:
        """Just enough to poll: the status and what moved, without the steps."""
        await enforce_rate_limit(request, caller, "read")
        run = await owned_run(caller, run_id)
        return JSONResponse(content={
            "id": run.id.value, "status": run.status, "terminal": run.is_terminal,
            "agent_id": run.agent_id, "iterations": run.iterations, "tool_calls": run.tool_calls,
            "plan_steps_total": run.plan_steps_total, "plan_steps_succeeded": run.plan_steps_succeeded,
            "stop_reason": run.stop_reason, "exhausted_budget": run.exhausted_budget,
            "retry_of": run.retry_of, "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        })

    @router.get("/agents/runs/{run_id}/logs")
    async def run_logs(
        request: Request,
        run_id: str,
        caller: Any = Depends(principal),
        after_seq: int = Query(default=-1, ge=-1, description="Only steps after this seq; -1 for all."),
        limit: int = Query(default=DEFAULT_LOGS_LIMIT, ge=1, le=MAX_LOGS_LIMIT),
    ) -> Any:
        """The run's steps, oldest first. Pass the last ``seq`` back as ``after_seq`` to follow a
        live run; stop when ``terminal`` is true and ``next_after_seq`` is null."""
        await enforce_rate_limit(request, caller, "read")
        run = await owned_run(caller, run_id)
        steps = list(await store().list_steps(run.id, user_id=caller.user_id, limit=limit + 1, after_seq=after_seq))
        page, more = steps[:limit], len(steps) > limit
        return JSONResponse(content={
            "run_id": run.id.value, "status": run.status, "terminal": run.is_terminal,
            "logs": [s.as_dict() for s in page],
            "next_after_seq": page[-1].seq if page and more else None,
        })

    @router.post("/agents/runs/{run_id}/cancel", dependencies=idempotent)
    async def cancel_run(request: Request, run_id: str, caller: Any = Depends(principal)) -> Any:
        """Ask a live run to stop. Takes effect at the next step boundary (see the module notes)."""
        await enforce_rate_limit(request, caller, "read")
        run = await owned_run(caller, run_id)
        if run.is_terminal:
            raise ConflictError(
                f"this run already finished ({run.status})",
                details={"reason": "already_finished", "status": run.status},
            )
        cancellation = getattr(container, "run_cancellation", None)
        now = datetime.now(timezone.utc)
        live_here = cancellation is not None and cancellation.is_live(caller.user_id.value, run.id.value)
        if not live_here and now - run.updated_at > timedelta(seconds=STALE_RUN_S):
            # No worker has written this run for longer than any live run goes between saves: its
            # worker died. There is nothing to signal, so close it.
            closed = run.with_(status=AgentRunStatus.CANCELLED, stop_reason="orphaned", finished_at=now, updated_at=now)
            await store().save_run(closed)
            return JSONResponse(content={"id": run.id.value, "status": closed.status, "orphaned": True})
        if cancellation is None:
            raise ConflictError("runs cannot be cancelled on this deployment", details={"reason": "unavailable"})
        await cancellation.request(caller.user_id.value, run.id.value)
        return JSONResponse(status_code=202, content={
            "id": run.id.value, "status": "cancel_requested", "current_status": run.status,
            "effective": "at the next step boundary",
        })
