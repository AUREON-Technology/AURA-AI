"""Tool approvals: what is waiting for the person, what they decided, and refusing a held call
(``/api/v3/approvals``).

**Approving is not here, and that is deliberate.** A held call is released by resuming the turn that
held it: ``POST /chat`` (or ``/chat/stream``) with ``approval_ids``. That request runs the stored
plan, so the call is approved, executed and answered in one place, bound to the exact call the
person was shown (``execution/confirmations.py``). An approve route that only flipped a flag would
leave nothing to run it.

    GET  /approvals                 pending and past, newest first (cursor-paged; status, tool, time)
    GET  /approvals/{approval_id}   one
    POST /approvals/{approval_id}/reject   refuse it; the held turn is discarded

**A row has no content**: the tool, the call id and a digest, never the arguments
(``domain/approvals.py``). Each row carries the ``approval_id`` a client sends to ``/chat`` -- a
credential only the owner can use, one time, within an hour -- so a second device can approve what
the first one was shown.

**Rejecting ends the held turn.** One turn is one pending record with one claim, so refusing any of
its calls discards all of it: every call it still held is recorded as rejected and none can be
approved afterwards. ``approved`` means the approval was accepted and the call released to run, not
that it succeeded.

**Ownership.** Every read and write takes the caller's user id; another person's id answers exactly
as a missing one (404). A service key has no approvals.

No ``from __future__ import annotations``, like ``agents.py`` and ``agent_runs.py``.
"""

from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.approvals import ApprovalStatus
from ..domain.errors import ConfirmationInvalidError, ConflictError, NotFoundError, ValidationError
from ..domain.identifiers import UserId
from .agent_runs import decode_run_cursor, encode_run_cursor

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

__all__ = ["register_approval_ops"]


def _fingerprint(status: str | None, tool: str | None, since: datetime | None, until: datetime | None) -> dict[str, Any]:
    return {"status": status, "tool": tool, "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None}


def register_approval_ops(
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

    def ledger() -> Any:
        found = getattr(container, "approval_ledger", None)
        if found is None:
            raise NotFoundError("approvals are not recorded on this deployment")
        return found

    def person(caller: Any, approval_id: str) -> UserId:
        if caller.is_service:
            raise NotFoundError("approval not found", details={"approval_id": approval_id})
        return caller.user_id

    def now() -> datetime:
        return container.clock.now() if getattr(container, "clock", None) else datetime.now(timezone.utc)

    @router.get("/approvals")
    async def list_approvals(
        request: Request,
        caller: Any = Depends(principal),
        status: str | None = Query(default=None, description="pending, approved, rejected, superseded or expired"),
        tool: str | None = Query(default=None, max_length=200),
        since: datetime | None = Query(default=None),
        until: datetime | None = Query(default=None),
        cursor: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    ) -> Any:
        await enforce_rate_limit(request, caller, "read")
        if caller.is_service:
            raise NotFoundError("approvals are not available to a service key")
        try:
            wanted = ApprovalStatus(status) if status else None
        except ValueError:
            raise ValidationError(
                "status must be pending, approved, rejected, superseded or expired",
                details={"field": "status"},
            ) from None
        fingerprint = _fingerprint(status, tool, since, until)
        before = decode_run_cursor(cursor, fingerprint) if cursor else None
        current = now()
        rows = list(await ledger().list(
            caller.user_id, now=current, status=wanted, tool=tool, since=since, until=until,
            before=before, limit=limit + 1,
        ))
        page, more = rows[:limit], len(rows) > limit
        return JSONResponse(content={
            "approvals": [r.as_dict(current) for r in page],
            "next_cursor": encode_run_cursor(fingerprint, created_at=page[-1].created_at, run_id=page[-1].id)
            if page and more else None,
        })

    @router.get("/approvals/{approval_id}")
    async def get_approval(request: Request, approval_id: str, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller, approval_id)
        row = await ledger().get(owner, approval_id)
        if row is None:
            raise NotFoundError("approval not found", details={"approval_id": approval_id})
        return JSONResponse(content=row.as_dict(now()))

    @router.post("/approvals/{approval_id}/reject", dependencies=idempotent)
    async def reject_approval(request: Request, approval_id: str, caller: Any = Depends(principal)) -> Any:
        """Refuse a held call. Discards the whole held turn (see the module notes)."""
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller, approval_id)
        row = await ledger().get(owner, approval_id)
        if row is None:
            raise NotFoundError("approval not found", details={"approval_id": approval_id})
        effective = row.effective_status(now())
        if effective is not ApprovalStatus.PENDING:
            raise ConflictError(
                f"this approval is {effective.value}", details={"reason": "not_pending", "status": effective.value},
            )
        confirmations = getattr(container, "tool_confirmations", None)
        if confirmations is None:
            raise ConflictError("approvals cannot be refused on this deployment", details={"reason": "unavailable"})
        try:
            await confirmations.reject(approval_id, user_id=owner.value, session_id=row.session_id)
        except ConfirmationInvalidError:
            # The held turn is gone from the cache (used from another device a moment ago, or
            # evicted): there is nothing left to refuse or to approve.
            raise ConflictError(
                "this approval is no longer pending", details={"reason": "no_longer_pending"},
            ) from None
        after = await ledger().get(owner, approval_id)
        return JSONResponse(content=(after or row).as_dict(now()))
