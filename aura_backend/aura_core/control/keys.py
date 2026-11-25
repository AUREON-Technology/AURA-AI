"""Provider API keys from the control center (ADR-037, stage 4).

Engine layer: domain and ports only, plus the Railway and Space services of stages 2 and 3.

A provider's key lives in one variable on the gateway (the slot's ``api_key_env``), so every key
change is a Railway variable change on the gateway itself: checked the same way, written, one
redeploy, watched, and rolled back with the old value if the gateway does not come back. On top
of that:

* **Replace** -- any slot: you paste the new value (Groq and others have no key API).
* **Rotate on both sides** -- the GPU Space's slot. The Space checks one key (vLLM takes a single
  ``--api-key``), so the two sides cannot overlap. The new key is generated here, set as the
  Space's ``VLLM_API_KEY`` secret (which restarts the Space), then written to the gateway. The
  gateway redeploys faster than the Space reloads its model, so the gap is the Space's restart,
  which any Space change costs anyway; the router fails over meanwhile. If the gateway change
  cannot start, the Space gets its old key back at once; if it is rolled back later, the
  reconciler puts the Space's old key back too (the change's companion restore). The old key is
  known because it is the gateway's own variable.
* **Create** -- a Workers AI slot: the management token creates a new Cloudflare token scoped to
  the slot's account, the gateway switches to it, and the old one keeps working until you revoke
  it (revoking the key in use is refused). A switch that cannot start revokes the token it made.

No key value is ever returned to the browser or written to the journal or audit.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..domain.control import KeySlot, Platform
from ..domain.errors import NotFoundError, ValidationError
from ..ports.control_plane import CloudflareTokens, ControlStore, SpaceApi
from .railway import RailwayService, change_json
from .service import ControlService, _confirm
from .space import SpaceService

__all__ = ["KeysService", "SPACE_KEY_SECRET"]

#: The Space secret vLLM checks (deploy/spaces/inference-vllm/entrypoint.sh: ``--api-key``).
SPACE_KEY_SECRET = "VLLM_API_KEY"
_MAX_KEY = 8192


def _refuse(message: str, reason: str, **details: Any) -> ValidationError:
    return ValidationError(message, details={"reason": reason, **details})


def space_host_label(space_id: str) -> str:
    """The subdomain the Hub serves ``owner/name`` at: lower case, ``/`` ``_`` ``.`` as ``-``."""
    return space_id.lower().replace("/", "-").replace("_", "-").replace(".", "-")


class KeysService:
    def __init__(
        self,
        *,
        control: ControlService,
        store: ControlStore,
        railway: RailwayService,
        space: SpaceService,
        space_api: SpaceApi,
        cloudflare: CloudflareTokens,
        slots: Sequence[KeySlot],
        provider_health: Callable[[], Sequence[Mapping[str, Any]]],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._control = control
        self._store = store
        self._railway = railway
        self._space = space
        self._space_api = space_api
        self._cloudflare = cloudflare
        self._slots = {s.name: s for s in slots}
        self._provider_health = provider_health
        self._now = now

    # -- what is possible right now --------------------------------------------------------------
    async def _connected(self, platform: Platform) -> bool:
        return await self._store.get_credential(platform) is not None

    async def _gateway(self) -> str | None:
        if self._railway.self_service_id and await self._connected(Platform.RAILWAY):
            return self._railway.self_service_id
        return None

    async def _space_id(self) -> str | None:
        credential = await self._store.get_credential(Platform.HF_SPACE)
        return credential.target if credential is not None and "/" in credential.target else None

    def _rotation(self, slot: KeySlot, space_id: str | None, cloudflare: bool) -> str:
        if slot.remote_host and space_id and slot.host.startswith(space_host_label(space_id) + "."):
            return "both_sides"
        if slot.account_id and cloudflare:
            return "create"
        return "replace"

    def _slot(self, name: str) -> KeySlot:
        slot = self._slots.get(name)
        if slot is None:
            raise NotFoundError("no provider with that name reads its key from a variable")
        return slot

    async def overview(self) -> dict[str, Any]:
        gateway = await self._gateway()
        space_id = await self._space_id()
        cloudflare = await self._connected(Platform.CLOUDFLARE)
        environ: Mapping[str, str] = await self._railway.environment(gateway) if gateway else {}
        health = {str(row.get("provider") or row.get("name")): row for row in self._provider_health()}
        in_use: dict[str, str | None] = {}
        rows = []
        for slot in self._slots.values():
            rotation = self._rotation(slot, space_id, cloudflare)
            if rotation == "create" and environ.get(slot.api_key_env):
                in_use[slot.name] = await self._cloudflare.identify(environ[slot.api_key_env])
            state = health.get(slot.name, {})
            rows.append({
                "name": slot.name,
                "kind": slot.kind,
                "host": slot.host,
                "api_key_env": slot.api_key_env,
                "key_set": bool(environ.get(slot.api_key_env)) if gateway else None,
                "rotation": rotation,
                "healthy": state.get("healthy"),
                "state": state.get("state"),
                "cloudflare_token_id": in_use.get(slot.name),
            })
        return {
            "gateway_ready": gateway is not None,
            "space_id": space_id,
            "cloudflare_ready": cloudflare,
            "slots": rows,
        }

    async def _require_gateway(self) -> tuple[str, str]:
        gateway = await self._gateway()
        if gateway is None:
            raise _refuse("connect Railway first: a key lives in the gateway's Railway variables",
                          "railway_not_connected")
        return gateway, (await self._railway.service(gateway)).name

    # -- replace (any slot) ------------------------------------------------------------------
    async def replace(self, name: str, value: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        slot = self._slot(name)
        _confirm(slot.name, confirm)
        value = (value or "").strip()
        if not value or len(value) > _MAX_KEY:
            raise _refuse("paste the new key", "value_required")
        gateway, service = await self._require_gateway()
        return await self._railway.apply_variables(
            gateway, {slot.api_key_env: value}, actor=actor, confirm=service,
            action="replace_key", extra_detail={"slot": slot.name},
        )

    async def rotate(self, name: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        """The rotation this slot supports: both sides for the GPU Space, a new Cloudflare token
        for a Workers AI slot. Any other slot can only be replaced with a value you paste."""
        slot = self._slot(name)
        rotation = self._rotation(slot, await self._space_id(), await self._connected(Platform.CLOUDFLARE))
        if rotation == "both_sides":
            return await self.rotate_space(name, actor=actor, confirm=confirm)
        if rotation == "create":
            return await self.rotate_cloudflare(name, actor=actor, confirm=confirm)
        raise _refuse("this provider's vendor has no key API here: replace the key with one you paste",
                      "replace_only")

    # -- both sides (the GPU Space) ----------------------------------------------------------
    async def rotate_space(self, name: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        slot = self._slot(name)
        _confirm(slot.name, confirm)
        gateway, service = await self._require_gateway()
        space_id = await self._space_id()
        if self._rotation(slot, space_id, cloudflare=False) != "both_sides" or space_id is None:
            raise _refuse("this provider is not the connected GPU Space", "not_the_space")
        old = (await self._railway.environment(gateway)).get(slot.api_key_env) or ""
        new = secrets.token_urlsafe(48)
        await self._space_api.set_secrets(space_id, {SPACE_KEY_SECRET: new})
        companion = {"platform": Platform.HF_SPACE.value, "target": space_id,
                     "secrets": {SPACE_KEY_SECRET: old}} if old else None
        try:
            return await self._railway.apply_variables(
                gateway, {slot.api_key_env: new}, actor=actor, confirm=service,
                action="rotate_key", companion=companion,
                extra_detail={"slot": slot.name, "space": space_id, "both_sides": True},
            )
        except Exception:
            # The gateway still sends the old key: give the Space that key back.
            if old:
                await self._space_api.set_secrets(space_id, {SPACE_KEY_SECRET: old})
            raise

    # -- Cloudflare ------------------------------------------------------------------------
    async def cloudflare_tokens(self) -> list[dict[str, Any]]:
        if not await self._connected(Platform.CLOUDFLARE):
            raise NotFoundError("Cloudflare is not connected; connect it in Settings")
        gateway = await self._gateway()
        environ = await self._railway.environment(gateway) if gateway else {}
        in_use = set()
        for slot in self._slots.values():
            if slot.account_id and environ.get(slot.api_key_env):
                token_id = await self._cloudflare.identify(environ[slot.api_key_env])
                if token_id:
                    in_use.add(token_id)
        return [
            {"token_id": t.token_id, "name": t.name, "status": t.status,
             "issued_on": t.issued_on.isoformat() if t.issued_on else None, "in_use": t.token_id in in_use}
            for t in await self._cloudflare.list()
        ]

    async def rotate_cloudflare(self, name: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        slot = self._slot(name)
        _confirm(slot.name, confirm)
        if not slot.account_id:
            raise _refuse("this provider is not a Workers AI slot", "not_workers_ai")
        if not await self._connected(Platform.CLOUDFLARE):
            raise _refuse("connect Cloudflare first, with a token that has API Tokens: Edit",
                          "cloudflare_not_connected")
        gateway, service = await self._require_gateway()
        stamp = self._now().strftime("%Y%m%d-%H%M")
        token_id, value = await self._cloudflare.create(name=f"aura-{slot.name}-{stamp}", account_id=slot.account_id)
        try:
            return await self._railway.apply_variables(
                gateway, {slot.api_key_env: value}, actor=actor, confirm=service,
                action="rotate_key", extra_detail={"slot": slot.name, "cloudflare_token_id": token_id},
            )
        except Exception:
            # Nothing uses the new token: do not leave it behind.
            await self._cloudflare.revoke(token_id)
            raise

    async def revoke_cloudflare(self, token_id: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        tokens = {t["token_id"]: t for t in await self.cloudflare_tokens()}
        token = tokens.get(token_id)
        if token is None:
            raise NotFoundError("Cloudflare has no such token")
        _confirm(token["name"], confirm)
        if token["in_use"]:
            raise _refuse("the gateway still uses that token; create a new key first", "token_in_use")
        await self._control.check_write_cap(actor)
        await self._cloudflare.revoke(token_id)
        return change_json(await self._control.journal(
            actor=actor, platform=Platform.CLOUDFLARE, target=token_id, action="revoke_key",
            detail={"service": token["name"], "token_id": token_id},
        ))
