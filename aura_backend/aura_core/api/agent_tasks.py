"""The agent task gateway (ADR-041, roadmap rows 84, 87 and 91).

* ``POST   /api/v3/agents/tasks``                 submit a task (202; 200 on an idempotent replay)
* ``GET    /api/v3/agents/tasks``                 the caller's tasks, filterable
* ``GET    /api/v3/agents/tasks/{id}``            status and result
* ``POST   /api/v3/agents/tasks/{id}/cancel``     cancel
* ``POST   /api/v3/agents/tasks/{id}/approve``    approve the held tool calls and resume

A task is one ``/chat`` turn that runs server-side. Submitting one is validated by the *same code*
``/chat`` uses -- the rate limit, ``to_turn`` (capability grant, caller context, session id),
``kernel.preflight`` (Engineer consent) and ``admit_turn`` (plan entitlement and the message
reservation) -- passed in from ``v3.py`` rather than re-implemented, so a check added to ``/chat``
reaches tasks too. What the kernel itself enforces while running (the per-user token allowance, the
tool policy, taint and confirmation gates) applies unchanged because the task runs through
``kernel.run``.

Owner scoping: every store call takes ``caller.user_id`` and the record is checked against it, so
another person's task id reads as 404, exactly like an id that never existed.

Idempotency (row 91): ``POST /agents/tasks`` and ``POST /agents/tasks/{id}/approve`` accept a client
generated ``client_request_id`` (alias ``idempotency_key``) in the body or an ``Idempotency-Key``
header. The same key from the same person returns the same task; a different request under a
reused key is 409 ``idempotency_key_reused``; a key still being processed is 409
``idempotency_in_progress`` (retryable). Keys are remembered ``AURA_AGENT_TASKS_IDEMPOTENCY_TTL_S``
(24 h by default) and are scoped by person, so two people's keys never meet.
"""

# No `from __future__ import annotations`: request models are defined inside the register
# function and FastAPI must resolve their annotations at decoration time (as in v3.py).
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from ..domain.errors import (
    ConfirmationInvalidError,
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationError,
)
from ..domain.identifiers import CorrelationId
from ..domain.untrusted import MAX_UNTRUSTED_CHARS
from ..history.run_search import parse_bound
from ..observability.logging import get_logger
from ..tasks import (
    AgentTask,
    IdempotencyInProgressError,
    IdempotencyKeyReusedError,
    TaskPriority,
    TaskStatus,
    is_stale,
    spec_to_turn,
    turn_to_spec,
)
from .idempotency import IDEMPOTENCY_HEADER, REPLAYED_HEADER, parse_idempotency_key

__all__ = ["register_agent_task_ops"]

logger = get_logger("api.agent_tasks")

#: How long a retry waits for the request that holds the same key to finish being accepted.
_IN_PROGRESS_WAIT_S = 2.0


class TaskNotAwaitingApprovalError(ConflictError):
    """409: the task is not stopped on a confirmation (it is queued, running, or finished)."""

    kind = "task_not_awaiting_approval"


def _fingerprint(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def register_agent_task_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    enforce_rate_limit: Callable,
    build_turn: Callable[..., Awaitable[Any]],
    admit_turn: Callable[..., Awaitable[Any]],
    release_turn: Callable[..., Awaitable[None]],
    Depends: Any,
    JSONResponse: Any,
    Query: Any,
    Request: Any,
) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    class ContextEntry(BaseModel):
        id: str = Field(min_length=1, max_length=128)
        text: str = Field(min_length=1, max_length=MAX_UNTRUSTED_CHARS)
        source: str = Field(default="", max_length=128)

    class TaskCreateRequest(BaseModel):
        # Unknown fields are a 422, not silently dropped: a field this route does not support
        # (images, response_schema) must not look as if it had been honoured.
        model_config = ConfigDict(extra="forbid")

        goal: str = Field(min_length=1, max_length=32_000)
        persona: str = Field(default="orchestrator", max_length=64)
        priority: str = Field(default=TaskPriority.NORMAL, pattern="^(high|normal|low)$")
        #: Do not start before this time (ISO-8601; naive means UTC). In the past means now.
        run_at: datetime | None = None
        session_id: str | None = None
        use_knowledge: bool = True
        use_memory: bool = True
        use_tools: bool = True
        plan: bool | None = None
        strategy: str | None = None
        routing_policy: str | None = None
        require_local: bool = False
        max_output_tokens: int = Field(default=1024, ge=16, le=8192)
        temperature: float = Field(default=0.3, ge=0.0, le=2.0)
        desktop_task_isolation: bool = False
        workspace_root: str | None = Field(default=None, max_length=1024)
        context: list[ContextEntry] = Field(default_factory=list, max_length=16)
        client_request_id: str | None = Field(default=None, max_length=255)
        idempotency_key: str | None = Field(default=None, max_length=255)

    class TaskApproveRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        approval_ids: list[str] = Field(min_length=1, max_length=32)
        client_request_id: str | None = Field(default=None, max_length=255)
        idempotency_key: str | None = Field(default=None, max_length=255)

    store = container.agent_task_store
    settings = container.settings.agent_tasks

    def _queue() -> Any:
        queue = getattr(container, "agent_tasks", None)
        if queue is None:
            raise DependencyUnavailableError(
                "agent tasks are not enabled on this deployment (AURA_AGENT_TASKS_ENABLED)",
                dependency="agent_tasks",
            )
        return queue

    def _now() -> datetime:
        clock = getattr(container, "clock", None)
        return clock.now() if clock is not None and hasattr(clock, "now") else datetime.now(timezone.utc)

    def _idempotency_key(request: Any, *candidates: str | None) -> str | None:
        """The one key a request carries: body ``client_request_id`` / ``idempotency_key`` and the
        ``Idempotency-Key`` header are the same thing, and must agree if more than one is sent."""
        raw = [c for c in candidates if c]
        header = request.headers.get(IDEMPOTENCY_HEADER)
        if header:
            raw.append(header)
        keys = {parse_idempotency_key(r) for r in raw}
        if len(keys) > 1:
            raise ValidationError(
                "client_request_id, idempotency_key and Idempotency-Key must agree when more than "
                "one is sent", details={"field": "client_request_id"},
            )
        return next(iter(keys), None)

    async def _fresh(task: AgentTask) -> AgentTask:
        """Report a queued or running task whose process went silent as ``failed``, and keep it
        that way: its owner (this worker's queue, or another's) stopped vouching for it."""
        queue = getattr(container, "agent_tasks", None)
        if queue is not None and queue.owns(task.id):
            return task
        if not is_stale(task, now=_now(), stale_after_s=settings.stale_after_s):
            return task
        now = _now()
        stale = task.with_(
            status=TaskStatus.FAILED, finished_at=now, updated_at=now,
            error={"kind": "interrupted", "retryable": True, "details": {},
                   "message": "the server that was running this task stopped; submit it again"},
        )
        try:
            await store.save(stale)
        except Exception:
            logger.debug("could not persist a stale task's state", exc_info=True)
        return stale

    async def _owned(caller: Any, task_id: str) -> AgentTask:
        task = await store.get(caller.user_id.value, task_id)
        if task is None:
            # Another person's id and an unknown id read the same.
            raise NotFoundError("task not found")
        return await _fresh(task)

    def _view(task: AgentTask, *, with_queue: bool = False, replay: bool | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"task": task.public()}
        if with_queue:
            queue = getattr(container, "agent_tasks", None)
            position = queue.position(task.id) if queue is not None else None
            body["queue"] = {"position": position, **(queue.stats() if queue is not None else {})}
        if replay is not None:
            body["idempotent_replay"] = replay
        return body

    async def _await_value(owner: str, scope: str, key: str) -> dict[str, Any] | None:
        deadline = _now() + timedelta(seconds=_IN_PROGRESS_WAIT_S)
        while True:
            value = await store.idempotency_value(owner, scope, key)
            if value is not None or _now() >= deadline:
                return value
            await asyncio.sleep(0.05)

    async def _claim(owner: str, scope: str, key: str, fingerprint: str) -> AgentTask | None:
        """Claim ``key`` or return the task its first holder produced. Raises 409 for a reused
        key with a different request, or while the first request is still being accepted."""
        won = await store.claim_idempotency(owner, scope, key)
        if won:
            return None
        value = await _await_value(owner, scope, key)
        return await _replay(owner, value, fingerprint)

    async def _replay(owner: str, value: dict[str, Any] | None, fingerprint: str) -> AgentTask:
        if value is None:
            raise IdempotencyInProgressError(
                "a request with this key is still being processed; send the same request again "
                "in a moment", details={"retry_after": 1},
            )
        if value.get("fp") != fingerprint:
            raise IdempotencyKeyReusedError(
                "this client_request_id was already used for a different request",
                details={"reason": "idempotency_key_reused"},
            )
        task = await store.get(owner, str(value.get("task_id")))
        if task is None:
            # The task's record outlived neither its TTL nor the key's; the replay has nothing
            # to return, and a fresh key is the only honest way forward.
            raise NotFoundError("the task this key created is no longer stored; use a new key")
        return await _fresh(task)

    def _replay_response(task: AgentTask) -> Any:
        return JSONResponse(
            status_code=200, content=_view(task, with_queue=True, replay=True),
            headers={REPLAYED_HEADER: "true"},
        )

    # -- submit --------------------------------------------------------------------------

    @router.post("/agents/tasks")
    async def create_task(
        request: Request, payload: TaskCreateRequest, caller: Any = Depends(principal),
    ) -> JSONResponse:
        """Submit a task: ``202`` with the task (``status: queued``), or ``200`` with the same
        task and ``idempotent_replay: true`` when ``client_request_id`` was already used by this
        person for this request. Refusals: ``429`` (rate limit, plan quota, ``task_queue_full``
        with ``Retry-After``), ``403`` (Engineer consent, plan entitlement), ``409``
        (``idempotency_key_reused`` / ``idempotency_in_progress``), ``422`` (invalid request),
        ``503`` (tasks disabled)."""
        queue = _queue()
        owner = caller.user_id.value
        key = _idempotency_key(request, payload.client_request_id, payload.idempotency_key)
        run_at = payload.run_at
        if run_at is not None and run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        now = _now()
        if run_at is not None and run_at > now + timedelta(seconds=settings.max_schedule_ahead_s):
            raise ValidationError(
                "run_at is too far ahead",
                details={"field": "run_at", "max_ahead_s": settings.max_schedule_ahead_s},
            )
        fields = payload.model_dump(exclude={"client_request_id", "idempotency_key", "run_at"})
        fingerprint = _fingerprint({**fields, "run_at": run_at.isoformat() if run_at else None})

        if key:
            # A replay costs nothing and takes nothing: answered before the rate limit and
            # before anything is reserved.
            known = await store.idempotency_value(owner, "create", key)
            if known is not None:
                return _replay_response(await _replay(owner, known, fingerprint))

        await enforce_rate_limit(request, caller, "chat")

        if key:
            existing = await _claim(owner, "create", key, fingerprint)
            if existing is not None:
                return _replay_response(existing)
        try:
            turn = await build_turn(
                {
                    "message": payload.goal, "agent": payload.persona,
                    "session_id": payload.session_id, "use_knowledge": payload.use_knowledge,
                    "use_memory": payload.use_memory, "use_tools": payload.use_tools,
                    "plan": payload.plan, "strategy": payload.strategy,
                    "routing_policy": payload.routing_policy, "require_local": payload.require_local,
                    "max_output_tokens": payload.max_output_tokens,
                    "temperature": payload.temperature,
                    "desktop_task_isolation": payload.desktop_task_isolation,
                    "workspace_root": payload.workspace_root,
                    "context": [c.model_dump() for c in payload.context],
                },
                caller, request,
            )
            # Consent is refused here, at submission, not discovered later in a failed task.
            await container.kernel.preflight(turn)
            reservation, turn = await admit_turn(caller, payload.persona, turn)
            try:
                task = AgentTask(
                    id=str(uuid.uuid4()), owner=owner, goal=payload.goal,
                    persona=payload.persona, status=TaskStatus.QUEUED, priority=payload.priority,
                    created_at=now, updated_at=now, run_at=run_at, heartbeat_at=now,
                    correlation_id=CorrelationId.new().value, attempts=1,
                    session_id=payload.session_id,
                )
                await queue.submit(task, turn_to_spec(turn), reservation=reservation,
                                   release=release_turn)
            except BaseException:
                await release_turn(reservation)
                raise
        except BaseException:
            if key:
                try:
                    await store.release_idempotency(owner, "create", key)
                except Exception:
                    logger.debug("could not release an idempotency claim", exc_info=True)
            raise
        if key:
            await store.store_idempotency(owner, "create", key, {"fp": fingerprint, "task_id": task.id})
        return JSONResponse(status_code=202, content=_view(task, with_queue=True, replay=False))

    # -- read ----------------------------------------------------------------------------

    @router.get("/agents/tasks")
    async def list_tasks(
        request: Request,
        caller: Any = Depends(principal),
        status: str | None = Query(default=None, max_length=32),
        persona: str | None = Query(default=None, max_length=64),
        since: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> JSONResponse:
        """The caller's own tasks, newest first (the newest 200 are kept in the listing). Each
        row carries ``result_summary`` instead of the full ``result``; fetch one task for that."""
        await enforce_rate_limit(request, caller, "read")
        if status is not None and status not in TaskStatus.ALL:
            raise ValidationError("unknown status", details={"field": "status",
                                                              "allowed": list(TaskStatus.ALL)})
        rows = await store.list_tasks(
            caller.user_id.value, status=None, persona=persona,
            since=parse_bound(since, name="since"), limit=200,
        )
        fresh = [await _fresh(t) for t in rows]
        if status is not None:
            fresh = [t for t in fresh if t.status == status]
        out = []
        for task in fresh[:limit]:
            row = task.public()
            result = row.pop("result", None) or {}
            row["result_summary"] = result.get("summary")
            out.append(row)
        return JSONResponse(content={"tasks": out, "count": len(out)})

    @router.get("/agents/tasks/{task_id}")
    async def get_task(
        request: Request, task_id: str, caller: Any = Depends(principal),
    ) -> JSONResponse:
        """One of the caller's tasks: ``status`` (``queued``, ``running``, ``awaiting_approval``,
        ``succeeded``, ``failed``, ``cancelled``), timestamps, ``result`` once it has one,
        ``error`` for a failure, ``run_id`` for ``GET /agents/runs/{run_id}``, and where it waits
        in this server's queue. A task survives a dropped connection; it does not survive a
        server restart (it then reads ``failed`` / ``interrupted`` and may be resubmitted)."""
        await enforce_rate_limit(request, caller, "read")
        return JSONResponse(content=_view(await _owned(caller, task_id), with_queue=True))

    # -- cancel --------------------------------------------------------------------------

    @router.post("/agents/tasks/{task_id}/cancel")
    async def cancel_task(
        request: Request, task_id: str, caller: Any = Depends(principal),
    ) -> JSONResponse:
        """Cancel a task. Idempotent: cancelling a finished task changes nothing and says so.
        A running task is stopped (``cancelled`` once its turn unwinds, normally at once; on a
        different server it is flagged ``cancel_requested`` and stops within a few seconds)."""
        await enforce_rate_limit(request, caller, "read")
        queue = _queue()
        task = await _owned(caller, task_id)
        if task.is_terminal:
            return JSONResponse(content={**_view(task), "cancel": {
                "accepted": False, "note": f"the task already {task.status}"}})
        updated = await queue.cancel(task)
        return JSONResponse(content={**_view(updated), "cancel": {
            "accepted": True,
            "note": ("cancelled" if updated.status == TaskStatus.CANCELLED
                     else "cancellation requested; the task stops shortly"),
        }})

    # -- approve -------------------------------------------------------------------------

    @router.post("/agents/tasks/{task_id}/approve")
    async def approve_task(
        request: Request, task_id: str, payload: TaskApproveRequest,
        caller: Any = Depends(principal),
    ) -> JSONResponse:
        """Approve the confirm-gated tool calls a task is waiting on and resume it. Send the
        ``approval_id`` values from the task's ``result.pending_tool_calls``. The task returns to
        ``queued`` and runs again from its stored plan, executing only the approved calls, once
        (ADR-006). Idempotent with ``client_request_id``: a retry of the same approval returns
        the same task instead of ``409 confirmation_invalid`` (approvals are one-shot)."""
        queue = _queue()
        owner = caller.user_id.value
        key = _idempotency_key(request, payload.client_request_id, payload.idempotency_key)
        scope = f"approve:{task_id}"
        fingerprint = _fingerprint({"approval_ids": sorted(set(payload.approval_ids))})
        await _owned(caller, task_id)  # 404 for a task that is not the caller's, before anything else

        if key:
            known = await store.idempotency_value(owner, scope, key)
            if known is not None:
                return _replay_response(await _replay(owner, known, fingerprint))

        await enforce_rate_limit(request, caller, "chat")
        if key:
            existing = await _claim(owner, scope, key, fingerprint)
            if existing is not None:
                return _replay_response(existing)
        try:
            # Re-read under the claim: the task may have moved while this request waited.
            task = await _owned(caller, task_id)
            if task.status != TaskStatus.AWAITING_APPROVAL:
                raise TaskNotAwaitingApprovalError(
                    f"the task is {task.status}; only a task awaiting approval can be approved",
                    details={"status": task.status},
                )
            pending = {
                str(p.get("approval_id")) for p in (task.result or {}).get("pending_tool_calls", [])
                if isinstance(p, dict) and p.get("approval_id")
            }
            if not set(payload.approval_ids) <= pending:
                raise ConfirmationInvalidError(
                    "an approval id does not name a call this task is waiting on",
                    details={"pending": sorted(pending)},
                )
            spec = await store.spec(owner, task.id)
            if spec is None:
                raise NotFoundError("the task's request is no longer stored; submit it again")
            turn = spec_to_turn({**spec, "approval_ids": list(payload.approval_ids)}, task=task)
            reservation, turn = await admit_turn(caller, task.persona, turn)
            try:
                now = _now()
                queued = task.with_(
                    status=TaskStatus.QUEUED, updated_at=now, heartbeat_at=now, finished_at=None,
                    attempts=task.attempts + 1, result=None, error=None, cancel_requested=False,
                )
                await queue.requeue(queued, turn_to_spec(turn), reservation=reservation,
                                    release=release_turn)
            except BaseException:
                await release_turn(reservation)
                raise
        except BaseException:
            if key:
                try:
                    await store.release_idempotency(owner, scope, key)
                except Exception:
                    logger.debug("could not release an idempotency claim", exc_info=True)
            raise
        if key:
            await store.store_idempotency(owner, scope, key, {"fp": fingerprint, "task_id": task.id})
        return JSONResponse(status_code=202, content=_view(queued, with_queue=True, replay=False))
