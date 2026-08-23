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


def _refresh_store_get(token: str) -> Optional[dict]:
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.get(f"{_REFRESH_PREFIX}{token}")
        return json.loads(raw) if raw else None
    entry = _local_refresh_store.get(token)
    if entry is None:
        return None
    if entry["expires_at"] < datetime.now(timezone.utc):
        del _local_refresh_store[token]
        return None
    return entry["user_info"]


def _refresh_store_delete(token: str) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.delete(f"{_REFRESH_PREFIX}{token}")
    _local_refresh_store.pop(token, None)


def create_access_token(user_info: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_info["uid"],
        "email": user_info.get("email"),
        "name": user_info.get("name"),
        "picture": user_info.get("picture"),
        "role": user_info.get("role"),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
    }
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


def refresh_token_pair(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Rotates the refresh token on every use (the old one stops working
    immediately) so a stolen-but-unused refresh token has a shrinking
    window of validity rather than living for the full 30 days."""
    user_info = _refresh_store_get(refresh_token)
    if user_info is None:
        return None
    _refresh_store_delete(refresh_token)
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

    return {
        "uid": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "picture": payload.get("picture"),
        "role": payload.get("role"),
    }
