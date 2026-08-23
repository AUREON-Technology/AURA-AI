"""Platform-only admin routes: operators, GDPR forget, Celery job status.

Lives outside ``aura_core`` because it talks to SQLAlchemy, JWT refresh
storage, and Celery (ADR-002).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from services.jwt_auth import revoke_all_for_uid
from services.rbac import ROLE_HIERARCHY, require_role

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


@router.get("/operators")
async def list_operators(user_info: dict = Depends(require_role("Admin"))):
    try:
        from infrastructure.db.base import is_configured
        from infrastructure.db.models import User

        if not is_configured():
            return {"operators": []}
        from infrastructure.db.base import get_session_factory

        factory = get_session_factory()
        async with factory() as db:
            rows = await db.execute(
                select(User).where(User.role.in_(("Developer", "Admin", "SuperAdmin")))
            )
            operators = [
                {
                    "email": user.email,
                    "auth_subject": user.auth_subject,
                    "display_name": user.display_name,
                    "role": user.role,
                }
                for user in rows.scalars()
            ]
        return {"operators": operators}
    except HTTPException:
        raise
    except Exception:
        return {"operators": []}


@router.patch("/operators/{email}/role")
async def assign_role(
    email: str,
    payload: RolePatch,
    user_info: dict = Depends(require_role("SuperAdmin")),
):
    if payload.role not in _ASSIGNABLE:
        raise HTTPException(status_code=422, detail={"allowed": list(_ASSIGNABLE)})

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

        remaining = 0
        if user.role == "SuperAdmin":
            remaining = int(
                await db.scalar(
                    select(func.count()).select_from(User).where(User.role == "SuperAdmin")
                )
                or 0
            )
        blocked = last_superadmin_block(
            target_role=user.role,
            remaining=remaining,
            actor_email=user_info.get("email"),
            target_email=user.email,
            new_role=payload.role,
            action="demote",
        )
        if blocked:
            raise _forbid(blocked)

        user.role = payload.role
        await db.commit()
        return {"email": user.email, "role": user.role}


@router.post("/operators/{email}/revoke-sessions")
async def revoke_sessions(email: str, user_info: dict = Depends(require_role("SuperAdmin"))):
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
    return {"email": email, "revoked": revoked}


@router.post("/users/{subject}/forget")
async def forget_user(subject: str, user_info: dict = Depends(require_role("SuperAdmin"))):
    """Erase a user. Returns counts only — never content."""
    from infrastructure.db.base import get_session_factory, is_configured
    from infrastructure.db.models import (
        CalendarEvent,
        FinanceRecord,
        HealthRecord,
        Memory,
        Message,
        Task,
        User,
    )

    counts = {"sessions": 0, "sync_rows": 0, "user": 0}
    if is_configured():
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(User).where((User.auth_subject == subject) | (User.email == subject))
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            if user.role == "SuperAdmin":
                remaining = int(
                    await db.scalar(
                        select(func.count()).select_from(User).where(User.role == "SuperAdmin")
                    )
                    or 0
                )
                blocked = last_superadmin_block(
                    target_role=user.role,
                    remaining=remaining,
                    action="forget",
                )
                if blocked:
                    raise _forbid(blocked)
            now = datetime.now(timezone.utc)
            sync_count = 0
            for model in (Task, CalendarEvent, FinanceRecord, HealthRecord, Memory, Message):
                outcome = await db.execute(
                    update(model)
                    .where(model.user_id == user.id, model.deleted_at.is_(None))
                    .values(deleted_at=now)
                )
                sync_count += outcome.rowcount or 0
            counts["sync_rows"] = sync_count
            counts["sessions"] = revoke_all_for_uid(user.auth_subject)
            user.role = "User"
            counts["user"] = 1
            await db.commit()
    else:
        counts["sessions"] = revoke_all_for_uid(subject)
    return {"forgotten": True, "counts": counts}


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
