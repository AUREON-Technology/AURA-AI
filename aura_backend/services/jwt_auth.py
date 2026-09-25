"""Backend-issued JWT access/refresh tokens (Doc/V2_BLUEPRINT.md §9).

V1 (and the v1 `/api/chat/agent` etc. routes, still untouched) has the
client re-verify its Google/Firebase ID token against Google's servers on
*every single request* via `services.security.verify_token` — that's a
network round-trip per API call, and there's no way to revoke a session
short of waiting out Google's own ~1hr token expiry.

The v2 flow: exchange a Google/Firebase ID token once for a short-lived
access JWT (signed locally, no network call to verify) + a long-lived
opaque refresh token (stored server-side, so it CAN be revoked). Device
attestation is still enforced at the exchange step (verify_token checks it
before this module ever runs) — it is deliberately not re-checked on every
subsequent request once a session exists; that's the whole point of having
a session.
"""
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings
from infrastructure.redis_client import get_redis_client

logger = logging.getLogger("aura_backend.jwt_auth")

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
ALGORITHM = "HS256"
_REFRESH_PREFIX = "refresh_token:"

# A desktop-paired device (services/device_pairing.py) has no refresh token at all -- ADR-011's
# "device-scoped, revocable, and narrower than a full session token" is met differently here than
# for a mobile/web session: one long-lived, self-contained access token instead of a short-lived
# access token renewed indefinitely by a long-lived refresh token. Long enough that a desktop
# daemon someone actually uses day to day doesn't need to be re-paired constantly (aura_hostd has
# no refresh-on-401 logic to begin with); "revocable" comes from the device-id check below, not
# from a renewable session the server can decline to renew.
DEVICE_ACCESS_TOKEN_TTL = timedelta(days=30)
_REVOKED_DEVICE_PREFIX = "revoked_device:"

# Dev-mode fallback: a random secret generated once per process if
# JWT_SECRET isn't configured. Every existing session is invalidated on
# restart in this mode — fine for local dev, not for production (see
# config.py's jwt_secret doc comment).
_dev_secret = secrets.token_urlsafe(32)
if not (settings.jwt_secret or os.environ.get("JWT_SECRET") or os.environ.get("AURA_JWT_SECRET")):
    logger.warning("JWT_SECRET is not configured — using a random per-process dev secret. Set JWT_SECRET for stable sessions across restarts.")


def _secret() -> str:
    # Read env at call time so a secret hydrated after Settings() was
    # constructed (AURA_JWT_SECRET from the Docker secret file) is still used
    # instead of the per-process random fallback.
    return (
        settings.jwt_secret
        or os.environ.get("JWT_SECRET")
        or os.environ.get("AURA_JWT_SECRET")
        or _dev_secret
    )


# In-process fallback store for refresh tokens if Redis is unreachable.
# Does not survive restart and isn't shared across the Dockerfile's 4
# uvicorn workers — logged clearly so this limitation is visible in prod.
_local_refresh_store: Dict[str, Dict[str, Any]] = {}
_warned_no_redis = False


def _refresh_store_set(token: str, user_info: dict, ttl: timedelta) -> None:
    global _warned_no_redis
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(f"{_REFRESH_PREFIX}{token}", int(ttl.total_seconds()), json.dumps(user_info))
        return
    if not _warned_no_redis:
        logger.warning("Redis unavailable: refresh tokens are stored in-process only (lost on restart, not shared across workers).")
        _warned_no_redis = True
    _local_refresh_store[token] = {"user_info": user_info, "expires_at": datetime.now(timezone.utc) + ttl}


_local_refresh_lock = threading.Lock()


def _refresh_store_take(token: str) -> Optional[dict]:
    """Atomically read *and* consume a refresh token -- the only safe primitive for rotation.

    A GET followed by a DEL is two round trips: concurrent refreshes of one token (the gateway
    runs several workers sharing Redis) could all read it before any deleted it, and each mint a
    session -- the stolen-token race rotation exists to close. GETDEL (Redis >= 6.2; production
    runs 7) is one command, so exactly one caller gets the value.
    """
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.getdel(f"{_REFRESH_PREFIX}{token}")
        return json.loads(raw) if raw else None
    with _local_refresh_lock:
        entry = _local_refresh_store.pop(token, None)
    if entry is None or entry["expires_at"] < datetime.now(timezone.utc):
        return None
    return entry["user_info"]


def _refresh_store_delete(token: str) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.delete(f"{_REFRESH_PREFIX}{token}")
    _local_refresh_store.pop(token, None)


def create_access_token(
    user_info: dict, *, ttl: timedelta = ACCESS_TOKEN_TTL, extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_info["uid"],
        "email": user_info.get("email"),
        "name": user_info.get("name"),
        "picture": user_info.get("picture"),
        "role": user_info.get("role"),
        "iat": now,
        "exp": now + ttl,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def issue_token_pair(user_info: dict) -> Dict[str, Any]:
    access_token = create_access_token(user_info)
    refresh_token = secrets.token_urlsafe(48)
    _refresh_store_set(refresh_token, user_info, REFRESH_TOKEN_TTL)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
    }


_DEVICE_OWNER_PREFIX = "device_owner:"
_local_device_owners: Dict[str, str] = {}


def _register_device_owner(device_id: str, uid: str) -> None:
    """So a revoke request can be checked against who actually owns the device (below) -- without
    this, any authenticated caller who learned or guessed a device_id could revoke someone else's
    paired desktop. TTL matches the token's own lifetime: once the token would have expired via
    its own `exp` claim anyway, there is nothing left for a revoke to protect against."""
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(f"{_DEVICE_OWNER_PREFIX}{device_id}", int(DEVICE_ACCESS_TOKEN_TTL.total_seconds()), uid)
        return
    _local_device_owners[device_id] = uid


def _device_owner(device_id: str) -> Optional[str]:
    redis_client = get_redis_client()
    if redis_client is not None:
        return redis_client.get(f"{_DEVICE_OWNER_PREFIX}{device_id}")
    return _local_device_owners.get(device_id)


def issue_device_token(user_info: dict, *, device_id: str) -> Dict[str, Any]:
    """A single long-lived access token for a paired desktop device -- no refresh token, see
    DEVICE_ACCESS_TOKEN_TTL's doc comment. Called only from services/device_pairing.py once a
    pairing has been approved by the user on an already-authenticated client."""
    access_token = create_access_token(
        user_info, ttl=DEVICE_ACCESS_TOKEN_TTL, extra_claims={"platform": "desktop", "device_id": device_id},
    )
    _register_device_owner(device_id, str(user_info["uid"]))
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": int(DEVICE_ACCESS_TOKEN_TTL.total_seconds()),
        "device_id": device_id,
    }


_local_revoked_devices: set = set()
_warned_no_redis_revocation = False


def revoke_device(device_id: str, *, requesting_uid: str) -> bool:
    """Marks a paired device's token as no longer valid, but only for the account that owns it --
    checked against the owner record `issue_device_token` wrote, not just "any authenticated
    caller." Returns False (nothing revoked) if the device is unknown or owned by someone else;
    the API layer turns that into the same 404 either way; an owner check that could tell an
    attacker "device exists, wrong owner" apart from "no such device" would leak device_ids to
    probe for.

    `verify_access_token` checks the revocation flag this sets for any token carrying a device_id
    claim -- see there for why a device token needs this at all when mobile/web sessions get by
    with a revocable refresh token instead (this one has none)."""
    global _warned_no_redis_revocation
    owner = _device_owner(device_id)
    if owner is None or owner != requesting_uid:
        return False
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(
            f"{_REVOKED_DEVICE_PREFIX}{device_id}", int(DEVICE_ACCESS_TOKEN_TTL.total_seconds()), "1"
        )
        return True
    if not _warned_no_redis_revocation:
        logger.warning("Redis unavailable: device revocations are stored in-process only (lost on restart, not shared across workers).")
        _warned_no_redis_revocation = True
    _local_revoked_devices.add(device_id)
    return True


def is_device_revoked(device_id: str) -> bool:
    redis_client = get_redis_client()
    if redis_client is not None:
        return redis_client.exists(f"{_REVOKED_DEVICE_PREFIX}{device_id}") > 0
    return device_id in _local_revoked_devices


def refresh_token_pair(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Rotates the refresh token on every use (the old one stops working
    immediately) so a stolen-but-unused refresh token has a shrinking
    window of validity rather than living for the full 30 days."""
    user_info = _refresh_store_take(refresh_token)
    if user_info is None:
        return None
    from services.rbac import stamp_resolved_role

    return issue_token_pair(stamp_resolved_role(dict(user_info)))


def revoke_refresh_token(refresh_token: str) -> None:
    _refresh_store_delete(refresh_token)


def revoke_all_for_uid(uid: str) -> int:
    """Drop every refresh token belonging to ``uid``. Returns how many were removed."""
    revoked = 0
    redis_client = get_redis_client()
    if redis_client is not None:
        for key in redis_client.scan_iter(f"{_REFRESH_PREFIX}*"):
            raw = redis_client.get(key)
            if not raw:
                continue
            try:
                stored = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if stored.get("uid") == uid:
                redis_client.delete(key)
                revoked += 1
    stale = [
        token
        for token, entry in _local_refresh_store.items()
        if entry.get("user_info", {}).get("uid") == uid
    ]
    for token in stale:
        _local_refresh_store.pop(token, None)
        revoked += 1
    return revoked


_bearer_scheme = HTTPBearer(auto_error=False)


async def verify_access_token(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> Dict[str, Any]:
    """FastAPI dependency for v2 endpoints — verifies a locally-issued
    access JWT (no network call), returning the same {uid, email, name,
    picture, role} shape services.security.verify_token returns, so
    services/rbac.py's require_role works unchanged against either."""
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header is required.")

    token = authorization.credentials
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token expired. Use /api/v3/auth/refresh.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token.")

    device_id = payload.get("device_id")
    if device_id and is_device_revoked(device_id):
        # A device token has no refresh token to decline to renew (issue_device_token's doc
        # comment) -- this check is the entire revocation mechanism for it, so it must run on
        # every call, not just at issue time.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device access has been revoked.")

    return {
        "uid": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "picture": payload.get("picture"),
        "role": payload.get("role"),
        "device_id": device_id,
    }
