"""The console's Super-Admin control center (ADR-037, docs/plans/console-control-center-plan.md).

Every route needs the Super Admin role, checked here on each request. Every write also needs a
step-up grant (``X-Aura-Step-Up``, bought with a fresh Google sign-in) and the typed
confirmation the console asks for, re-checked here so a script cannot skip either. Responses
are ``no-store``. No route returns a secret value; the audit trail names what changed, never
the value.

Stage 1: overview, the AURA settings view (read), platform tokens (connect / replace /
disconnect) and the change journal. Platform actions arrive with their stages.
"""

# No `from __future__ import annotations`: request models are defined inside the register
# function, and FastAPI must resolve their annotations at decoration time (as in v3.py).
import os
from typing import Any, Callable

from ..control.catalog import build_catalog
from ..control.service import STEP_UP_HEADER
from ..domain.control import ChangeRecord, Platform
from ..domain.errors import ValidationError

__all__ = ["register_control_ops"]

_NO_STORE = {"Cache-Control": "no-store"}


def _change_json(change: ChangeRecord) -> dict[str, Any]:
    return {
        "change_id": change.change_id,
        "actor": change.actor,
        "platform": change.platform.value,
        "target": change.target,
        "action": change.action,
        "status": change.status.value,
        "detail": dict(change.detail),
        "deployment_id": change.deployment_id,
        "previous_deployment_id": change.previous_deployment_id,
        "reason": change.reason,
        "created_at": change.created_at.isoformat(),
        "updated_at": change.updated_at.isoformat(),
    }


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
        require_super_admin(caller)
        # Stage 1 reads the running process's environment; stage 2 reads Railway's variables.
        return ok({"source": "process", "groups": build_catalog(container.settings, os.environ)})

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
        return ok({"changes": [_change_json(c) for c in changes]})
