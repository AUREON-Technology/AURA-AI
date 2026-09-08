"""Desktop pairing tokens (ADR-011 Unit 6).

Desktop has no Play Integrity / App Attest equivalent worth relying on. Instead:

1. An already-authenticated session (Flutter after ``/api/v3/auth/desktop/token``) requests a
   short-lived one-time pairing code.
2. ``aura_hostd`` exchanges that code for a device-scoped opaque token plus a normal
   access/refresh JWT pair (so the host-agent WebSocket can authenticate through the same
   ``verify_access_token`` path every other v3 caller uses).
3. The opaque device token is what ``verify_device_integrity`` accepts when
   ``X-Device-Platform: desktop`` -- narrower than a full session, revocable, and never a
   bypass of ``ToolPolicy`` / daemon containment.

Storage mirrors ``jwt_auth``'s refresh-token pattern: Redis when available, in-process fallback
otherwise (fine for single-node / tests; not shared across workers without Redis).
"""

from __future__ import annotations

import json
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from infrastructure.redis_client import get_redis_client

logger = logging.getLogger("aura_backend.desktop_pairing")

_CODE_PREFIX = "desktop_pair_code:"
_DEVICE_PREFIX = "desktop_device:"
_CODE_TTL = timedelta(minutes=10)
_DEVICE_TTL = timedelta(days=90)
_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 8

_local_codes: dict[str, dict[str, Any]] = {}
_local_devices: dict[str, dict[str, Any]] = {}
_warned_no_redis = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _warn_local() -> None:
    global _warned_no_redis
    if not _warned_no_redis:
        logger.warning(
            "Redis unavailable: desktop pairing codes/device tokens are in-process only "
            "(lost on restart, not shared across workers)."
        )
        _warned_no_redis = True


def _setex(key: str, ttl: timedelta, payload: dict[str, Any]) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(key, int(ttl.total_seconds()), json.dumps(payload))
        return
    _warn_local()
    store = _local_codes if key.startswith(_CODE_PREFIX) else _local_devices
    store[key] = {"payload": payload, "expires_at": _now() + ttl}


def _get(key: str) -> Optional[dict[str, Any]]:
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.get(key)
        return json.loads(raw) if raw else None
    store = _local_codes if key.startswith(_CODE_PREFIX) else _local_devices
    entry = store.get(key)
    if entry is None:
        return None
    if entry["expires_at"] < _now():
        store.pop(key, None)
        return None
    return entry["payload"]


def _delete(key: str) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.delete(key)
        return
    _local_codes.pop(key, None)
    _local_devices.pop(key, None)


def _fresh_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def issue_pairing_code(user_info: dict[str, Any]) -> dict[str, Any]:
    """Mint a one-time code bound to ``user_info``. Caller must already be authenticated."""
    uid = user_info.get("uid")
    if not uid:
        raise ValueError("user_info must include uid")
    for _ in range(8):
        code = _fresh_code()
        key = f"{_CODE_PREFIX}{code}"
        if _get(key) is None:
            _setex(
                key,
                _CODE_TTL,
                {
                    "uid": uid,
                    "email": user_info.get("email"),
                    "name": user_info.get("name"),
                    "picture": user_info.get("picture"),
                    "role": user_info.get("role"),
                },
            )
            return {
                "code": code,
                "expires_in": int(_CODE_TTL.total_seconds()),
            }
    raise RuntimeError("could not allocate a unique pairing code")


def exchange_pairing_code(code: str, *, device_name: str = "") -> Optional[dict[str, Any]]:
    """Consume a pairing code and return device + session material, or None if invalid."""
    normalized = (code or "").strip().upper()
    if not normalized:
        return None
    key = f"{_CODE_PREFIX}{normalized}"
    user_info = _get(key)
    if user_info is None:
        return None
    _delete(key)

    device_token = secrets.token_urlsafe(32)
    device_id = secrets.token_urlsafe(12)
    _setex(
        f"{_DEVICE_PREFIX}{device_token}",
        _DEVICE_TTL,
        {
            **user_info,
            "device_id": device_id,
            "device_name": (device_name or "aura_hostd")[:120],
        },
    )

    from services.jwt_auth import issue_token_pair
    from services.rbac import stamp_resolved_role

    stamped = stamp_resolved_role(dict(user_info))
    pair = issue_token_pair(stamped)
    return {
        **pair,
        "device_token": device_token,
        "device_id": device_id,
        "expires_in_device": int(_DEVICE_TTL.total_seconds()),
    }


def verify_desktop_device_token(token: Optional[str]) -> bool:
    """True when ``token`` is a live device-scoped pairing token."""
    if not token:
        return False
    return _get(f"{_DEVICE_PREFIX}{token}") is not None


def lookup_desktop_device(token: str) -> Optional[dict[str, Any]]:
    return _get(f"{_DEVICE_PREFIX}{token}")


def revoke_desktop_device_token(token: str) -> bool:
    key = f"{_DEVICE_PREFIX}{token}"
    if _get(key) is None:
        return False
    _delete(key)
    return True


def clear_local_pairing_state_for_tests() -> None:
    """Test helper -- empties the in-process stores."""
    _local_codes.clear()
    _local_devices.clear()
