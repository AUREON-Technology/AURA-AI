"""A person's in-app notification inbox and its settings.

    GET    /notifications                   newest first (cursor-paged; unread, kind)
    GET    /notifications/unread-count      the badge
    POST   /notifications/{id}/read         mark one read
    POST   /notifications/read-all          mark every unread one read
    DELETE /notifications/{id}              remove one
    GET    /me/notification-settings        which kinds are on
    PUT    /me/notification-settings        switch kinds off (replaces: a kind not listed is on)

**A notification is a kind and its parameters, never prose** (``domain/notifications.py``): the
client renders and translates them, so the server stores no user-visible text and nothing of a
person's own content. No credential travels in one either: an approval notification names the tool
and the conversation; the id that would release the call is behind ``GET /approvals``.

**There is no push.** A client learns of a new notification by asking (``unread-count`` is cheap, for
polling). Pushing to a closed app needs device tokens and a push service the deployment does not
have; the inbox is what a push would point at.

**The emails stay as they are.** Invites, receipts, renewal reminders and past-due notices are
transactional email from ``notifications/service.py`` and have no switch here; the billing ones are
mirrored into the inbox for the workspace's owners.

**Ownership.** Every statement takes the caller's user id; another person's notification id answers
exactly as a missing one (404). A service key has no inbox.

No ``from __future__ import annotations``, like the other API modules.
"""

from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.errors import NotFoundError, ValidationError
from ..domain.notifications import KINDS, NotificationSettings
from .agent_runs import decode_run_cursor, encode_run_cursor

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

__all__ = ["register_notification_ops"]


def register_notification_ops(
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
    from pydantic import BaseModel, Field

    class SettingsRequest(BaseModel):
        disabled_kinds: list[str] = Field(default_factory=list, max_length=len(KINDS))

    def store() -> Any:
        found = getattr(container, "notification_store", None)
        if found is None:
            raise NotFoundError("notifications are not stored on this deployment")
        return found

    def settings_store() -> Any:
        found = getattr(container, "notification_settings", None)
        if found is None:
            raise NotFoundError("notification settings are not stored on this deployment")
        return found

    def person(caller: Any) -> Any:
        if caller.is_service:
            raise NotFoundError("a service key has no notifications")
        return caller.user_id

    def now() -> datetime:
        return container.clock.now() if getattr(container, "clock", None) else datetime.now(timezone.utc)

    @router.get("/notifications")
    async def list_notifications(
        request: Request,
        caller: Any = Depends(principal),
        unread: bool = Query(default=False, description="Only the ones not yet read."),
        kind: str | None = Query(default=None, max_length=64),
        cursor: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    ) -> Any:
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller)
        if kind is not None and kind not in KINDS:
            raise ValidationError(f"kind must be one of {', '.join(KINDS)}", details={"field": "kind"})
        fingerprint = {"unread": unread, "kind": kind}
        before = decode_run_cursor(cursor, fingerprint) if cursor else None
        rows = list(await store().list(owner, unread_only=unread, kind=kind, before=before, limit=limit + 1))
        page, more = rows[:limit], len(rows) > limit
        return JSONResponse(content={
            "notifications": [n.as_dict() for n in page],
            "next_cursor": encode_run_cursor(fingerprint, created_at=page[-1].created_at, run_id=page[-1].id)
            if page and more else None,
            "unread_count": await store().unread_count(owner),
        })

    @router.get("/notifications/unread-count")
    async def unread_count(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        return JSONResponse(content={"unread": await store().unread_count(person(caller))})

    @router.post("/notifications/read-all", dependencies=idempotent)
    async def read_all(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        marked = await store().mark_read(person(caller), None, at=now())
        return JSONResponse(content={"marked": marked, "unread": 0})

    @router.post("/notifications/{notification_id}/read", dependencies=idempotent)
    async def read_one(request: Request, notification_id: str, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller)
        if await store().get(owner, notification_id) is None:
            raise NotFoundError("notification not found", details={"notification_id": notification_id})
        await store().mark_read(owner, [notification_id], at=now())
        return JSONResponse(content=(await store().get(owner, notification_id)).as_dict())

    @router.delete("/notifications/{notification_id}", dependencies=idempotent)
    async def delete_one(request: Request, notification_id: str, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        if not await store().delete(person(caller), notification_id):
            raise NotFoundError("notification not found", details={"notification_id": notification_id})
        return JSONResponse(content={"deleted": True})

    @router.get("/me/notification-settings")
    async def get_settings(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        stored = await settings_store().get(person(caller)) or NotificationSettings()
        return JSONResponse(content=stored.as_dict())

    @router.put("/me/notification-settings", dependencies=idempotent)
    async def put_settings(request: Request, payload: SettingsRequest, caller: Any = Depends(principal)) -> Any:
        """Replace which kinds are off. A kind not listed is on; listing none turns everything on."""
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller)
        wanted = NotificationSettings(disabled_kinds=frozenset(payload.disabled_kinds))
        stored = await settings_store().put(owner, wanted, at=now())
        return JSONResponse(content=stored.as_dict())
