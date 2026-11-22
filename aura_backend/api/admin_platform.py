"""Platform-only admin routes: operators, GDPR forget, Celery job status.

Lives outside ``aura_core`` because it talks to SQLAlchemy, JWT refresh
storage, and Celery (ADR-002).
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from services.jwt_auth import mark_account_erased, revoke_all_devices_for_uid, revoke_all_for_uid
from services.rbac import (
    CONSOLE_MIN_ROLE,
    ROLE_HIERARCHY,
    _email_list,
    configured_identities,
    granted_role,
    require_role,
)

logger = logging.getLogger("aura.admin_platform")

router = APIRouter(prefix="/api/v3/admin", tags=["admin-platform"])

_ASSIGNABLE = ("User", "Developer", "Admin", "SuperAdmin")


class RolePatch(BaseModel):
    role: str = Field(min_length=1, max_length=32)


def last_superadmin_block(
    *,
    target_role: str,
    remaining: int,
    actor_email: str | None = None,
    target_email: str | None = None,
    new_role: str | None = None,
    action: str = "demote",
) -> str | None:
    """Refuse actions that would leave the deployment with zero SuperAdmins."""
    if target_role != "SuperAdmin":
        return None
    if action == "forget" and remaining <= 1:
        return "cannot forget the last SuperAdmin"
    if action == "demote" and new_role != "SuperAdmin":
        if remaining <= 1:
            return "cannot demote the last SuperAdmin"
        if actor_email and target_email and actor_email == target_email:
            return "cannot change your own SuperAdmin role"
    return None


def _client_ip(request: Request) -> str | None:
    """The caller's address as the trusted proxy saw it, the way ``aura_core/api/v3``'s
    ``client_ip`` names it for every audit record (never ``request.client.host`` alone)."""
    from aura_core.config import load_settings
    from aura_core.security.rate_limit import extract_client_ip

    try:
        return str(extract_client_ip(
            request.headers,
            peer=request.client.host if request.client else None,
            trusted_proxies=load_settings(validate=False).security.trusted_proxy_count,
        ))
    except Exception:  # noqa: BLE001 -- an address is context, not a reason to lose the record
        return None


async def _audit(
    request: Request, user_info: dict, action: str, resource: str, outcome: str,
    detail: dict | None = None,
) -> None:
    """One audit event for an Access-page mutation (INT-107), shaped like ``audit_admin`` in
    ``aura_core/api/v3.py``: the operator as actor, never a secret or token. A failed write is
    logged and does not undo the action, as there."""
    from aura_core.ports.runtime import AuditEvent

    container = getattr(request.app.state, "aura", None)
    audit = getattr(container, "audit", None)
    if audit is None:
        return
    try:
        await audit.record(AuditEvent(
            action=action,
            actor=user_info.get("email") or str(user_info.get("uid") or ""),
            actor_role=user_info.get("role") or "",
            resource=resource,
            outcome=outcome,
            correlation_id=request.headers.get("x-correlation-id") or "",
            source_ip=_client_ip(request),
            detail=detail,
        ))
    except Exception:
        logger.debug("admin audit write failed", extra={"action": action}, exc_info=True)


def _erased_pseudonym(subject: str) -> str:
    """The name an erased person's own audit events are given (``_erase_core``'s
    ``audit_actor``), so the record of erasing them does not keep the identity it erased."""
    return "erased:" + hashlib.sha256(subject.encode("utf-8")).hexdigest()[:12]


#: Approval kinds only a SuperAdmin may decide -- the same set ``aura_core/api/admin.py`` gates the
#: decide route on (``_SUPER_ADMIN_APPROVAL_KINDS``); everything else an Admin may decide.
_SUPER_ADMIN_APPROVAL_KINDS = frozenset({"connector_registration", "blueprint_deployment"})


async def other_approver_exists(requester: str, kind) -> bool:
    """Whether anyone besides ``requester`` (an email or auth subject, as
    ``ApprovalService.request(requested_by=...)`` records it) holds the role that may decide an
    approval of ``kind``: SuperAdmin for the Track AP kinds, Admin otherwise (INT-106).

    The ``ApprovalService`` check, so a self-approval is refused while someone else could decide.
    Counts everyone the env lists and roles.json grant the role to -- signed in yet or not -- and
    everyone the Access page assigned it, each person once. Raises when the directory cannot be
    read, so the service refuses the self-approval with a retryable error rather than guessing."""
    from infrastructure.db.base import get_session_factory, is_configured

    needed = "SuperAdmin" if str(kind) in _SUPER_ADMIN_APPROVAL_KINDS else "Admin"
    requester = (requester or "").strip()
    known: set[str] = set()
    if is_configured():
        async with get_session_factory()() as db:
            for user, role, _ in await _operators(db):
                names = {user.email, user.auth_subject}
                known |= names
                if requester not in names and ROLE_HIERARCHY[role] >= ROLE_HIERARCHY[needed]:
                    return True
    for name in configured_identities() - known:
        # Not signed in yet (no row): the configuration alone names them. An email or a uid.
        role, _ = granted_role(name, name)
        if name != requester and ROLE_HIERARCHY[role] >= ROLE_HIERARCHY[needed]:
            return True
    return False


def _forbid(message: str) -> HTTPException:
    return HTTPException(status_code=403, detail=message)


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="operator directory requires Postgres")


async def _session():
    from infrastructure.db.base import get_db, is_configured

    if not is_configured():
        raise _unavailable()
    async for session in get_db():
        yield session


#: Where a role came from when the Access page assigned it.
ACCESS_PAGE = "Access page"


def effective_role(user) -> tuple[str, str | None]:
    """A user row's resolved role and what grants it.

    The same resolution sign-in performs (``services.rbac.get_user_role``): the highest of what
    the env lists / roles.json grant and what the Access page assigned. ``users.role`` only
    counts when ``role_assigned_at`` is set -- older rows hold roles a sign-in copied there.
    """
    granted, source = granted_role(user.email, user.auth_subject)
    assigned = user.role if user.role_assigned_at is not None else None
    if assigned in ROLE_HIERARCHY and ROLE_HIERARCHY[assigned] > ROLE_HIERARCHY[granted]:
        return assigned, ACCESS_PAGE
    return granted, source


async def _operators(db) -> list:
    """Users whose resolved role is console-capable, with that role and its source."""
    from infrastructure.db.models import User

    named = configured_identities()
    conditions = [User.role_assigned_at.is_not(None)]
    if named:
        conditions += [User.email.in_(named), User.auth_subject.in_(named)]
    rows = await db.execute(select(User).where(or_(*conditions)))
    operators = []
    for user in rows.scalars():
        role, source = effective_role(user)
        if ROLE_HIERARCHY[role] >= ROLE_HIERARCHY[CONSOLE_MIN_ROLE]:
            operators.append((user, role, source))
    return operators


async def _superadmin_count(db) -> int:
    """How many people would still be SuperAdmin: assigned ones plus everyone the env grants it
    to, whether or not they have signed in yet."""
    emails = {user.email for user, role, _ in await _operators(db) if role == "SuperAdmin"}
    return len(emails | set(_email_list("SUPERADMIN_EMAILS")))


@router.get("/operators")
async def list_operators(user_info: dict = Depends(require_role("Admin"))):
    try:
        from infrastructure.db.base import get_session_factory, is_configured

        if not is_configured():
            return {"operators": []}
        async with get_session_factory()() as db:
            operators = [
                {
                    "email": user.email,
                    "auth_subject": user.auth_subject,
                    "display_name": user.display_name,
                    "role": role,
                    "granted_by": source,
                }
                for user, role, source in await _operators(db)
            ]
        return {"operators": operators}
    except HTTPException:
        raise
    except Exception:
        # Not ``[]`` with 200: the Access page would show "nobody has access", which is
        # indistinguishable from a real, empty directory (audit INT-034). The cause stays in the
        # log, not in the response.
        logger.exception("operator directory lookup failed")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "operator_directory_unavailable",
                "message": "The operator directory could not be read. Try again shortly.",
                "retryable": True,
            },
        )


@router.patch("/operators/{email}/role")
async def assign_role(
    email: str,
    payload: RolePatch,
    request: Request,
    user_info: dict = Depends(require_role("SuperAdmin")),
):
    """Assign a role on the Access page.

    It takes effect at the person's next sign-in or token refresh (within the 15-minute
    access-token lifetime): every token issue resolves the role again, including this assignment.
    A role the env lists or roles.json grant is a floor the page cannot lower -- the refusal names
    the source to change instead.
    """
    if payload.role not in _ASSIGNABLE:
        raise HTTPException(status_code=422, detail={"allowed": list(_ASSIGNABLE)})

    from infrastructure.db.base import get_session_factory, is_configured
    from infrastructure.db.models import User

    if not is_configured():
        raise _unavailable()

    async with get_session_factory()() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="no user with that email -- they must sign in once before a role can be "
                "assigned",
            )

        current, _ = effective_role(user)
        floor, source = granted_role(user.email, user.auth_subject)
        if ROLE_HIERARCHY[payload.role] < ROLE_HIERARCHY[floor]:
            raise HTTPException(
                status_code=409,
                detail=f"{user.email} is granted {floor} by {source}; remove them there to "
                "lower their role",
            )

        remaining = await _superadmin_count(db) if current == "SuperAdmin" else 0
        blocked = last_superadmin_block(
            target_role=current,
            remaining=remaining,
            actor_email=user_info.get("email"),
            target_email=user.email,
            new_role=payload.role,
            action="demote",
        )
        if blocked:
            raise _forbid(blocked)

        if payload.role == "User":
            user.role = "User"
            user.role_assigned_at = None
            user.role_assigned_by = None
        else:
            user.role = payload.role
            user.role_assigned_at = datetime.now(timezone.utc)
            user.role_assigned_by = user_info.get("email")
        await db.commit()
        role, granted_by = effective_role(user)
        revoked = {"sessions": 0, "devices": 0}
        if ROLE_HIERARCHY[role] < ROLE_HIERARCHY[current]:
            # A demotion otherwise waits for the next refresh, and a paired desktop's 30-day
            # device token has none. Ending both makes it take effect at once, apart from the
            # current 15-minute access token.
            revoked = {
                "sessions": revoke_all_for_uid(user.auth_subject),
                "devices": revoke_all_devices_for_uid(user.auth_subject),
            }
        await _audit(
            request, user_info, "admin.operator.role", f"user:{user.auth_subject}", "ok",
            detail={"from": current, "to": role, "requested": payload.role,
                    "granted_by": granted_by, "revoked": revoked},
        )
        return {"email": user.email, "role": role, "granted_by": granted_by, "revoked": revoked}


@router.post("/operators/{email}/revoke-sessions")
async def revoke_sessions(
    email: str, request: Request, user_info: dict = Depends(require_role("SuperAdmin")),
):
    from infrastructure.db.base import get_session_factory, is_configured
    from infrastructure.db.models import User

    if not is_configured():
        raise _unavailable()
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="operator not found")
        revoked = revoke_all_for_uid(user.auth_subject)
    await _audit(request, user_info, "admin.operator.revoke_sessions", f"user:{user.auth_subject}", "ok",
                 detail={"revoked": revoked})
    return {"email": email, "revoked": revoked}


#: What erasure does not reach yet, reported with every result rather than claimed as done.
NOT_ERASED = (
    "feedback records",
    "audit log events (kept; the actor is pseudonymised and the source IP dropped)",
    "LLM response cache (expires within an hour)",
    "agent run history",
    "idempotency keys (expire within a day)",
    "conversation transcripts never synced to this server (expire after 30 days)",
)


async def _erase_core(
    container, owner: str, conversation_ids: set[str], *, aliases: set[str] | frozenset[str] = frozenset(),
) -> tuple[dict, list]:
    """Erase ``owner`` from the aura_core stores: memories and their vectors, knowledge documents
    with every chunk, vector, lexical entry and graph fact, agent-written personal-OS records,
    the server-side transcript of each known conversation, the telemetry of those conversations,
    and the owner's identity in the audit log (``aliases``: the other names the actor column may
    carry, such as the email). Returns counts and the stores that failed, so a failure is
    reported instead of swallowed."""
    import hashlib

    from aura_core.domain.identifiers import SessionId, UserId

    owner_id = UserId(owner)
    counts: dict = {}
    failed: list = []

    async def step(name, run):
        try:
            counts[name] = await run()
        except Exception:
            logger.exception("erasure step failed", extra={"step": name})
            failed.append(name)

    async def drain(store):
        removed = 0
        while records := await store.list_for_owner(owner_id, limit=100):
            deleted = [await store.delete(r.id, owner_id=owner_id) for r in records]
            if not any(deleted):
                raise RuntimeError("personal-OS records could not be deleted")
            removed += sum(deleted)
        return removed

    async def personal_os():
        # The synced tables (ADR-032): this tombstones and blanks the ciphertext; the `users`
        # row's cascade below then removes the rows and the user's data keys outright. The old
        # server-only table is still cleared until it is dropped.
        removed = await drain(container.personal_os_store)
        legacy = getattr(container, "legacy_personal_os_store", None)
        if legacy is not None:
            removed += await drain(legacy)
        return removed

    async def conversations():
        sessions = conversation_ids | {"default_session"}
        for session_id in sessions:
            await container.episodic.clear(SessionId(session_id), owner_id)
        return len(sessions)

    async def audit_actor():
        # The events stay (who did what to the system is a security record); the identity in
        # them goes (audit FSA-016). One stable pseudonym per subject keeps an actor's events
        # correlatable without naming them.
        pseudonym = "erased:" + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]
        return sum([
            await container.audit.pseudonymise_actor(name, pseudonym)
            for name in dict.fromkeys([owner, *sorted(a for a in aliases if a)])
        ])

    await step("memories", lambda: container.memory.forget_owner(owner_id))
    await step("documents", lambda: container.knowledge.erase_owner(owner_id))
    await step("personal_os_records", personal_os)
    await step("conversations", conversations)
    await step("telemetry_turns", lambda: container.telemetry_sink.purge_sessions(conversation_ids))
    make_ref = getattr(container, "owner_ref", None)
    owner_ref = make_ref(owner) if make_ref is not None else None
    if owner_ref:
        # A turn's usage is tied to its owner by a keyed reference (migration 0033). It is
        # pseudonymous personal data, and `purge_sessions` cannot find a session the server never
        # learned the id of, so the reference is what erasure deletes by.
        await step("telemetry_owner_turns", lambda: container.telemetry_sink.purge_owner(owner_ref))
    sharing = getattr(container, "sharing", None)
    if sharing is not None:
        # Copies they shared into teams are theirs to take back (review F1#7).
        await step("shared_chats", lambda: sharing.store.delete_by_sharer(owner))
    consent_store = getattr(container, "engineer_consent", None)
    if consent_store is not None:
        # D-03: the record of agreeing to send code to Anthropic is personal data too.
        await step("engineer_consent", lambda: consent_store.delete_for_owner(owner_id))
    await step("audit_events_pseudonymised", audit_actor)
    return counts, failed


async def erase_account(container, subject: str, *, unknown_user_ok: bool = False) -> dict:
    """Erase the user ``subject`` names (an auth subject or an email). Returns counts only.

    1. the aura_core stores are erased (``_erase_core``);
    2. refresh tokens and every paired device are revoked;
    3. the ``users`` row is deleted, which removes every v2 row the user owns -- all synced
       tables, insights, the GitHub link -- through the schema's ``ON DELETE CASCADE``.

    If any store fails, the ``users`` row is kept so the same call can be retried, and the
    result says ``forgotten: false`` and which stores failed. What erasure still does not reach
    is listed in ``not_erased`` on every result.

    Shared by the operator route below and the user's own ``POST /api/v3/account/delete``
    (``api/account.py``). ``unknown_user_ok`` is for the latter: a caller holding a valid token
    for a subject with no ``users`` row (one that never synced) still has aura_core data and
    sessions to erase, where an operator's unknown subject is more likely a typo -- 404.
    """
    from infrastructure.db.base import get_session_factory, is_configured
    from infrastructure.db.models import Message, User

    counts: dict = {"sessions": 0, "devices": 0, "user": 0}
    failed: list = []

    async def erase_everything_but_the_row(auth_subject: str, conversation_ids: set, email: str | None = None) -> None:
        tenancy = getattr(container, "tenancy", None)
        if tenancy is not None:
            # A team cannot lose its only Owner to an erasure, the operator's included (SaaS
            # P1-F1 review #7): hand the team over or delete it first. Nothing is erased here.
            owned = await tenancy.sole_ownerships(auth_subject)
            if owned:
                raise HTTPException(status_code=409, detail={
                    "error": "owns_team_workspaces",
                    "workspaces": [{"id": ws.id, "name": ws.name} for ws in owned],
                })
        if container is not None:
            core, core_failed = await _erase_core(
                container, auth_subject, conversation_ids, aliases={email} if email else frozenset(),
            )
            counts.update(core)
            failed.extend(core_failed)
        else:
            failed.append("aura_core stores (no container on this app)")
        counts["sessions"] = revoke_all_for_uid(auth_subject)
        counts["devices"] = revoke_all_devices_for_uid(auth_subject)
        # Live access tokens too, so another signed-in device cannot recreate the account
        # in the minutes before its token expires.
        try:
            mark_account_erased(auth_subject)
        except Exception:
            logger.exception("erasure step failed", extra={"step": "access_tokens"})
            failed.append("access_tokens")

    if is_configured():
        async with get_session_factory()() as db:
            result = await db.execute(
                select(User).where((User.auth_subject == subject) | (User.email == subject))
            )
            user = result.scalar_one_or_none()
            if user is None:
                if not unknown_user_ok:
                    raise HTTPException(status_code=404, detail="user not found")
                await erase_everything_but_the_row(subject, set())
            else:
                current, _ = effective_role(user)
                if current == "SuperAdmin":
                    blocked = last_superadmin_block(
                        target_role=current, remaining=await _superadmin_count(db), action="forget",
                    )
                    if blocked:
                        raise _forbid(blocked)
                conversation_ids = set(
                    (await db.execute(
                        select(Message.conversation_id).where(Message.user_id == user.id).distinct()
                    )).scalars()
                )
                await erase_everything_but_the_row(user.auth_subject, conversation_ids, user.email)
                if not failed:
                    await db.delete(user)
                    await db.commit()
                    counts["user"] = 1
    else:
        await erase_everything_but_the_row(subject, set())
    return {
        "forgotten": not failed,
        "counts": counts,
        "failed": failed,
        "not_erased": list(NOT_ERASED),
    }


@router.post("/users/{subject}/forget")
async def forget_user(
    subject: str, request: Request, user_info: dict = Depends(require_role("SuperAdmin")),
):
    """Erase a user. Returns counts only — never content.

    Used to soft-delete six sync tables and answer ``forgotten: true`` while the user's memories
    (and their vectors), knowledge documents, server-side transcripts, agent-written records,
    projects and goals, device tokens and the ciphertext itself all stayed. What it does now is
    ``erase_account``'s docstring.

    Audited (INT-107) under the erased person's pseudonym, not their subject or email: the
    record that someone was erased must not keep who they were. Counts only, as returned.
    """
    result = await erase_account(getattr(request.app.state, "aura", None), subject)
    await _audit(
        request, user_info, "admin.user.forget", f"user:{_erased_pseudonym(subject)}",
        "ok" if result["forgotten"] else "partial",
        detail={"forgotten": result["forgotten"], "failed": list(result["failed"]),
                "counts": dict(result["counts"])},
    )
    return result


@router.get("/jobs/{task_id}")
async def job_status(task_id: str, user_info: dict = Depends(require_role("Admin"))):
    try:
        from celery.result import AsyncResult
        from celery_app import celery_app

        result = AsyncResult(task_id, app=celery_app)
        payload = {"task_id": task_id, "state": result.state}
        if result.successful():
            payload["result"] = result.result
        return payload
    except Exception:
        return {"task_id": task_id, "state": "UNKNOWN"}


@router.get("/me")
async def whoami(user_info: dict = Depends(require_role("Developer")), request: Request = None):
    return {
        "uid": user_info.get("uid"),
        "email": user_info.get("email"),
        "name": user_info.get("name"),
        "role": user_info.get("role"),
        "is_admin": ROLE_HIERARCHY.get(user_info.get("role"), 0) >= ROLE_HIERARCHY["Admin"],
        "is_super_admin": user_info.get("role") == "SuperAdmin",
    }
