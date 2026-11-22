"""The inference Space from the control center (ADR-037, stage 3).

Engine layer: it reaches Hugging Face only through the ``SpaceApi`` port. The Space is the one
named when its token was connected (the HF credential's ``target``, ``owner/name``), so nothing
else has to be configured, and a token cannot be pointed at another Space from here.

What is different from Railway:

* **The Hub restarts a Space by itself** whenever a variable or secret changes. There is no
  deploy step to start, and no deployment id: the reconciler reads the Space's stage
  (``RUNNING`` is healthy, ``*_ERROR`` failed, ``PAUSED`` superseded).
* **A rebuild is slow** (an image build plus a model load on the GPU), so a Space change carries
  its own watch window (``AURA_CONTROL_SPACE_WATCH_S``, 20 minutes by default).
* **Variables are undone; secrets cannot be.** The Hub never returns a secret's value, so a
  replaced secret cannot be restored: secret changes are journaled, not watched, and the console
  says so before you make one. A variable whose name looks secret is refused: it belongs in the
  Space's secrets, where it is not readable.
* **Hardware costs money.** A hardware request is journaled (and asks for the Space's name like
  every write); it is not rolled back automatically, because the previous hardware may be what
  was failing.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..domain.control import SPACE_HARDWARE, Platform, is_secret_name, is_secret_variable
from ..domain.errors import NotFoundError, ValidationError
from ..ports.control_plane import ControlStore, SpaceApi
from .railway import change_json
from .service import ControlService, _confirm

__all__ = ["SpaceService", "SLEEP_CHOICES"]

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAX_VALUE = 32_768
_MAX_CHANGES = 30
#: What the console offers; the Hub takes any number of seconds, -1 meaning never.
SLEEP_CHOICES = (-1, 300, 900, 1800, 3600, 10_800, 36_000, 86_400, 172_800)


def _refuse(message: str, reason: str, **details: Any) -> ValidationError:
    return ValidationError(message, details={"reason": reason, **details})


class SpaceService:
    def __init__(self, *, control: ControlService, api: SpaceApi, store: ControlStore, watch_s: int) -> None:
        self._control = control
        self._api = api
        self._store = store
        self._watch_s = watch_s

    async def space_id(self) -> str:
        credential = await self._store.get_credential(Platform.HF_SPACE)
        if credential is None:
            raise NotFoundError("the GPU Space is not connected; connect it in Settings")
        if "/" not in credential.target:
            raise _refuse("reconnect the GPU Space with its owner/name", "space_not_named")
        return credential.target

    @staticmethod
    def short_name(space_id: str) -> str:
        return space_id.rsplit("/", 1)[-1]

    async def _busy(self, space_id: str) -> bool:
        return any(c.target == space_id for c in await self._control.open_changes(Platform.HF_SPACE))

    # -- reading -----------------------------------------------------------------------------
    async def overview(self) -> dict[str, Any]:
        space_id = await self.space_id()
        status = await self._api.status(space_id)
        return {
            "space_id": space_id,
            "name": self.short_name(space_id),
            "stage": status.stage,
            "hardware": status.hardware,
            "requested_hardware": status.requested_hardware,
            "sleep_time": status.sleep_time,
            "hardware_options": list(SPACE_HARDWARE),
            "sleep_choices": list(SLEEP_CHOICES),
            "change_in_progress": await self._busy(space_id),
            "watch_minutes": self._watch_s // 60,
        }

    async def variables(self) -> list[dict[str, Any]]:
        space_id = await self.space_id()
        found = await self._api.variables(space_id)
        rows = []
        for name in sorted(found):
            secret = is_secret_variable(name, found[name])
            rows.append({"name": name, "secret": secret, "value": None if secret else found[name]})
        return rows

    async def secrets(self) -> list[dict[str, Any]]:
        space_id = await self.space_id()
        return [
            {"name": s.name, "updated_at": s.updated_at.isoformat() if s.updated_at else None}
            for s in await self._api.secrets(space_id)
        ]

    async def logs(self, *, kind: str, limit: int) -> list[str]:
        if kind not in ("run", "build"):
            raise _refuse("Space logs are 'run' or 'build'", "bad_log_kind")
        space_id = await self.space_id()
        lines = await self._api.logs(space_id, kind=kind, limit=limit)
        found = await self._api.variables(space_id)
        hidden = sorted({v for k, v in found.items() if v and len(v) >= 6 and is_secret_variable(k, v)},
                        key=len, reverse=True)
        masked = []
        for line in lines:
            for value in hidden:
                line = line.replace(value, "[redacted]")
            masked.append(line)
        return masked

    # -- variables (watched, undone on failure) ----------------------------------------------
    def _clean(self, changes: Mapping[str, str | None], current: Mapping[str, str]) -> dict[str, str | None]:
        if not changes:
            raise _refuse("there is nothing to change", "no_changes")
        if len(changes) > _MAX_CHANGES:
            raise _refuse(f"at most {_MAX_CHANGES} variables per change", "too_many_changes")
        clean: dict[str, str | None] = {}
        for name, value in changes.items():
            if not _NAME.match(name or ""):
                raise _refuse("a variable name is letters, digits and _ and starts with a letter or _",
                              "bad_name", variable=str(name)[:64])
            if value is not None:
                if is_secret_name(name) or is_secret_variable(name, value):
                    raise _refuse(f"{name} looks secret: add it under the Space's secrets, where its value "
                                  "cannot be read back", "secret_as_variable", variable=name)
                if len(value) > _MAX_VALUE:
                    raise _refuse(f"{name} is too long", "value_too_long", variable=name)
            if value is None and name not in current:
                continue
            if value is not None and current.get(name) == value:
                continue
            clean[name] = value
        if not clean:
            raise _refuse("every value is already what you asked for", "no_changes")
        return clean

    async def validate(self, changes: Mapping[str, str | None]) -> dict[str, Any]:
        space_id = await self.space_id()
        clean = self._clean(changes, await self._api.variables(space_id))
        return {"ok": True, "variables": [{"name": n, "deleted": v is None} for n, v in sorted(clean.items())]}

    async def _guard(self, actor: str, confirm: str | None) -> str:
        space_id = await self.space_id()
        _confirm(self.short_name(space_id), confirm)
        await self._control.check_write_cap(actor)
        return space_id

    async def _not_busy(self, space_id: str) -> None:
        if await self._busy(space_id):
            raise _refuse("a change to the Space is still being watched; wait for it to finish",
                          "change_in_progress")

    async def apply_variables(self, changes: Mapping[str, str | None], *, actor: str, confirm: str | None) -> dict[str, Any]:
        space_id = await self._guard(actor, confirm)
        await self._not_busy(space_id)
        current = await self._api.variables(space_id)
        clean = self._clean(changes, current)
        restore = {name: current.get(name) for name in clean}
        await self._api.set_variables(space_id, clean)
        # No deployment id: the reconciler reads the Space's stage. No previous deployment either:
        # restoring the variables restarts the Space with the old values, which is the rollback.
        record = await self._control.watch(
            actor=actor, platform=Platform.HF_SPACE, target=space_id, action="space_variables",
            detail={
                "service": self.short_name(space_id), "watch_s": self._watch_s,
                "variables": [{"name": n, "secret": False, "deleted": v is None, "added": n not in current}
                              for n, v in sorted(clean.items())],
            },
            deployment_id=f"space:{space_id}", previous_deployment_id=None, restore_values=restore,
        )
        return change_json(record)

    # -- secrets (write-only, not undone) ------------------------------------------------------
    async def set_secrets(self, changes: Mapping[str, str | None], *, actor: str, confirm: str | None) -> dict[str, Any]:
        space_id = await self._guard(actor, confirm)
        await self._not_busy(space_id)
        if not changes:
            raise _refuse("there is nothing to change", "no_changes")
        if len(changes) > _MAX_CHANGES:
            raise _refuse(f"at most {_MAX_CHANGES} secrets per change", "too_many_changes")
        existing = {s.name for s in await self._api.secrets(space_id)}
        clean: dict[str, str | None] = {}
        for name, value in changes.items():
            if not _NAME.match(name or ""):
                raise _refuse("a secret name is letters, digits and _ and starts with a letter or _",
                              "bad_name", variable=str(name)[:64])
            if value is not None and (not value or len(value) > _MAX_VALUE):
                raise _refuse(f"give {name} a value (or delete it)", "value_required", variable=name)
            if value is None and name not in existing:
                continue
            clean[name] = value
        if not clean:
            raise _refuse("those secrets do not exist", "no_changes")
        await self._api.set_secrets(space_id, clean)
        record = await self._control.journal(
            actor=actor, platform=Platform.HF_SPACE, target=space_id, action="space_secrets",
            detail={
                "service": self.short_name(space_id), "restorable": False,
                "variables": [{"name": n, "secret": True, "deleted": v is None, "added": n not in existing}
                              for n, v in sorted(clean.items())],
            },
        )
        return change_json(record)

    # -- runtime -------------------------------------------------------------------------------
    async def pause(self, *, actor: str, confirm: str | None) -> dict[str, Any]:
        space_id = await self._guard(actor, confirm)
        await self._api.pause(space_id)
        return change_json(await self._control.journal(
            actor=actor, platform=Platform.HF_SPACE, target=space_id, action="space_pause",
            detail={"service": self.short_name(space_id)},
        ))

    async def restart(self, *, actor: str, confirm: str | None, factory: bool) -> dict[str, Any]:
        """Also how a paused Space is resumed. ``factory`` rebuilds the image from scratch."""
        space_id = await self._guard(actor, confirm)
        await self._not_busy(space_id)
        await self._api.restart(space_id, factory=factory)
        return change_json(await self._control.journal(
            actor=actor, platform=Platform.HF_SPACE, target=space_id,
            action="space_rebuild" if factory else "space_restart",
            detail={"service": self.short_name(space_id)},
        ))

    async def request_hardware(self, hardware: str, *, actor: str, confirm: str | None) -> dict[str, Any]:
        if hardware not in SPACE_HARDWARE:
            raise _refuse("unknown Space hardware", "bad_hardware", options=list(SPACE_HARDWARE))
        space_id = await self._guard(actor, confirm)
        await self._not_busy(space_id)
        status = await self._api.status(space_id)
        if (status.requested_hardware or status.hardware) == hardware:
            raise _refuse(f"the Space already runs on {hardware}", "no_changes")
        await self._api.request_hardware(space_id, hardware)
        return change_json(await self._control.journal(
            actor=actor, platform=Platform.HF_SPACE, target=space_id, action="space_hardware",
            detail={"service": self.short_name(space_id), "from": status.hardware, "to": hardware},
        ))

    async def set_sleep_time(self, seconds: int, *, actor: str, confirm: str | None) -> dict[str, Any]:
        if seconds not in SLEEP_CHOICES:
            raise _refuse("choose one of the offered sleep times", "bad_sleep_time", options=list(SLEEP_CHOICES))
        space_id = await self._guard(actor, confirm)
        status = await self._api.status(space_id)
        if status.sleep_time == seconds:
            raise _refuse("the Space already sleeps after that long", "no_changes")
        await self._api.set_sleep_time(space_id, seconds)
        return change_json(await self._control.journal(
            actor=actor, platform=Platform.HF_SPACE, target=space_id, action="space_sleep_time",
            detail={"service": self.short_name(space_id), "from": status.sleep_time, "to": seconds},
        ))

