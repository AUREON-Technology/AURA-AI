"""Bind a tool-call confirmation to the exact call the user approved (ADR-006; F-04, CB-04).

A side-effecting call that policy holds for confirmation used to be approved by tool name or by a
positional call id (``step_s1``), and the approving request then *re-planned the turn from
scratch*. Nothing tied the approval to what the user had been shown: a re-plan -- a 3B model is
not deterministic, and a poisoned document can steer it -- could produce ``delete_task(B)`` where
the user approved ``delete_task(A)``, and ``B`` ran under ``A``'s approval. Auto-allowed steps
before the gated one ran a second time on resume, too.

The confirmation is stateful now, and lives on the server:

* When a turn stops on confirm-gated calls, :class:`PendingConfirmationStore` saves the turn --
  the plan exactly as executed so far (succeeded steps and their results included), whether the
  tool context was tainted, the owner, session, persona and message -- and issues one opaque,
  unguessable ``approval_id`` per held call. Each held call is recorded with a digest of
  ``(binding, tool, resolved arguments)``, where the binding is the stored turn's own random
  token, so a digest is only ever meaningful for that one pending turn.
* Approving sends those ids back. :meth:`PendingConfirmationStore.claim` checks owner and
  session, consumes the whole pending turn atomically (``Cache.incr``: two concurrent resumes
  cannot both win), and returns a :class:`ResumedTurn` -- the stored plan with the held steps put
  back to ``PENDING``, and a :class:`ConfirmationGrant` for the approved calls only.
* The kernel executes **that stored plan**; it does not re-plan. Steps that already succeeded do
  not run again. The executor asks the grant before each dispatch, and the grant confirms a call
  only if its call id, tool and digest of the *resolved* arguments all match what was approved --
  and only once. Anything else is held for confirmation again, with a fresh approval id.

Fails closed throughout: a cache that is down, an evicted record, a mismatched owner or session,
or a reused id is a refused confirmation (:class:`ConfirmationInvalidError`), never a silent
execution.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..domain.errors import ConfirmationInvalidError
from ..domain.identifiers import PlanId, StepId
from ..domain.planning import (
    Goal,
    Plan,
    PlanRevision,
    PlanStep,
    Priority,
    StepKind,
    StepStatus,
)
from ..observability.logging import get_logger

__all__ = [
    "ConfirmationGrant",
    "HeldTurn",
    "PendingCall",
    "PendingConfirmationStore",
    "ResumedTurn",
    "call_digest",
]

logger = get_logger("confirmations")

#: The ``blocked`` reason the execution engine gives a confirm-gated step.
NEEDS_CONFIRMATION = "needs_confirmation"

_RECORD_KEY = "tool-confirm:pending:v1:{token}"
_CLAIM_KEY = "tool-confirm:claimed:v1:{token}"
_ID_PREFIX = "apr"
_DEFAULT_TTL_S = 3600


def call_digest(tool: str, arguments: Mapping[str, Any], *, binding: str) -> str:
    """A stable digest of one call: tool name and canonicalized arguments, bound to one turn.

    Canonical JSON (sorted keys, no whitespace) so the same arguments always hash the same, in
    any key order. ``binding`` is the pending turn's random token, so a digest from one pending
    turn never matches a call in another.
    """
    payload = json.dumps(
        {"binding": binding, "tool": tool, "arguments": dict(arguments)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PendingCall:
    """A confirm-gated call exactly as the executor was about to dispatch it.

    ``arguments`` are the *resolved* arguments (``${step.output}`` references substituted), which
    are what the user is shown and what a later dispatch is compared against.
    """

    step_id: str
    call_id: str
    tool: str
    arguments: Mapping[str, Any]


class ConfirmationGrant:
    """The calls a user approved, each usable once, and only by the identical call.

    Mutable on purpose: :meth:`admits` consumes the entry it matches -- and the entry it fails
    to match -- so an approval can never confirm a second dispatch, whether that is a retry, a
    replanned step that happens to reuse the call id, or a different call altogether.
    """

    __slots__ = ("_approved", "_binding", "refused")

    def __init__(self, approved: Mapping[str, tuple[str, str]], *, binding: str) -> None:
        #: call id -> (tool, digest)
        self._approved = dict(approved)
        self._binding = binding
        #: Call ids whose approval was presented with a *different* call; for audit and tests.
        self.refused: list[str] = []

    def admits(self, call_id: str, tool: str, arguments: Mapping[str, Any]) -> bool:
        entry = self._approved.pop(call_id, None)
        if entry is None:
            return False
        if entry != (tool, call_digest(tool, arguments, binding=self._binding)):
            self.refused.append(call_id)
            logger.warning(
                "confirmation refused: the call differs from the one approved",
                extra={"call_id": call_id, "tool": tool, "approved_tool": entry[0]},
            )
            return False
        return True

    @property
    def remaining(self) -> tuple[str, ...]:
        return tuple(self._approved)


@dataclass(frozen=True, slots=True)
class ResumedTurn:
    """A pending turn the caller just approved, ready to execute -- not to re-plan."""

    message: str
    agent: str
    plan: Plan
    tainted: bool
    grant: ConfirmationGrant
    approved_call_ids: tuple[str, ...]
    origin_turn_id: str | None = None
    #: The Desktop IDE workspace root the held turn was proposed in (FSA-006). Pinned on resume so
    #: an approved command runs where the user saw it proposed, not in whatever workspace the
    #: client has open by the time it sends the approval.
    workspace_root: str | None = None


@dataclass(frozen=True, slots=True)
class HeldTurn:
    """A pending turn as stored, read without consuming it (:meth:`PendingConfirmationStore.peek`)."""

    message: str
    agent: str
    plan: Plan
    tainted: bool
    origin_turn_id: str | None = None
    workspace_root: str | None = None


class PendingConfirmationStore:
    """Server-side pending confirmations over the ``Cache`` port (Redis in production).

    Not an adapter: only logic over an injected port, so it sits beside the engine that uses it.
    """

    def __init__(self, cache: Any, *, ttl_s: int = _DEFAULT_TTL_S) -> None:
        self._cache = cache
        self._ttl_s = max(1, int(ttl_s))

    # -- issue ---------------------------------------------------------------------------

    async def issue(
        self,
        *,
        user_id: str,
        session_id: str | None,
        agent: str,
        message: str,
        plan: Plan,
        tainted: bool,
        calls: Sequence[PendingCall],
        turn_id: str | None = None,
        workspace_root: str | None = None,
    ) -> dict[str, str]:
        """Store the pending turn; return ``{call_id: approval_id}`` for each held call."""
        if not calls:
            return {}
        token = secrets.token_urlsafe(18)
        entries: list[dict[str, Any]] = []
        approval_ids: dict[str, str] = {}
        for call in calls:
            if call.call_id in approval_ids:
                continue
            nonce = secrets.token_urlsafe(9)
            approval_ids[call.call_id] = f"{_ID_PREFIX}.{token}.{nonce}"
            entries.append({
                "nonce": nonce,
                "step_id": call.step_id,
                "call_id": call.call_id,
                "tool": call.tool,
                "digest": call_digest(call.tool, call.arguments, binding=token),
            })
        record = {
            "v": 1,
            "user_id": user_id,
            "session_id": session_id,
            "agent": agent,
            "message": message,
            "tainted": bool(tainted),
            "turn_id": turn_id,
            "workspace_root": workspace_root,
            "plan": _plan_to_dict(plan),
            "calls": entries,
        }
        await self._cache.set(
            _RECORD_KEY.format(token=token),
            json.dumps(record, ensure_ascii=False, default=str).encode("utf-8"),
            ttl_s=self._ttl_s,
        )
        return approval_ids

    # -- claim ---------------------------------------------------------------------------

    async def _load(
        self, approval_ids: Sequence[str], *, user_id: str, session_id: str | None
    ) -> tuple[str, tuple[str, ...], dict[str, Any], dict[str, Any]]:
        """The stored record these ids name, after every ownership check; nothing is consumed."""
        token, nonces = _parse(approval_ids)
        record_key = _RECORD_KEY.format(token=token)
        try:
            raw = await self._cache.get(record_key)
        except Exception as exc:  # pragma: no cover - defensive: the port swallows its errors
            raise ConfirmationInvalidError("the confirmation could not be verified") from exc
        if raw is None:
            raise ConfirmationInvalidError(
                "this confirmation has expired or was already used; ask again"
            )
        try:
            record = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ConfirmationInvalidError("the confirmation could not be verified") from exc
        # Ownership before the claim: a token leaked to someone else must not let them burn it.
        if record.get("user_id") != user_id:
            raise ConfirmationInvalidError(
                "this confirmation has expired or was already used; ask again"
            )
        if record.get("session_id") != session_id:
            raise ConfirmationInvalidError(
                "this confirmation belongs to a different conversation",
                details={"reason": "session_mismatch"},
            )
        by_nonce = {c["nonce"]: c for c in record.get("calls") or ()}
        unknown = [n for n in nonces if n not in by_nonce]
        if unknown:
            raise ConfirmationInvalidError("the confirmation names a call that was not pending")

        return token, nonces, record, by_nonce

    async def peek(
        self, approval_ids: Sequence[str], *, user_id: str, session_id: str | None
    ) -> "HeldTurn":
        """Read the pending turn these ids name **without consuming it** (ADR-047, row 51).

        The same owner and session checks as :meth:`claim`, so a stranger learns nothing and gets
        the same refusal. What it returns is the plan as stored; nothing about it can be executed
        from here -- only :meth:`claim` consumes the turn and yields a grant.
        """
        token, _nonces, record, _by = await self._load(
            approval_ids, user_id=user_id, session_id=session_id
        )
        return HeldTurn(
            message=str(record.get("message") or ""),
            agent=str(record.get("agent") or "orchestrator"),
            plan=_plan_from_dict(record["plan"]),
            tainted=bool(record.get("tainted")),
            origin_turn_id=record.get("turn_id"),
            workspace_root=record.get("workspace_root") or None,
        )


    async def claim(
        self,
        approval_ids: Sequence[str],
        *,
        user_id: str,
        session_id: str | None,
    ) -> ResumedTurn:
        """Consume the pending turn these ids belong to. One-shot; raises if anything is off."""
        token, nonces, record, by_nonce = await self._load(
            approval_ids, user_id=user_id, session_id=session_id
        )
        record_key = _RECORD_KEY.format(token=token)
        try:
            claimed = await self._cache.incr(
                _CLAIM_KEY.format(token=token), ttl_s=self._ttl_s
            )
        except Exception as exc:
            raise ConfirmationInvalidError("the confirmation could not be verified") from exc
        if claimed != 1:
            raise ConfirmationInvalidError(
                "this confirmation has expired or was already used; ask again"
            )
        try:
            await self._cache.delete(record_key)
        except Exception:  # pragma: no cover - the claim key already makes it single-use
            logger.debug("could not delete a claimed confirmation record", exc_info=True)

        approved = {
            by_nonce[n]["call_id"]: (by_nonce[n]["tool"], by_nonce[n]["digest"]) for n in nonces
        }
        plan = _plan_from_dict(record["plan"])
        # Every held step goes back to PENDING: the approved ones run (if, and only if, the
        # grant matches), the rest are held again and get a fresh approval id.
        plan = plan.with_steps(
            replace(s, status=StepStatus.PENDING, error_kind=None, finished_at=None)
            for s in plan.steps
            if s.status is StepStatus.BLOCKED and s.error_kind == NEEDS_CONFIRMATION
        )
        return ResumedTurn(
            message=str(record.get("message") or ""),
            agent=str(record.get("agent") or "orchestrator"),
            plan=plan,
            tainted=bool(record.get("tainted")),
            grant=ConfirmationGrant(approved, binding=token),
            approved_call_ids=tuple(approved),
            origin_turn_id=record.get("turn_id"),
            workspace_root=record.get("workspace_root") or None,
        )


def _parse(approval_ids: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    ids = [i for i in dict.fromkeys(approval_ids) if isinstance(i, str)]
    if not ids:
        raise ConfirmationInvalidError("no confirmation was given")
    tokens: set[str] = set()
    nonces: list[str] = []
    for approval_id in ids:
        parts = approval_id.split(".")
        if len(parts) != 3 or parts[0] != _ID_PREFIX or not parts[1] or not parts[2]:
            raise ConfirmationInvalidError("the confirmation id is not one this server issued")
        tokens.add(parts[1])
        nonces.append(parts[2])
    if len(tokens) != 1:
        raise ConfirmationInvalidError(
            "one confirmation can only approve calls from a single turn",
            details={"reason": "mixed_turns"},
        )
    return tokens.pop(), tuple(nonces)


# -- plan (de)serialization -------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


def _plan_to_dict(plan: Plan) -> dict[str, Any]:
    goal = plan.goal
    return {
        "id": plan.id.value,
        "goal": {
            "description": goal.description,
            "success_criteria": list(goal.success_criteria),
            "constraints": list(goal.constraints),
            "priority": int(goal.priority.value),
            "deadline": _iso(goal.deadline),
            "metadata": dict(goal.metadata),
        },
        "steps": [
            {
                "id": s.id.value,
                "kind": s.kind.value,
                "description": s.description,
                "depends_on": [d.value for d in s.depends_on],
                "tool": s.tool,
                "arguments": dict(s.arguments),
                "status": s.status.value,
                "priority": int(s.priority.value),
                "attempts": s.attempts,
                "max_attempts": s.max_attempts,
                "result": s.result,
                "error_kind": s.error_kind,
                "started_at": _iso(s.started_at),
                "finished_at": _iso(s.finished_at),
                "duration_ms": s.duration_ms,
            }
            for s in plan.steps
        ],
        "revisions": [
            {
                "revision": r.revision,
                "reason": r.reason,
                "at": _iso(r.at),
                "added_steps": [s.value for s in r.added_steps],
                "removed_steps": [s.value for s in r.removed_steps],
            }
            for r in plan.revisions
        ],
        "created_at": _iso(plan.created_at),
        "max_steps": plan.max_steps,
        "generation_tokens": plan.generation_tokens,
    }


def _plan_from_dict(data: Mapping[str, Any]) -> Plan:
    goal = data["goal"]
    steps = tuple(
        PlanStep(
            id=StepId(s["id"]),
            kind=StepKind(s["kind"]),
            description=s["description"],
            depends_on=tuple(StepId(d) for d in s.get("depends_on") or ()),
            tool=s.get("tool"),
            arguments=dict(s.get("arguments") or {}),
            status=StepStatus(s["status"]),
            priority=Priority(int(s.get("priority", Priority.NORMAL.value))),
            attempts=int(s.get("attempts", 0)),
            max_attempts=int(s.get("max_attempts", 2)),
            result=s.get("result"),
            error_kind=s.get("error_kind"),
            started_at=_dt(s.get("started_at")),
            finished_at=_dt(s.get("finished_at")),
            duration_ms=float(s.get("duration_ms", 0.0)),
        )
        for s in data.get("steps") or ()
    )
    revisions = tuple(
        PlanRevision(
            revision=int(r["revision"]),
            reason=str(r.get("reason", "")),
            at=_dt(r.get("at")) or datetime.now().astimezone(),
            added_steps=tuple(StepId(s) for s in r.get("added_steps") or ()),
            removed_steps=tuple(StepId(s) for s in r.get("removed_steps") or ()),
        )
        for r in data.get("revisions") or ()
    )
    kwargs: dict[str, Any] = {}
    created = _dt(data.get("created_at"))
    if created is not None:
        kwargs["created_at"] = created
    return Plan(
        id=PlanId(data["id"]),
        goal=Goal(
            description=goal["description"],
            success_criteria=tuple(goal.get("success_criteria") or ()),
            constraints=tuple(goal.get("constraints") or ()),
            priority=Priority(int(goal.get("priority", Priority.NORMAL.value))),
            deadline=_dt(goal.get("deadline")),
            metadata=dict(goal.get("metadata") or {}),
        ),
        steps=steps,
        revisions=revisions,
        max_steps=int(data.get("max_steps", max(24, len(steps)))),
        generation_tokens=int(data.get("generation_tokens", 0)),
        **kwargs,
    )
