"""Revoking a user's paired desktop devices for the kill switch (``POST /api/v3/agents/stop-all``).

Lives here, not in ``aura_core``, because the device registry is ``services.jwt_auth`` and
``aura_core`` never imports ``services`` (ADR-002). ``aura_service`` attaches
:func:`revoke_paired_devices` to the container as ``container.device_revoker``.
"""

from __future__ import annotations

from typing import Any

from services.jwt_auth import list_devices_for_uid, revoke_device

__all__ = ["revoke_paired_devices"]


async def revoke_paired_devices(uid: str) -> list[dict[str, Any]]:
    """Revoke every paired ``aura_hostd`` daemon ``uid`` owns; what was revoked.

    Goes through :func:`services.jwt_auth.revoke_device`, which checks the device is the
    caller's own. The desktop *app's* own sign-in (``kind == "app"``) is left alone: it is the
    person's session, and signing them out of it is a different action
    (``/auth/desktop/devices/revoke``). A device of unknown kind (registered before metadata
    existed) is revoked, for the reason ``list_devices_for_uid`` gives: an unnamed entry the owner
    cannot place is the one a kill switch should not leave connected.
    """
    revoked: list[dict[str, Any]] = []
    for device in list_devices_for_uid(uid):
        if device.get("kind") == "app":
            continue
        if revoke_device(device["device_id"], requesting_uid=uid):
            revoked.append(
                {"device_id": device["device_id"], "name": device.get("name"), "kind": device.get("kind")}
            )
    return revoked
