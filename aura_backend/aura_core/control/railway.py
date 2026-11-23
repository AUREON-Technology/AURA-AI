"""Railway from the control center (ADR-037, stage 2): services, variables, deploys and logs.

Engine layer: it talks to Railway only through the ``RailwayApi`` port and checks a gateway's
candidate settings only through ``SettingsValidator``; ``container.py`` hands both in.

What makes a variable change safe to make from inside the gateway it may break:

1. **Typed confirmation** (the service's name) and a step-up grant (checked by the route).
2. **One change at a time per service**: a second change is refused while the first is watched,
   so a rollback can never undo the wrong thing.
3. **Refused outright**: the protected variables (they hold the control center's own vault,
   data key and journal), Railway's own ``RAILWAY_*`` variables, and ``${{...}}`` references
   (their rendered value is unknown until Railway renders it, so it cannot be validated).
4. **A service that runs AURA** (any ``AURA_*`` variable) must have a health-check path, or
   Railway would switch traffic to a deployment that never proved it boots; and the candidate
   environment must pass ``SettingsValidator`` -- the same settings code, in a child process --
   before anything is written.
5. **Written without a deploy, then one redeploy** of the current image, whose id is journaled
   with the variables' previous raw values (references kept) in the vault. The reconciler
   watches it and, if it fails or is not healthy in time, restores the values and rolls back.

Nothing here returns a secret value: variables come back as name, set, and the value only when
the name and value are not secret; logs have every secret value of the service masked.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..domain.control import PROTECTED_VARIABLES, ChangeRecord, ChangeStatus, Platform, is_secret_variable
from ..domain.errors import NotFoundError, ValidationError
from ..ports.control_plane import DeploymentSummary, RailwayApi, ServiceSummary, SettingsValidator
from .service import ON_SUCCESS_KEY, ControlService, _confirm

__all__ = ["COMPANION_KEY", "RailwayService", "change_json"]

#: The entry in a change's restore values that holds a second platform's restore (stage 4).
COMPANION_KEY = "__companion__"

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAX_VALUE = 32_768
_MAX_CHANGES = 50
_REFERENCE = "${{"
_MASK = "[redacted]"


def change_json(change: ChangeRecord) -> dict[str, Any]:
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


def _refuse(message: str, reason: str, **details: Any) -> ValidationError:
    return ValidationError(message, details={"reason": reason, **details})


def runs_aura(variables: Mapping[str, str]) -> bool:
    return any(name.startswith("AURA_") for name in variables)


class RailwayService:
    def __init__(
        self,
        *,
        control: ControlService,
        api: RailwayApi,
        validator: SettingsValidator,
        self_service_id: str = "",
    ) -> None:
        self._control = control
        self._api = api
        self._validator = validator
        #: ``RAILWAY_SERVICE_ID`` of the running gateway: the service the AURA settings tab edits.
        self.self_service_id = self_service_id

    # -- reading -----------------------------------------------------------------------------
    async def overview(self) -> dict[str, Any]:
        scope = await self._api.scope()
        services = await self._api.services()
        open_targets = {c.target for c in await self._control.open_changes(Platform.RAILWAY)}
        return {
            "project": {"id": scope.project_id, "name": scope.project_name},
            "environment": {"id": scope.environment_id, "name": scope.environment_name},
            "services": [self._service_json(s, open_targets) for s in services],
        }

    def _service_json(self, service: ServiceSummary, open_targets: set[str]) -> dict[str, Any]:
        return {
            "service_id": service.service_id,
            "name": service.name,
            "latest_deployment_id": service.latest_deployment_id,
            "latest_status": service.latest_status,
            "latest_at": service.latest_at.isoformat() if service.latest_at else None,
            "healthcheck_path": service.healthcheck_path,
            "is_gateway": bool(self.self_service_id) and service.service_id == self.self_service_id,
            "change_in_progress": service.service_id in open_targets,
        }

    async def service(self, service_id: str) -> ServiceSummary:
        for service in await self._api.services():
            if service.service_id == service_id:
                return service
        raise NotFoundError("this environment has no such Railway service")

    async def variables(self, service_id: str) -> list[dict[str, Any]]:
        await self.service(service_id)
        rendered = await self._api.variables(service_id)
        raw = await self._api.variables(service_id, rendered=False)
        rows = []
        for name in sorted(rendered):
            value = rendered[name]
            secret = is_secret_variable(name, value) or is_secret_variable(name, raw.get(name))
            rows.append({
                "name": name,
                "secret": secret,
                "value": None if secret else value,
                "reference": _REFERENCE in (raw.get(name) or ""),
                "protected": name in PROTECTED_VARIABLES,
                "provided": name.startswith("RAILWAY_"),
            })
        return rows

    async def deployments(self, service_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        await self.service(service_id)
        return [self._deployment_json(d) for d in await self._api.deployments(service_id, limit=limit)]

    @staticmethod
    def _deployment_json(d: DeploymentSummary) -> dict[str, Any]:
        return {
            "deployment_id": d.deployment_id, "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "can_rollback": d.can_rollback, "can_redeploy": d.can_redeploy,
            "commit": d.commit, "message": d.message,
        }

    async def logs(self, service_id: str, deployment_id: str, *, kind: str, limit: int) -> list[dict[str, str]]:
        await self._deployment_of(service_id, deployment_id)
        lines = await self._api.logs(deployment_id, kind=kind, limit=limit)
        rendered = await self._api.variables(service_id)
        secrets = sorted(
            {v for k, v in rendered.items() if v and len(v) >= 6 and is_secret_variable(k, v)},
            key=len, reverse=True,
        )

        def mask(text: str) -> str:
            for value in secrets:
                text = text.replace(value, _MASK)
            return text

        return [{"timestamp": l.timestamp, "severity": l.severity, "message": mask(l.message)} for l in lines]

    async def _deployment_of(self, service_id: str, deployment_id: str) -> DeploymentSummary:
        for deployment in await self._api.deployments(service_id, limit=50):
            if deployment.deployment_id == deployment_id:
                return deployment
        raise NotFoundError("that deployment is not one of this service's last 50")

    async def environment(self, service_id: str) -> Mapping[str, str]:
        """The rendered variables, for the settings catalog. Server-side only."""
        return await self._api.variables(service_id)

    # -- checking a change -------------------------------------------------------------------
    def _clean(self, changes: Mapping[str, str | None], raw: Mapping[str, str]) -> dict[str, str | None]:
        if not changes:
            raise _refuse("there is nothing to change", "no_changes")
        if len(changes) > _MAX_CHANGES:
            raise _refuse(f"at most {_MAX_CHANGES} variables per change", "too_many_changes")
        clean: dict[str, str | None] = {}
        for name, value in changes.items():
            if not _NAME.match(name or ""):
                raise _refuse("a variable name is letters, digits and _ and starts with a letter or _",
                              "bad_name", variable=str(name)[:64])
            if name in PROTECTED_VARIABLES:
                raise _refuse(f"{name} can only be changed in the Railway dashboard: changing it from "
                              "here could lock the control center out of its own data",
                              "protected_variable", variable=name)
            if name.startswith("RAILWAY_"):
                raise _refuse(f"{name} is provided by Railway", "provided_variable", variable=name)
            if value is not None:
                if len(value) > _MAX_VALUE:
                    raise _refuse(f"{name} is too long", "value_too_long", variable=name)
                if _REFERENCE in value:
                    raise _refuse("references (${{...}}) are set in the Railway dashboard: their value "
                                  "is not known until Railway renders it, so it cannot be checked first",
                                  "reference_not_supported", variable=name)
            if value is None and name not in raw:
                continue
            if value is not None and raw.get(name) == value:
                continue
            clean[name] = value
        if not clean:
            raise _refuse("every value is already what you asked for", "no_changes")
        return clean

    async def _check(
        self, service: ServiceSummary, changes: Mapping[str, str | None]
    ) -> tuple[dict[str, str | None], dict[str, str], bool]:
        raw = dict(await self._api.variables(service.service_id, rendered=False))
        clean = self._clean(changes, raw)
        rendered = dict(await self._api.variables(service.service_id))
        candidate = {k: v for k, v in rendered.items() if k not in clean}
        candidate.update({k: v for k, v in clean.items() if v is not None})
        aura = runs_aura(rendered) or runs_aura(candidate)
        if aura:
            if not service.healthcheck_path:
                raise _refuse(
                    f"{service.name} has no health-check path, so Railway would switch traffic to a "
                    "deployment that never proved it boots. Set it to /health/live in Railway "
                    "(service Settings -> Deploy -> Healthcheck Path), then try again",
                    "healthcheck_not_configured",
                )
            problem = await self._validator.validate(candidate)
            if problem:
                raise _refuse(f"the gateway would not start with this change: {problem}", "settings_invalid")
        return clean, raw, aura

    async def validate(self, service_id: str, changes: Mapping[str, str | None]) -> dict[str, Any]:
        """A dry run: everything ``apply_variables`` checks, nothing written."""
        service = await self.service(service_id)
        clean, _raw, aura = await self._check(service, changes)
        return {"ok": True, "settings_checked": aura, "variables": self._redacted(clean, {})}

    @staticmethod
    def _redacted(clean: Mapping[str, str | None], raw: Mapping[str, str]) -> list[dict[str, Any]]:
        return [
            {"name": name, "secret": is_secret_variable(name, value) or is_secret_variable(name, raw.get(name)),
             "deleted": value is None, "added": name not in raw}
            for name, value in sorted(clean.items())
        ]

    async def _not_busy(self, service: ServiceSummary) -> None:
        for change in await self._control.open_changes(Platform.RAILWAY):
            if change.target == service.service_id:
                raise _refuse(
                    f"a change to {service.name} is still being watched; wait for it to finish",
                    "change_in_progress", change_id=change.change_id,
                )

    async def _last_good(self, service_id: str) -> str | None:
        for deployment in await self._api.deployments(service_id, limit=20):
            if deployment.status.upper() == "SUCCESS":
                return deployment.deployment_id
        return None

    # -- acting ------------------------------------------------------------------------------
    async def apply_variables(
        self, service_id: str, changes: Mapping[str, str | None], *, actor: str, confirm: str | None,
        action: str = "set_variables", extra_detail: Mapping[str, Any] | None = None,
        companion: Mapping[str, Any] | None = None,
        on_success: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``companion`` is a second platform's restore (stage 4: the Space's key when both
        sides of a key rotate): ``{"platform": ..., "target": ..., "secrets": {name: old}}``. It
        is kept in the vault with the variables and put back by the reconciler on a rollback.

        ``on_success`` is a follow-up for the reconciler to run only after the change has stayed
        healthy for its whole watch (stage 6: revoke a removed provider's Cloudflare token):
        ``{"action": ..., ...}``, held in the vault like the companion and discarded by a rollback.
        It must hold no secret value."""
        service = await self.service(service_id)
        _confirm(service.name, confirm)
        await self._control.check_write_cap(actor)
        await self._not_busy(service)
        clean, raw, aura = await self._check(service, changes)
        restore: dict[str, Any] = {name: raw.get(name) for name in clean}
        if companion:
            restore[COMPANION_KEY] = dict(companion)
        if on_success:
            restore[ON_SUCCESS_KEY] = dict(on_success)
        previous = await self._last_good(service_id)
        detail = {"service": service.name, "variables": self._redacted(clean, raw), "settings_checked": aura,
                  **dict(extra_detail or {})}
        await self._api.set_variables(service_id, clean)
        try:
            deployment_id = await self._api.redeploy(service_id)
        except Exception:
            # Nothing is running the new values yet: put the old ones back before saying so.
            await self._api.set_variables(
                service_id, {k: v for k, v in restore.items() if k not in (COMPANION_KEY, ON_SUCCESS_KEY)},
            )
            await self._control.journal(
                actor=actor, platform=Platform.RAILWAY, target=service_id, action=action,
                detail=detail, status=ChangeStatus.FAILED,
                reason="the redeploy could not be started; the variables were put back",
            )
            raise
        record = await self._control.watch(
            actor=actor, platform=Platform.RAILWAY, target=service_id, action=action,
            detail=detail, deployment_id=deployment_id, previous_deployment_id=previous,
            restore_values=restore,
        )
        return change_json(record)

    async def redeploy(self, service_id: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        service = await self.service(service_id)
        _confirm(service.name, confirm)
        await self._control.check_write_cap(actor)
        await self._not_busy(service)
        previous = await self._last_good(service_id)
        deployment_id = await self._api.redeploy(service_id)
        record = await self._control.watch(
            actor=actor, platform=Platform.RAILWAY, target=service_id, action="redeploy",
            detail={"service": service.name}, deployment_id=deployment_id,
            previous_deployment_id=previous, restore_values=None,
        )
        return change_json(record)

    async def restart(self, service_id: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        service = await self.service(service_id)
        _confirm(service.name, confirm)
        await self._control.check_write_cap(actor)
        if not service.latest_deployment_id:
            raise NotFoundError("the service has no deployment to restart")
        await self._api.restart(service.latest_deployment_id)
        record = await self._control.journal(
            actor=actor, platform=Platform.RAILWAY, target=service_id, action="restart",
            detail={"service": service.name, "deployment_id": service.latest_deployment_id},
        )
        return change_json(record)

    async def rollback(
        self, service_id: str, deployment_id: str, *, actor: str, confirm: str | None,
    ) -> dict[str, Any]:
        """A deliberate rollback to a chosen earlier deployment. Journaled, not watched: the
        reconciler undoing a rollback would only pick between two states a person rejected."""
        service = await self.service(service_id)
        _confirm(service.name, confirm)
        await self._control.check_write_cap(actor)
        await self._not_busy(service)
        target = await self._deployment_of(service_id, deployment_id)
        if not target.can_rollback:
            raise _refuse("Railway cannot roll back to that deployment", "rollback_not_possible")
        new_id = await self._api.rollback(service_id, deployment_id)
        record = await self._control.journal(
            actor=actor, platform=Platform.RAILWAY, target=service_id, action="rollback",
            detail={"service": service.name, "to_deployment_id": deployment_id, "deployment_id": new_id},
        )
        return change_json(record)

