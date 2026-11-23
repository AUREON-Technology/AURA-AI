"""Workspace lifecycle and the paired-desktop status (ADR-041, roadmap row 89).

* ``POST /api/v3/engineering/workspaces/{id}/start`` | ``/stop`` | ``/restart``
* ``GET  /api/v3/engineering/workspaces/{id}/stats``
* ``GET  /api/v3/host/status``

These expose what ``WorkspaceManager`` already does inside a turn, to the person who owns the
workspace. They add no policy of their own: ``start`` and ``restart`` go through the manager's
admission (the running limit, the host-memory guard), so a request cannot take a slot or memory a
turn could not, and a refusal is a clear ``409 workspace_capacity`` (retryable) rather than a
generic failure. A workspace that is not the caller's reads as 404, exactly like one that does not
exist (the lookup *is* the ownership check).

**The paired desktop cannot be started or stopped from here, by design.** The desktop daemon
(``aura_hostd``) is the user's own process on the user's own computer; it connects *out* to the
gateway, and nothing on the server may spawn, kill or restart software on someone's machine --
that would be remote control of a personal computer, which ADR-011 deliberately does not offer.
``GET /host/status`` is therefore read-only: whether the daemon is connected, whether it allows
control from other devices, and the folders it currently grants.
"""

# No `from __future__ import annotations`: FastAPI resolves handler annotations at decoration time.
from typing import Any, Callable

from ..domain.engineering import WorkspaceStatus
from ..observability.logging import get_logger
from ..tools.workspaces import WorkspaceCapacityError

__all__ = ["register_engineering_ops"]

logger = get_logger("api.engineering_ops")


def register_engineering_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    enforce_rate_limit: Callable,
    idempotent: list,
    Depends: Any,
    HTTPException: Any,
    JSONResponse: Any,
    Request: Any,
) -> None:
    def _manager() -> Any:
        manager = getattr(container, "workspace_manager", None)
        if manager is None:
            raise HTTPException(status_code=404, detail="workspaces are not enabled")
        return manager

    async def _owned(caller: Any, workspace_id: str) -> Any:
        workspace = await _manager().get_for_user(caller.user_id, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return workspace

    def _view(workspace: Any) -> dict[str, Any]:
        ttl_hours = container.settings.sandbox.workspace_ttl_hours
        from datetime import timedelta

        return {
            "id": workspace.id.value,
            "repo": workspace.repo,
            "status": workspace.status,
            "branch": workspace.branch,
            "created_at": workspace.created_at.isoformat(),
            "last_active_at": workspace.last_active_at.isoformat(),
            "expires_at": (workspace.last_active_at + timedelta(hours=ttl_hours)).isoformat(),
        }

    async def _own_running(caller: Any) -> dict[str, str]:
        return {
            w.id.value: w.repo for w in await _manager().list_for_user(caller.user_id)
            if w.status == WorkspaceStatus.HOT
        }

    def _capacity_refused(exc: WorkspaceCapacityError) -> Any:
        # A refusal, not a fault: nothing was started, and it clears as other workspaces stop.
        return JSONResponse(status_code=409, content={
            "error": "workspace_capacity",
            "message": getattr(exc, "public_message", None) or str(exc),
            "retryable": True, "details": {},
        })

    async def _lifecycle(request: Any, caller: Any, workspace_id: str, action: str) -> Any:
        await enforce_rate_limit(request, caller, "search")
        manager = _manager()
        workspace = await _owned(caller, workspace_id)
        before = await _own_running(caller)
        evicted: tuple[str, ...] = ()
        was_running = workspace.id.value in before
        try:
            if action == "stop":
                changed = await manager.stop(workspace)
            elif action == "start":
                opened = await manager.start(workspace)
                evicted, changed = opened.evicted, not was_running
            else:
                opened = await manager.restart(workspace)
                evicted, changed = opened.evicted, True
        except WorkspaceCapacityError as exc:
            return _capacity_refused(exc)
        current = await manager.get_for_user(caller.user_id, workspace_id) or workspace
        after = await _own_running(caller)
        # Making room can pause someone else's workspace (the running limit is host-wide). The
        # caller is told how many, and which of *their own*; never another person's repository.
        paused_yours = sorted(repo for wid, repo in before.items()
                              if wid != workspace.id.value and wid not in after)
        return JSONResponse(content={
            "workspace": _view(current),
            "action": action,
            "changed": changed,
            "paused_to_make_room": len(evicted),
            "paused_yours": paused_yours,
        })

    @router.post("/engineering/workspaces/{workspace_id}/start", dependencies=idempotent)
    async def start_agent_workspace(
        request: Request, workspace_id: str, caller: Any = Depends(principal),
    ) -> JSONResponse:
        """Start (resume) one of the caller's workspaces. Idempotent. ``409 workspace_capacity``
        (retryable) when the host is at its running limit or short of memory; ``changed`` says
        whether this call actually started it, ``paused_to_make_room`` how many other workspaces
        were paused to fit it."""
        return await _lifecycle(request, caller, workspace_id, "start")

    @router.post("/engineering/workspaces/{workspace_id}/stop", dependencies=idempotent)
    async def stop_agent_workspace(
        request: Request, workspace_id: str, caller: Any = Depends(principal),
    ) -> JSONResponse:
        """Stop one of the caller's workspaces, keeping its files (it then costs disk only).
        Idempotent: ``changed`` is false when it was already stopped."""
        return await _lifecycle(request, caller, workspace_id, "stop")

    @router.post("/engineering/workspaces/{workspace_id}/restart", dependencies=idempotent)
    async def restart_agent_workspace(
        request: Request, workspace_id: str, caller: Any = Depends(principal),
    ) -> JSONResponse:
        """Stop then start. If the start is refused for capacity the workspace stays stopped and
        the answer is ``409 workspace_capacity``."""
        return await _lifecycle(request, caller, workspace_id, "restart")

    @router.get("/engineering/workspaces/{workspace_id}/stats")
    async def agent_workspace_stats(
        request: Request, workspace_id: str, caller: Any = Depends(principal),
    ) -> JSONResponse:
        """State, uptime, disk against its quota, and CPU/memory when the runner can report them.
        Reading never starts a stopped workspace. A figure the runner cannot supply is ``null`` and
        ``resources.reason`` says why -- it is never reported as zero."""
        await enforce_rate_limit(request, caller, "read")
        workspace = await _owned(caller, workspace_id)
        stats = await _manager().stats(workspace)
        return JSONResponse(content={"workspace": _view(workspace), **stats})

    @router.get("/host/status")
    async def host_status(request: Request, caller: Any = Depends(principal)) -> JSONResponse:
        """Read-only status of the caller's paired desktop daemon(s). ``daemons`` has at most one
        entry today (a new connection supersedes the old one); it is a list so a client need not
        change when that does. ``controllable_remotely`` is always false: the daemon is the person's
        own process on their own computer and is never started or stopped from the server."""
        await enforce_rate_limit(request, caller, "read")
        note = ("The desktop daemon runs on your own computer and connects out to this server; "
                "it can only be started or stopped there.")
        registry = getattr(container, "host_agent_registry", None)
        session = await registry.resolve(caller.user_id) if registry is not None else None
        base = {"controllable_remotely": False, "note": note}
        if session is None:
            return JSONResponse(content={"connected": False, "daemons": [], **base})
        entry: dict[str, Any] = {
            "kind": "desktop", "connected": True, "remote_control": None, "roots": [],
            "reachable": True,
        }
        try:
            result = await session.call("list_roots", {}, timeout_s=10.0)
            payload = result if isinstance(result, dict) else {}
            entry["remote_control"] = bool(payload.get("remote_control", False))
            entry["roots"] = [
                {"path": str(r.get("path")), "trusted": bool(r.get("trusted")),
                 "is_git": r.get("is_git") if isinstance(r.get("is_git"), bool) else None}
                for r in payload.get("roots") or [] if isinstance(r, dict) and r.get("path")
            ]
        except Exception:
            # Connected, but an older daemon without list_roots or one that went away mid-call.
            logger.info("desktop did not answer list_roots for the status route", exc_info=True)
            entry["reachable"] = False
        return JSONResponse(content={"connected": True, "daemons": [entry], **base})
