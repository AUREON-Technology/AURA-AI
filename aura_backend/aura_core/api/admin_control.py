"""The console's Super-Admin control center (ADR-037, docs/plans/console-control-center-plan.md).

Every route needs the Super Admin role, checked here on each request. Every write also needs a
step-up grant (``X-Aura-Step-Up``, bought with a fresh Google sign-in) and the typed
confirmation the console asks for, re-checked here so a script cannot skip either. Responses
are ``no-store``. No route returns a secret value; the audit trail names what changed, never
the value.

Stage 1: overview, the AURA settings view, platform tokens (connect / replace / disconnect) and
the change journal. Stage 2: Railway -- services, variables (validated, then applied with one
redeploy that the reconciler watches), redeploy, restart, rollback, deployments and logs; and the
AURA settings view reads, and edits, the gateway's own Railway variables.
"""

# No `from __future__ import annotations`: request models are defined inside the register
# function, and FastAPI must resolve their annotations at decoration time (as in v3.py).
import os
from typing import Any, Callable, Optional

from ..control.catalog import build_catalog
from ..control.railway import change_json
from ..control.service import STEP_UP_HEADER
from ..domain.control import Platform
from ..domain.errors import ValidationError

__all__ = ["register_control_ops"]

_NO_STORE = {"Cache-Control": "no-store"}


def _platform(value: str) -> Platform:
    try:
        return Platform(value)
    except ValueError as exc:
        raise ValidationError(
            "unknown platform", details={"platform": value[:32], "supported": [p.value for p in Platform]}
        ) from exc


def register_control_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    require_super_admin: Callable,
    audit_admin: Callable,
    Depends: Any,
    JSONResponse: Any,
    Request: Any,
) -> None:
    from pydantic import BaseModel, ConfigDict, Field

    class RedeemRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        id_token: str = Field(min_length=1, max_length=8192)
        nonce: str = Field(min_length=1, max_length=128)

    class ConnectRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        token: str = Field(min_length=1, max_length=8192)
        target: str = Field(default="", max_length=256)
        confirm: str = Field(default="", max_length=64)

    class ConfirmRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        confirm: str = Field(default="", max_length=64)

    class VariablesRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        #: name -> new value; ``null`` deletes the variable.
        changes: dict[str, Optional[str]] = Field(min_length=1, max_length=50)
        confirm: str = Field(default="", max_length=128)

    class ServiceConfirmRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        confirm: str = Field(default="", max_length=128)

    class RollbackRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        deployment_id: str = Field(min_length=1, max_length=128)
        confirm: str = Field(default="", max_length=128)

    def actor(caller: Any) -> str:
        return caller.email or caller.user_id.value

    async def stepped_up(request: Any, caller: Any) -> None:
        require_super_admin(caller)
        await container.step_up.require(
            subject=caller.user_id.value, grant=request.headers.get(STEP_UP_HEADER)
        )

    def ok(content: Any, status_code: int = 200) -> Any:
        return JSONResponse(status_code=status_code, content=content, headers=_NO_STORE)

    @router.get("/admin/control/overview")
    async def control_overview(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        overview = await container.control.overview()
        overview["release"] = container.settings.as_dict()["release"]
        overview["environment"] = str(container.settings.environment)
        return ok(overview)

    @router.get("/admin/control/settings")
    async def control_settings(caller=Depends(principal)) -> Any:
        """The catalog, read from the gateway's own Railway variables when Railway is connected
        and this gateway runs there (so it can be edited); otherwise from this process."""
        require_super_admin(caller)
        railway = container.railway
        service_id = railway.self_service_id
        if service_id and await container.control_store.get_credential(Platform.RAILWAY) is not None:
            try:
                service = await railway.service(service_id)
                environ = await railway.environment(service_id)
            except Exception as exc:
                return ok({"source": "process", "editable": False, "railway_error": type(exc).__name__,
                           "groups": build_catalog(container.settings, os.environ)})
            return ok({"source": "railway", "editable": True, "service_id": service_id,
                       "service_name": service.name,
                       "groups": build_catalog(container.settings, environ)})
        return ok({"source": "process", "editable": False,
                   "groups": build_catalog(container.settings, os.environ)})

    @router.post("/admin/control/step-up/challenge")
    async def step_up_challenge(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        challenge = await container.step_up.challenge(subject=caller.user_id.value, email=caller.email)
        return ok({
            "nonce": challenge.nonce,
            "expires_at": challenge.expires_at.isoformat(),
            "max_age_s": container.settings.control.step_up_max_age_s,
        })

    @router.post("/admin/control/step-up")
    async def step_up_redeem(payload: RedeemRequest, request: Request, caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        try:
            grant, expires = await container.step_up.redeem(
                subject=caller.user_id.value, email=caller.email,
                id_token=payload.id_token, nonce=payload.nonce,
            )
        except Exception:
            await audit_admin(request, caller, "admin.control.step_up", "control", "refused")
            raise
        await audit_admin(request, caller, "admin.control.step_up", "control", "granted")
        return ok({"grant": grant, "expires_at": expires.isoformat()})

    @router.post("/admin/control/step-up/revoke")
    async def step_up_revoke(caller=Depends(principal)) -> Any:
        """Drops every grant this account holds. The console's sign-out calls it, so a grant
        never outlives the session that bought it (the access token alone stays valid until it
        expires, and with a grant it could still write)."""
        require_super_admin(caller)
        dropped = await container.step_up.revoke(caller.user_id.value)
        return ok({"revoked": dropped})

    @router.post("/admin/control/platforms/{platform}/credential")
    async def connect_platform(
        platform: str, payload: ConnectRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        target_platform = _platform(platform)
        await stepped_up(request, caller)
        try:
            result = await container.control.connect(
                platform=target_platform, token=payload.token, target=payload.target,
                actor=actor(caller), confirm=payload.confirm,
            )
        except Exception as exc:
            await audit_admin(request, caller, "admin.control.connect", target_platform.value,
                              "refused", detail={"error": type(exc).__name__})
            raise
        await audit_admin(request, caller, "admin.control.connect", target_platform.value, "success",
                          detail={"verified": result["verified"]})
        return ok(result)

    @router.post("/admin/control/platforms/{platform}/disconnect")
    async def disconnect_platform(
        platform: str, payload: ConfirmRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        target_platform = _platform(platform)
        await stepped_up(request, caller)
        await container.control.disconnect(
            platform=target_platform, actor=actor(caller), confirm=payload.confirm
        )
        await audit_admin(request, caller, "admin.control.disconnect", target_platform.value, "success")
        return ok({"platform": target_platform.value, "connected": False})

    @router.get("/admin/control/changes")
    async def control_changes(limit: int = 50, caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        changes = await container.control.changes(limit=limit)
        return ok({"changes": [change_json(c) for c in changes]})

    # -- Railway (stage 2) ------------------------------------------------------------------
    @router.get("/admin/control/railway")
    async def railway_overview(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok(await container.railway.overview())

    @router.get("/admin/control/railway/services/{service_id}/variables")
    async def railway_variables(service_id: str, caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok({"variables": await container.railway.variables(service_id)})

    @router.get("/admin/control/railway/services/{service_id}/deployments")
    async def railway_deployments(service_id: str, limit: int = 20, caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        limit = max(1, min(limit, 50))
        return ok({"deployments": await container.railway.deployments(service_id, limit=limit)})

    @router.get("/admin/control/railway/services/{service_id}/deployments/{deployment_id}/logs")
    async def railway_logs(
        service_id: str, deployment_id: str, kind: str = "deploy", limit: int = 200,
        caller=Depends(principal),
    ) -> Any:
        require_super_admin(caller)
        if kind not in ("deploy", "build"):
            raise ValidationError("logs are 'deploy' or 'build'")
        lines = await container.railway.logs(
            service_id, deployment_id, kind=kind, limit=max(1, min(limit, 1000))
        )
        return ok({"lines": lines})

    @router.post("/admin/control/railway/services/{service_id}/variables/validate")
    async def railway_validate(service_id: str, payload: VariablesRequest, caller=Depends(principal)) -> Any:
        """A dry run of a variable change: nothing is written, so no step-up is needed."""
        require_super_admin(caller)
        return ok(await container.railway.validate(service_id, payload.changes))

    async def railway_write(request: Any, caller: Any, action: str, service_id: str, run: Callable) -> Any:
        await stepped_up(request, caller)
        try:
            result = await run()
        except Exception as exc:
            await audit_admin(request, caller, f"admin.control.railway.{action}", service_id, "refused",
                              detail={"error": type(exc).__name__,
                                      "reason": str((getattr(exc, "details", None) or {}).get("reason", ""))})
            raise
        await audit_admin(request, caller, f"admin.control.railway.{action}", service_id, "success",
                          detail={"change_id": result["change_id"], "status": result["status"]})
        return ok(result)

    @router.post("/admin/control/railway/services/{service_id}/variables")
    async def railway_set_variables(
        service_id: str, payload: VariablesRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        return await railway_write(request, caller, "set_variables", service_id, lambda: container.railway.apply_variables(
            service_id, payload.changes, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/railway/services/{service_id}/redeploy")
    async def railway_redeploy(
        service_id: str, payload: ServiceConfirmRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        return await railway_write(request, caller, "redeploy", service_id, lambda: container.railway.redeploy(
            service_id, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/railway/services/{service_id}/restart")
    async def railway_restart(
        service_id: str, payload: ServiceConfirmRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        return await railway_write(request, caller, "restart", service_id, lambda: container.railway.restart(
            service_id, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/railway/services/{service_id}/rollback")
    async def railway_rollback(
        service_id: str, payload: RollbackRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        return await railway_write(request, caller, "rollback", service_id, lambda: container.railway.rollback(
            service_id, payload.deployment_id, actor=actor(caller), confirm=payload.confirm,
        ))
