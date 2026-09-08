"""Token exchange endpoints (Doc/V2_BLUEPRINT.md §9). The client
authenticates with its existing Google/Firebase ID token exactly once
(via services.security.verify_token, which also enforces device
attestation), then uses the returned access/refresh JWT pair for every v2
API call going forward instead of re-sending the Google/Firebase token.
"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from infrastructure.db.user_repo import get_or_create_user
from services.desktop_pairing import (
    exchange_pairing_code,
    issue_pairing_code,
    revoke_desktop_device_token,
)
from services.jwt_auth import (
    issue_token_pair,
    refresh_token_pair,
    revoke_refresh_token,
    verify_access_token,
)
from services.rbac import CONSOLE_MIN_ROLE, is_console_operator, stamp_resolved_role
from services.security import store_nonce, verify_identity_token, verify_token

logger = logging.getLogger("aura.auth")

router = APIRouter(prefix="/api/v3/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class NonceResponse(BaseModel):
    nonce: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RevokeRequest(BaseModel):
    refresh_token: str


@router.post("/nonce", response_model=NonceResponse)
async def issue_nonce():
    """Issues a single-use, short-lived nonce a client binds into its Play
    Integrity / App Attest attestation request and echoes back via the
    X-Device-Attestation-Nonce header on /token. Unauthenticated by design —
    it's the raw material an attestation call gets bound to, requested
    before the client has a session. Closes the replay hole where a
    client-only-generated nonce could never be correlated server-side."""
    nonce = secrets.token_urlsafe(24)
    store_nonce(nonce)
    return {"nonce": nonce}


async def _persist_user(user_info: dict) -> None:
    """Write the verified subject into ``users`` so Access can list operators.

    Token exchange used to issue JWTs only. SuperAdmin then existed solely via
    ``SUPERADMIN_EMAILS`` and never appeared in ``GET /admin/operators``.
    Skip when Postgres is unset so unit tests and local-without-DB still login.
    Persistence failures are logged and do not block token issue.
    """
    from infrastructure.db.base import get_session_factory, is_configured

    if not is_configured():
        return
    try:
        factory = get_session_factory()
        async with factory() as db:
            await get_or_create_user(db, user_info)
            await db.commit()
    except Exception:
        logger.exception("failed to persist authenticated user")


@router.post("/token", response_model=TokenResponse)
async def exchange_token(user_info: dict = Depends(verify_token)):
    """Exchanges a verified Google/Firebase ID token (+ device attestation,
    both checked by verify_token) for a backend-issued access/refresh pair."""
    stamp_resolved_role(user_info)
    await _persist_user(user_info)
    return issue_token_pair(user_info)


_web_bearer = HTTPBearer(auto_error=False)


@router.post("/web/token", response_model=TokenResponse)
async def exchange_web_token(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(_web_bearer),
):
    """Browser operator login. Google/Firebase ID token only — no Play Integrity.

    Fail-closed: only Developer+ receive a session. Consumers cannot use this
    path as a mobile-attestation bypass.
    """
    user_info = await verify_identity_token(request, authorization)
    stamp_resolved_role(user_info)
    if not is_console_operator(user_info["role"]):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Access forbidden: console login requires at least {CONSOLE_MIN_ROLE} "
                f"(resolved {user_info['role']})."
            ),
        )
    await _persist_user(user_info)
    return issue_token_pair(user_info)


@router.post("/desktop/token", response_model=TokenResponse)
async def exchange_desktop_token(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(_web_bearer),
):
    """Desktop Flutter login. Google/Firebase ID token only — no Play Integrity.

    ADR-011's deliberate attestation relaxation for the desktop platform: same identity
    verification as ``/web/token``, but available to any authenticated user (not console-role
    gated). Does **not** grant ``engineer_agent`` by itself -- that still comes from
    ``AURA_ENGINEER_AGENT_ROLES``. Pairing the host daemon is a separate one-time-code flow
    (``/desktop/pairing``).
    """
    user_info = await verify_identity_token(request, authorization)
    stamp_resolved_role(user_info)
    await _persist_user(user_info)
    return issue_token_pair(user_info)


class PairingCodeResponse(BaseModel):
    code: str
    expires_in: int


class PairingExchangeRequest(BaseModel):
    code: str
    device_name: str = "aura_hostd"


class PairingExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    device_token: str
    device_id: str
    expires_in_device: int


class DeviceRevokeRequest(BaseModel):
    device_token: str


@router.post("/desktop/pairing", response_model=PairingCodeResponse)
async def start_desktop_pairing(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(_web_bearer),
):
    """Issue a one-time pairing code for ``aura_hostd``. Requires a live access JWT."""
    user_info = await verify_access_token(request, authorization)
    stamp_resolved_role(user_info)
    return issue_pairing_code(user_info)


@router.post("/desktop/pairing/exchange", response_model=PairingExchangeResponse)
async def exchange_desktop_pairing(body: PairingExchangeRequest):
    """Daemon-facing: trade a pairing code for a device token + session JWTs.

    Unauthenticated by design -- possession of the short-lived code *is* the proof, mirroring
    how a one-time invite works. The code is single-use and expires quickly.
    """
    result = exchange_pairing_code(body.code, device_name=body.device_name)
    if result is None:
        raise HTTPException(status_code=400, detail="Pairing code is invalid or expired.")
    return result


@router.post("/desktop/pairing/revoke")
async def revoke_desktop_pairing(body: DeviceRevokeRequest):
    """Revoke a previously issued device-scoped pairing token."""
    if not revoke_desktop_device_token(body.device_token):
        raise HTTPException(status_code=404, detail="Device token not found.")
    return {"status": "revoked"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest):
    result = refresh_token_pair(request.refresh_token)
    if result is None:
        raise HTTPException(status_code=401, detail="Refresh token is invalid, expired, or already used.")
    return result


@router.post("/revoke")
async def revoke(request: RevokeRequest):
    revoke_refresh_token(request.refresh_token)
    return {"status": "revoked"}
