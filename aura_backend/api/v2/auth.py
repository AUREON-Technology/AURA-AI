"""Token exchange endpoints (Doc/V2_BLUEPRINT.md §9). The client
authenticates with its existing Google/Firebase ID token exactly once
(via services.security.verify_token, which also enforces device
attestation), then uses the returned access/refresh JWT pair for every v2
API call going forward instead of re-sending the Google/Firebase token.
"""
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from infrastructure.db.user_repo import get_or_create_user
from services import device_pairing
from services.jwt_auth import (
    HOST_AGENT_SCOPE,
    issue_desktop_session,
    issue_device_token,
    issue_token_pair,
    consume_refresh_token,
    list_devices_for_uid,
    reissue_for_refreshed_identity,
    revoke_device,
    revoke_refresh_token,
    verify_access_token,
)
from services.rbac import (
    CONSOLE_MIN_ROLE,
    CONSUMER_WEB_CHANNEL,
    console_operators_configured,
    is_console_operator,
    stamp_role,
)
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


class PairingStartRequest(BaseModel):
    #: The daemon's hostname, shown to whoever approves the code (FSA-004). Optional so an
    #: older daemon that posts an empty body still pairs.
    device_name: str | None = None


class PairingStartResponse(BaseModel):
    pairing_id: str
    user_code: str
    expires_in: int
    interval: int


class PairingApproveRequest(BaseModel):
    user_code: str


class PairingApproveResponse(BaseModel):
    status: str
    device_id: str
    device_name: str | None = None


class PairingPollRequest(BaseModel):
    pairing_id: str


class PairingPollResponse(BaseModel):
    status: str
    access_token: str | None = None
    token_type: str | None = None
    expires_in: int | None = None
    device_id: str | None = None


class DeviceRevokeRequest(BaseModel):
    device_id: str


class DesktopLoginRequest(BaseModel):
    #: The machine's hostname, so the owner can tell this sign-in apart in the device list.
    device_name: str | None = None


class DesktopLoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    device_id: str


class DesktopDevice(BaseModel):
    device_id: str
    name: str | None = None
    kind: str
    created_at: str | None = None
    current: bool = False


class DesktopDevicesResponse(BaseModel):
    devices: list[DesktopDevice]


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
    await stamp_role(user_info)
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
    await stamp_role(user_info)
    _require_operator(user_info, what="console login", where="the console")
    await _persist_user(user_info)
    return issue_token_pair(user_info)


#: Customer web sign-ins per client IP per minute. In-process and per worker, so the bound is
#: this many per worker; it still caps how fast one address can mint sessions.
CONSUMER_SIGN_INS_PER_MINUTE = 10
_consumer_hits: dict[str, list[float]] = {}


def _reset_consumer_rate_limit() -> None:
    _consumer_hits.clear()


def _consumer_rate_limit(request: Request) -> None:
    now = time.monotonic()
    ip = request.client.host if request.client else "unknown"
    recent = [t for t in _consumer_hits.get(ip, []) if now - t < 60.0]
    if len(recent) >= CONSUMER_SIGN_INS_PER_MINUTE:
        _consumer_hits[ip] = recent
        raise HTTPException(status_code=429, detail="Too many sign-in attempts; try again in a minute.")
    recent.append(now)
    _consumer_hits[ip] = recent


@router.post("/consumer/token", response_model=TokenResponse)
async def exchange_consumer_web_token(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(_web_bearer),
):
    """Customer browser sign-in (SaaS P1-B): a verified Google or Firebase ID token, no device
    attestation (a browser has none).

    The session is a customer's whatever the identity: ``channel=consumer_web`` caps its platform
    role at ``User`` here, on every refresh and in the gateway's per-request resolution
    (``services.rbac.get_user_role``). Operators keep using ``/web/token``.
    """
    _consumer_rate_limit(request)
    user_info = await verify_identity_token(request, authorization)
    user_info["channel"] = CONSUMER_WEB_CHANNEL
    await stamp_role(user_info)
    await _persist_user(user_info)
    return issue_token_pair(user_info)


def _require_operator(user_info: dict, *, what: str, where: str) -> None:
    """403 unless ``user_info`` holds at least ``CONSOLE_MIN_ROLE``: the gate on the two sign-in
    routes that skip device attestation (``/web/token`` and ``/desktop/login``)."""
    if is_console_operator(user_info["role"]):
        return
    # Two different failures wear the same 403, and telling them apart is the difference
    # between a five-minute fix and an afternoon. "Your role is too low" is true in both
    # cases but only actionable in the second.
    if not console_operators_configured():
        raise HTTPException(
            status_code=403,
            detail=(
                f"No console operator is configured on this deployment, so nobody can sign "
                f"in to {where} yet. Set SUPERADMIN_EMAILS (or ADMIN_EMAILS / "
                "DEVELOPER_EMAILS) to a comma-separated list of operator email addresses "
                "and restart. AURA_-prefixed spellings are accepted too."
            ),
        )
    raise HTTPException(
        status_code=403,
        detail=(
            f"Access forbidden: {what} requires at least {CONSOLE_MIN_ROLE} "
            f"(resolved {user_info['role']})."
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest):
    user_info = consume_refresh_token(request.refresh_token)
    if user_info is None:
        raise HTTPException(status_code=401, detail="Refresh token is invalid, expired, or already used.")
    # A desktop app's refresh token is bound to its device (FSA-004): the re-issued pair keeps
    # the device claims, and a revoked device gets nothing even with a valid refresh token.
    await stamp_role(user_info)
    if user_info.get("device_id") and not is_console_operator(user_info["role"]):
        # F-09: desktop sign-in is for operators only, and a session issued before that rule,
        # or to someone since demoted, must not outlive it. The refresh token was spent above,
        # so the session ends here; phone and web sessions carry no device_id.
        raise HTTPException(
            status_code=401,
            detail=f"Desktop sign-in now requires at least {CONSOLE_MIN_ROLE}; please sign in again.",
        )
    issued = reissue_for_refreshed_identity(user_info)
    if issued is None:
        raise HTTPException(status_code=401, detail="Device access has been revoked.")
    return issued


@router.post("/revoke")
async def revoke(request: RevokeRequest):
    revoke_refresh_token(request.refresh_token)
    return {"status": "revoked"}


# --- Desktop device pairing (ADR-011, docs/architecture/011-desktop-engineer-agent.md) ---
#
# services/device_pairing.py has the full design; the three-endpoint shape mirrors OAuth's
# device authorization grant (RFC 8628): a daemon starts a session and polls it (both
# unauthenticated -- it has no identity yet), a human approves it from a client that already has
# one (an ordinary access token, exactly what /token or /web/token already issue).


@router.post("/desktop/pair/start", response_model=PairingStartResponse)
async def start_desktop_pairing(body: PairingStartRequest | None = None):
    return device_pairing.start_pairing(device_name=body.device_name if body else None)


@router.post("/desktop/pair/approve", response_model=PairingApproveResponse)
async def approve_desktop_pairing(
    body: PairingApproveRequest, user_info: dict = Depends(verify_access_token),
):
    """Requires the approving client's own valid access token -- this is what stands in for
    attestation here: proof of an already-authenticated session, not proof of the new device's
    integrity (ADR-011 explains why the latter has no desktop equivalent worth relying on)."""
    device_name = device_pairing.pending_device_name(body.user_code)
    device_id = device_pairing.approve_pairing(body.user_code, user_info)
    if device_id is None:
        raise HTTPException(status_code=400, detail="Pairing code is invalid, expired, or already used.")
    return {"status": "approved", "device_id": device_id, "device_name": device_name}


@router.post("/desktop/pair/poll", response_model=PairingPollResponse)
async def poll_desktop_pairing(body: PairingPollRequest):
    outcome = device_pairing.poll_pairing(body.pairing_id)
    if outcome["status"] != "approved":
        return outcome
    user_info = dict(outcome["user_info"])
    await stamp_role(user_info)
    await _persist_user(user_info)
    # The daemon needs only its socket; a pairing code must not be worth the whole account.
    token = issue_device_token(
        user_info, device_id=outcome["device_id"], scope=HOST_AGENT_SCOPE,
        device_name=outcome.get("device_name"),
    )
    return {"status": "approved", **token}


@router.get("/desktop/devices", response_model=DesktopDevicesResponse)
async def list_desktop_devices(user_info: dict = Depends(verify_access_token)):
    """The caller's own desktop devices -- app sign-ins and paired daemons -- for the Settings
    device list (FSA-004). Only the authenticated subject's; nothing in the request names a
    user. ``current`` marks the device the calling token itself belongs to."""
    current = user_info.get("device_id")
    devices = [
        {**d, "current": bool(current) and d["device_id"] == current}
        for d in list_devices_for_uid(str(user_info["uid"]))
    ]
    return {"devices": devices}


@router.post("/desktop/devices/revoke")
async def revoke_desktop_device(
    body: DeviceRevokeRequest, user_info: dict = Depends(verify_access_token),
):
    ok = revoke_device(body.device_id, requesting_uid=str(user_info["uid"]))
    if not ok:
        raise HTTPException(status_code=404, detail="No such device paired to this account.")
    return {"status": "revoked"}


@router.post("/desktop/login", response_model=DesktopLoginResponse)
async def desktop_login(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(_web_bearer),
    body: DesktopLoginRequest | None = None,
):
    """The desktop *app's own* login (Unit 7) -- distinct from the pairing flow above, which is
    for aura_hostd, a headless daemon with no browser of its own to run an OAuth flow in.
    aura_client on desktop can open one (lib/core/auth/desktop_oauth_client.dart, a real
    loopback-redirect OAuth 2.0 + PKCE exchange against Google), so it has a real Google identity
    proof by the time it calls this -- verify_identity_token accepts it (see
    services/security.py's dual-audience check, since a Desktop-type OAuth client is required for
    that redirect and can't be the same client id the mobile/web login path verifies against).

    What it does not have is device attestation -- no desktop equivalent exists (ADR-011). So,
    like ``/web/token``, it is for operators only (audit F-09, owner decision 2026-11-16): any
    Google account could otherwise mint here the session a phone only gets after Play Integrity
    or App Attest, and no route tells a desktop session apart. Opening desktop to every user
    should come with a design that ties the device to an attested one, not by lowering this
    gate. The session it gets is also *device-bound*: a short access token carrying a device_id, and a
    refresh token that re-issues only for that device and dies with its revocation
    (issue_desktop_session). Until audit FSA-004 this minted the daemon's 30-day unscoped
    token instead -- a month-long full session that could fetch /keys/data; revocation, which
    only a device_id check provided, was reachable from no client screen.

    Called as a plain function (not via Depends), matching exchange_web_token just above --
    verify_identity_token has no attestation requirement of its own to enforce as a dependency,
    and this keeps both non-attested identity endpoints in the same shape."""
    user_info = await verify_identity_token(request, authorization)
    await stamp_role(user_info)
    _require_operator(user_info, what="desktop sign-in", where="the desktop app")
    await _persist_user(user_info)
    device_id = secrets.token_urlsafe(16)
    return issue_desktop_session(
        user_info, device_id=device_id, device_name=body.device_name if body else None,
    )
