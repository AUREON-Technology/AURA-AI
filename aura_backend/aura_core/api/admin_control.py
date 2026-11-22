"""The console's Super-Admin control center (ADR-037, docs/plans/console-control-center-plan.md).

Every route needs the Super Admin role, checked here on each request. Every write also needs a
step-up grant (``X-Aura-Step-Up``, bought with a fresh Google sign-in) and the typed
confirmation the console asks for, re-checked here so a script cannot skip either. Responses
are ``no-store``. No route returns a secret value; the audit trail names what changed, never
the value.

Stage 1: overview, the AURA settings view, platform tokens (connect / replace / disconnect) and
the change journal. Stage 2: Railway -- services, variables (validated, then applied with one
redeploy that the reconciler watches), redeploy, restart, rollback, deployments and logs; and the
AURA settings view reads, and edits, the gateway's own Railway variables. Stage 3: the GPU
Space -- status, variables (watched and undone on failure), write-only secrets, pause, restart,
rebuild, hardware, sleep time and logs. Stage 4: provider keys -- replace, rotate both sides
(gateway and Space), create a new Cloudflare key and revoke unused ones. Stage 5: Grafana Cloud
(rule groups applied, scrape and uptime status, firing alerts) and Qdrant Cloud (health,
collections, snapshots, and a restore that snapshots the current state first).
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

    class SpaceSecretsRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        #: name -> new value; ``null`` deletes the secret. Values are write-only.
        changes: dict[str, Optional[str]] = Field(min_length=1, max_length=30)
        confirm: str = Field(default="", max_length=128)

    class SpaceRestartRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        confirm: str = Field(default="", max_length=128)
        #: Rebuild the image from scratch instead of restarting it.
        factory: bool = False

    class SpaceHardwareRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        hardware: str = Field(min_length=1, max_length=32)
        confirm: str = Field(default="", max_length=128)

    class SpaceSleepRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        seconds: int
        confirm: str = Field(default="", max_length=128)

    class KeyReplaceRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str = Field(min_length=1, max_length=8192)
        confirm: str = Field(default="", max_length=128)

    class SnapshotRestoreRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        snapshot: str = Field(min_length=1, max_length=255)
        confirm: str = Field(default="", max_length=255)

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

    async def railway_write(
        request: Any, caller: Any, action: str, service_id: str, run: Callable,
        *, audit_prefix: str = "admin.control.railway",
    ) -> Any:
        await stepped_up(request, caller)
        try:
            result = await run()
        except Exception as exc:
            await audit_admin(request, caller, f"{audit_prefix}.{action}", service_id, "refused",
                              detail={"error": type(exc).__name__,
                                      "reason": str((getattr(exc, "details", None) or {}).get("reason", ""))})
            raise
        await audit_admin(request, caller, f"{audit_prefix}.{action}", service_id, "success",
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

    # -- GPU Space (stage 3) ----------------------------------------------------------------
    @router.get("/admin/control/space")
    async def space_overview(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok(await container.space.overview())

    @router.get("/admin/control/space/variables")
    async def space_variables(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok({"variables": await container.space.variables()})

    @router.get("/admin/control/space/secrets")
    async def space_secrets(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok({"secrets": await container.space.secrets()})

    @router.get("/admin/control/space/logs")
    async def space_logs(kind: str = "run", limit: int = 200, caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok({"lines": await container.space.logs(kind=kind, limit=max(1, min(limit, 1000)))})

    @router.post("/admin/control/space/variables/validate")
    async def space_validate(payload: VariablesRequest, caller=Depends(principal)) -> Any:
        """A dry run: nothing is written, so no step-up is needed."""
        require_super_admin(caller)
        return ok(await container.space.validate(payload.changes))

    async def space_write(request: Any, caller: Any, action: str, run: Callable) -> Any:
        return await railway_write(request, caller, action, "space", run, audit_prefix="admin.control.space")

    @router.post("/admin/control/space/variables")
    async def space_set_variables(payload: VariablesRequest, request: Request, caller=Depends(principal)) -> Any:
        return await space_write(request, caller, "set_variables", lambda: container.space.apply_variables(
            payload.changes, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/space/secrets")
    async def space_set_secrets(payload: SpaceSecretsRequest, request: Request, caller=Depends(principal)) -> Any:
        return await space_write(request, caller, "set_secrets", lambda: container.space.set_secrets(
            payload.changes, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/space/pause")
    async def space_pause(payload: ServiceConfirmRequest, request: Request, caller=Depends(principal)) -> Any:
        return await space_write(request, caller, "pause", lambda: container.space.pause(
            actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/space/restart")
    async def space_restart(payload: SpaceRestartRequest, request: Request, caller=Depends(principal)) -> Any:
        return await space_write(request, caller, "restart", lambda: container.space.restart(
            actor=actor(caller), confirm=payload.confirm, factory=payload.factory,
        ))

    @router.post("/admin/control/space/hardware")
    async def space_hardware(payload: SpaceHardwareRequest, request: Request, caller=Depends(principal)) -> Any:
        return await space_write(request, caller, "hardware", lambda: container.space.request_hardware(
            payload.hardware, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/space/sleep-time")
    async def space_sleep_time(payload: SpaceSleepRequest, request: Request, caller=Depends(principal)) -> Any:
        return await space_write(request, caller, "sleep_time", lambda: container.space.set_sleep_time(
            payload.seconds, actor=actor(caller), confirm=payload.confirm,
        ))

    # -- Provider keys (stage 4) ------------------------------------------------------------
    @router.get("/admin/control/keys")
    async def keys_overview(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok(await container.keys.overview())

    @router.get("/admin/control/keys/cloudflare/tokens")
    async def keys_cloudflare_tokens(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok({"tokens": await container.keys.cloudflare_tokens()})

    async def keys_write(request: Any, caller: Any, action: str, target: str, run: Callable) -> Any:
        return await railway_write(request, caller, action, target, run, audit_prefix="admin.control.keys")

    @router.post("/admin/control/keys/{slot}/replace")
    async def keys_replace(slot: str, payload: KeyReplaceRequest, request: Request, caller=Depends(principal)) -> Any:
        return await keys_write(request, caller, "replace", slot, lambda: container.keys.replace(
            slot, payload.value, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/keys/{slot}/rotate")
    async def keys_rotate(slot: str, payload: ServiceConfirmRequest, request: Request, caller=Depends(principal)) -> Any:
        return await keys_write(request, caller, "rotate", slot, lambda: container.keys.rotate(
            slot, actor=actor(caller), confirm=payload.confirm,
        ))

    @router.post("/admin/control/keys/cloudflare/tokens/{token_id}/revoke")
    async def keys_revoke(token_id: str, payload: ServiceConfirmRequest, request: Request, caller=Depends(principal)) -> Any:
        return await keys_write(request, caller, "revoke", token_id, lambda: container.keys.revoke_cloudflare(
            token_id, actor=actor(caller), confirm=payload.confirm,
        ))

    # -- Grafana Cloud and Qdrant Cloud (stage 5) -------------------------------------------
    def public_url(request: Any) -> str:
        configured = container.settings.control.public_api_url
        if configured:
            return configured
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
        return f"{proto}://{request.url.netloc}"

    @router.get("/admin/control/monitoring")
    async def monitoring_overview(request: Request, caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok(await container.monitoring.overview(public_url=public_url(request)))

    @router.post("/admin/control/monitoring/apply")
    async def monitoring_apply(payload: ConfirmRequest, request: Request, caller=Depends(principal)) -> Any:
        async def run() -> Any:
            result = await container.monitoring.apply(actor=actor(caller), confirm=payload.confirm)
            return {**result["change"], "results": result["results"]}

        return await railway_write(request, caller, "apply", "grafana", run, audit_prefix="admin.control.monitoring")

    @router.get("/admin/control/vector")
    async def vector_overview(caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok(await container.vector.overview())

    @router.get("/admin/control/vector/collections/{collection}/snapshots")
    async def vector_snapshots(collection: str, caller=Depends(principal)) -> Any:
        require_super_admin(caller)
        return ok({"snapshots": await container.vector.snapshots(collection)})

    @router.post("/admin/control/vector/collections/{collection}/snapshots")
    async def vector_create_snapshot(
        collection: str, payload: ServiceConfirmRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        async def run() -> Any:
            result = await container.vector.create_snapshot(collection, actor=actor(caller), confirm=payload.confirm)
            return {**result["change"], "snapshot": result["snapshot"]}

        return await railway_write(request, caller, "snapshot", collection, run, audit_prefix="admin.control.vector")

    @router.post("/admin/control/vector/collections/{collection}/restore")
    async def vector_restore(
        collection: str, payload: SnapshotRestoreRequest, request: Request, caller=Depends(principal)
    ) -> Any:
        async def run() -> Any:
            result = await container.vector.restore(
                collection, payload.snapshot, actor=actor(caller), confirm=payload.confirm,
            )
            return {**result["change"], "safety_snapshot": result["safety_snapshot"]}

        return await railway_write(request, caller, "restore", collection, run, audit_prefix="admin.control.vector")
