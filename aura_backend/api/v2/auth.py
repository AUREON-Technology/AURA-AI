"""Token exchange endpoints (Doc/V2_BLUEPRINT.md §9). The client
authenticates with its existing Google/Firebase ID token exactly once
(via services.security.verify_token, which also enforces device
attestation), then uses the returned access/refresh JWT pair for every v2
API call going forward instead of re-sending the Google/Firebase token.
"""
import logging
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from infrastructure.db.user_repo import RoleLookupUnavailable, get_or_create_user, role_lookup_must_succeed
from services import desktop_handoff as desktop_handoff_store
from services import device_pairing
from services.google_oauth import GoogleExchangeError, exchange_desktop_code, is_loopback_redirect
from services.jwt_auth import (
    ACCOUNT_DELETED_DETAIL,
    AUTH_STATE_UNAVAILABLE_DETAIL,
    HOST_AGENT_SCOPE,
    RETRY_KEY_MAX_CHARS,
    AuthStateUnavailable,
    account_erased_and_not_reinstated,
    issue_desktop_session,
    issue_device_token,
    issue_token_pair,
    consume_refresh_token,
    list_devices_for_uid,
    mark_account_reinstated,
    refresh_token_of_erased_account,
    reissue_for_refreshed_identity,
    restore_refresh_token,
    revoke_device,
    revoke_refresh_token,
    verify_access_token,
)
from services.rbac import (
    CONSOLE_MIN_ROLE,
    CONSUMER_DESKTOP_CHANNEL,
    CONSUMER_WEB_CHANNEL,
    console_operators_configured,
    get_user_role,
    is_console_operator,
    stamp_role,
)
from services.security import store_nonce, verify_identity_token, verify_token
from services.user_language import LANGUAGE_HEADER, remember_language

logger = logging.getLogger("aura.auth")

router = APIRouter(prefix="/api/v3/auth", tags=["auth"])

#: Sent by the client on ``/token`` when a person has just signed in (``interactive``), as opposed
#: to a silent re-exchange after a refresh failed. Only a deliberate sign-in may start a fresh
#: account under a subject that was erased (INT-010). Not a security boundary -- anyone with the
#: Google identity and an attestation could sign in anyway -- but it stops a device that is merely
#: still signed in from recreating an account its owner deleted elsewhere.
SIGN_IN_HEADER = "X-Aura-Sign-In"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class NonceResponse(BaseModel):
    nonce: str


class RefreshRequest(BaseModel):
    refresh_token: str
    #: The client's random name for this refresh *attempt*, re-sent unchanged when it retries
    #: after losing the response (INT-012; ``services/jwt_auth.REFRESH_RETRY_GRACE``). Optional:
    #: a client that sends none gets the strict single-use behaviour.
    retry_key: str | None = Field(default=None, max_length=RETRY_KEY_MAX_CHARS)


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


class DesktopExchangeRequest(BaseModel):
    #: The one-time authorization code Google redirected the browser to the app's loopback
    #: listener with. Bounds only: Google decides whether it is real.
    code: str = Field(min_length=1, max_length=2048)
    #: The PKCE verifier (RFC 7636 section 4.1: 43-128 unreserved characters) whose S256
    #: challenge the app put in the authorization URL. Google checks them against each other.
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9\-._~]+$")
    #: The loopback redirect the app used, ``http://127.0.0.1:<port>/``; must repeat the
    #: authorization request's value exactly or Google refuses the code.
    redirect_uri: str = Field(min_length=1, max_length=64)


class DesktopExchangeResponse(BaseModel):
    #: Google's ID token, unverified here -- ``/desktop/login`` verifies it. Nothing else from
    #: Google's response (access or refresh tokens) is passed on.
    id_token: str


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


#: Attestation nonces per client address per minute (F39). Each one is a write to the nonce store
#: from an unauthenticated caller; a phone needs one per sign-in attempt, so this is generous --
#: several devices behind one carrier NAT must not starve each other.
NONCES_PER_MINUTE = 30
_nonce_hits: dict = {}


@router.post("/nonce", response_model=NonceResponse)
async def issue_nonce(request: Request):
    """Issues a single-use, short-lived nonce a client binds into its Play
    Integrity / App Attest attestation request and echoes back via the
    X-Device-Attestation-Nonce header on /token. Unauthenticated by design —
    it's the raw material an attestation call gets bound to, requested
    before the client has a session. Closes the replay hole where a
    client-only-generated nonce could never be correlated server-side.
    Throttled per client address (F39), since anyone can call it."""
    _sliding_limit(_nonce_hits, _consumer_client_key(request), NONCES_PER_MINUTE, 60.0,
                   "Too many sign-in attempts; try again in a minute.")
    nonce = secrets.token_urlsafe(24)
    store_nonce(nonce)
    return {"nonce": nonce}


async def _persist_user(user_info: dict, *, client_language: str | None = None) -> None:
    """Write the verified subject into ``users`` so Access can list operators.

    Token exchange used to issue JWTs only. SuperAdmin then existed solely via
    ``SUPERADMIN_EMAILS`` and never appeared in ``GET /admin/operators``.
    Skip when Postgres is unset so unit tests and local-without-DB still login.
    Persistence failures are logged and do not block token issue.

    Also remembers the app's display language (``X-Client-Language``) when sent, so the daily
    briefing can be written in it (``services/user_language.py``); an absent or unknown value
    leaves the stored one alone.
    """
    from infrastructure.db.base import get_session_factory, is_configured

    if not is_configured():
        return
    try:
        factory = get_session_factory()
        async with factory() as db:
            user = await get_or_create_user(db, user_info)
            remember_language(user, client_language)
            await db.commit()
    except Exception:
        logger.exception("failed to persist authenticated user")


@router.post("/token", response_model=TokenResponse)
async def exchange_token(
    request: Request,
    user_info: dict = Depends(verify_token),
    client_language: str | None = Header(default=None, alias=LANGUAGE_HEADER),
):
    """Exchanges a verified Google/Firebase ID token (+ device attestation,
    both checked by verify_token) for a backend-issued access/refresh pair.

    For a subject erased within the last ``DEVICE_ACCESS_TOKEN_TTL`` only a deliberate sign-in
    (``SIGN_IN_HEADER: interactive``) goes ahead, starting a fresh account; a silent re-exchange
    gets 401 ``account_deleted`` and writes nothing (INT-010)."""
    _admit_erased_subject(request, user_info, deliberate=False)
    await stamp_role(user_info)
    await _persist_user(user_info, client_language=client_language)
    return issue_token_pair(user_info)


def _auth_state_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail=dict(AUTH_STATE_UNAVAILABLE_DETAIL))


def _admit_erased_subject(request: Request, user_info: dict, *, deliberate: bool) -> None:
    """The erased-account guard every sign-in exchange runs before it issues anything (INT-010,
    F37). For a subject erased within ``DEVICE_ACCESS_TOKEN_TTL`` and not signed in to since:

    * a deliberate sign-in goes ahead and records the reinstatement, starting a fresh account --
      ``SIGN_IN_HEADER: interactive`` on the routes a device also calls silently (``/token``,
      ``/consumer/token``), or ``deliberate`` on the routes only a person completing a sign-in
      reaches (``/web/token``, ``/desktop/login``, ``/desktop/firebase-exchange``: their sessions
      renew through ``/refresh``, which answers ``account_deleted`` itself);
    * anything else gets 401 ``account_deleted`` and writes nothing.

    Fails closed: when the erasure state cannot be read or the reinstatement cannot be recorded,
    a retryable 503 and no token."""
    uid = str(user_info.get("uid") or "")
    if not uid:
        return
    try:
        if not account_erased_and_not_reinstated(uid):
            return
        interactive = (request.headers.get(SIGN_IN_HEADER) or "").strip().lower() == "interactive"
        if not (deliberate or interactive):
            raise HTTPException(status_code=401, detail=dict(ACCOUNT_DELETED_DETAIL))
        mark_account_reinstated(uid)
    except AuthStateUnavailable:
        logger.warning("erased-account state unavailable; refusing to issue a session")
        raise _auth_state_unavailable() from None


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
    _admit_erased_subject(request, user_info, deliberate=True)  # only a sign-in button calls this
    await _persist_user(user_info)
    return issue_token_pair(user_info)


#: Customer web sign-ins per client IP per minute. In-process and per worker, so the bound is
#: this many per worker; it still caps how fast one address can mint sessions.
CONSUMER_SIGN_INS_PER_MINUTE = 10
#: Addresses tracked at once. Past this, entries with no hit in the last minute are dropped, and if
#: none are stale the oldest go, so a flood of distinct addresses cannot grow worker memory.
CONSUMER_RATE_TABLE_MAX = 10_000
_consumer_hits: dict[str, list[float]] = {}


def _reset_consumer_rate_limit() -> None:
    _consumer_hits.clear()
    _nonce_hits.clear()


def _rate_key(address: str) -> str:
    """One bucket per IPv4 address and per IPv6 /64: a single customer line usually holds a whole
    /64, and rotating within it must not mint fresh buckets (Plan E review #4)."""
    import ipaddress

    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _consumer_client_key(request) -> str:
    """The caller's address as the trusted proxy saw it -- counted from the right of
    X-Forwarded-For, never ``request.client.host`` alone, which on Railway is the leftmost,
    caller-written entry (see the Dockerfile's note and ``v3.client_ip``)."""
    from aura_core.config import load_settings
    from aura_core.security.rate_limit import extract_client_ip

    return _rate_key(str(extract_client_ip(
        request.headers,
        peer=request.client.host if request.client else None,
        trusted_proxies=load_settings(validate=False).security.trusted_proxy_count,
    )))


def _prune_hits(table: dict, now: float, window_s: float) -> None:
    if len(table) < CONSUMER_RATE_TABLE_MAX:
        return
    for key in [key for key, hits in table.items() if not hits or now - hits[-1] >= window_s]:
        del table[key]
    while len(table) >= CONSUMER_RATE_TABLE_MAX:
        del table[next(iter(table))]  # oldest inserted first


def _sliding_limit(table: dict, key: str, limit: int, window_s: float, detail: str) -> None:
    """In-process sliding window: at most ``limit`` hits per ``key`` in ``window_s``. Per worker,
    which bounds a burst rather than promising an exact global count."""
    now = time.monotonic()
    recent = [t for t in table.pop(key, []) if now - t < window_s]
    if len(recent) >= limit:
        table[key] = recent
        raise HTTPException(status_code=429, detail=detail)
    _prune_hits(table, now, window_s)
    recent.append(now)
    table[key] = recent


def _check_limit(table: dict, key: str, limit: int, window_s: float, detail: str) -> None:
    now = time.monotonic()
    if len([t for t in table.get(key, []) if now - t < window_s]) >= limit:
        raise HTTPException(status_code=429, detail=detail)


def _record_hit(table: dict, key: str, window_s: float) -> None:
    now = time.monotonic()
    recent = [t for t in table.pop(key, []) if now - t < window_s]
    _prune_hits(table, now, window_s)
    recent.append(now)
    table[key] = recent


def _consumer_rate_limit(request: Request) -> None:
    _sliding_limit(_consumer_hits, _consumer_client_key(request), CONSUMER_SIGN_INS_PER_MINUTE, 60.0,
                   "Too many sign-in attempts; try again in a minute.")


# --- Desktop Engineer for customers (SaaS P1-E, ADR-011 "Customers") ---

#: Desktop sign-ins and pairing starts per client address per minute: both are unauthenticated
#: or unattested entry points, now open beyond operators.
DESKTOP_SIGN_INS_PER_MINUTE = 10
#: Pairing approvals per person per hour, and live desktop devices per person.
PAIR_APPROVALS_PER_HOUR = 5
MAX_DESKTOP_DEVICES_PER_USER = 10
_desktop_hits: dict = {}
_pair_start_hits: dict = {}
_pair_approval_hits: dict = {}
_desktop_exchange_hits: dict = {}


def _reset_desktop_rate_limits() -> None:
    _handoff_hits.clear()
    _handoff_exchange_hits.clear()
    _desktop_hits.clear()
    _pair_start_hits.clear()
    _pair_approval_hits.clear()
    _desktop_exchange_hits.clear()


def _reset_auth_rate_limits() -> None:
    """Every in-process sign-in bucket this module keeps (tests: one TestClient address)."""
    _reset_consumer_rate_limit()
    _reset_desktop_rate_limits()


def _customers_open(request: Request) -> bool:
    container = getattr(request.app.state, "aura", None)
    return bool(container is not None and container.settings.security.engineer_for_customers)


async def _engineer_entitled(request: Request, uid: str) -> bool:
    """Whether ``uid`` may use the desktop Engineer as a customer: the switch is on and one of
    their workspaces' plans includes the Engineer. No container, no entitlement."""
    if not _customers_open(request):
        return False
    plans = getattr(request.app.state.aura, "plans", None)
    if plans is None:
        return False
    try:
        return await plans.engineer_entitled(uid)
    except Exception as exc:
        # Not "not entitled": a database blip must not sign a paying customer out or tell them
        # to upgrade (Plan E review #1). The caller answers 503 and keeps what it can.
        logger.warning("could not read a desktop customer's plan", exc_info=True)
        raise _EntitlementUnavailable() from exc


class _EntitlementUnavailable(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=503, detail="Could not check your plan just now; please try again.")


_UPGRADE_DETAIL = "The desktop Engineer needs a Pro or Team plan. Upgrade, then sign in again."
#: Marks a refusal whose fix is a Pro or Team plan, so a client can say so in the person's own
#: language instead of matching the English detail (module readiness WP11: "refused with a plain
#: reason"). Additive: the detail string stays as it was.
REFUSAL_HEADER = "X-Aura-Refusal"
_PLAN_REQUIRED = {REFUSAL_HEADER: "plan_required"}


async def _desktop_gate(request: Request, user_info: dict, *, what: str, where: str) -> None:
    """An operator passes as before (F-09). A customer passes when entitled, and from then on
    carries ``channel=consumer_desktop``: an unattested session is never worth more than ``User``
    (``rbac.get_user_role``), so it can never reach the console. Anyone else: 403."""
    if is_console_operator(user_info["role"]):
        return
    if await _engineer_entitled(request, str(user_info["uid"])):
        user_info["channel"] = CONSUMER_DESKTOP_CHANNEL
        await stamp_role(user_info)
        return
    if _customers_open(request):
        raise HTTPException(status_code=403, detail=_UPGRADE_DETAIL, headers=_PLAN_REQUIRED)
    _require_operator(user_info, what=what, where=where)


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
    # The web app re-exchanges a cached Firebase identity silently, exactly as a phone does on
    # /token, so the same rule: only an interactive sign-in revives an erased account (F37).
    _admit_erased_subject(request, user_info, deliberate=False)
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


def _put_back(refresh_token: str, user_info: dict) -> None:
    """Restore a refresh token this request spent but could not answer for a reason that is not
    the caller's, so the client can retry with it. Best effort: when the store itself is what
    failed, the write may fail too, and the 503 still goes out."""
    try:
        restore_refresh_token(refresh_token, user_info)
    except Exception:  # noqa: BLE001
        logger.warning("could not restore a spent refresh token", exc_info=True)


def _reissue(refresh_token: str, user_info: dict) -> dict:
    """``reissue_for_refreshed_identity``, with a device whose revocation cannot be read answered
    as a retryable 503 that keeps the token, not a 500 that loses it (INT-104)."""
    try:
        issued = reissue_for_refreshed_identity(user_info)
    except AuthStateUnavailable:
        _put_back(refresh_token, user_info)
        raise _auth_state_unavailable() from None
    if issued is None:
        raise HTTPException(status_code=401, detail="Device access has been revoked.")
    return issued


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest, http_request: Request):
    user_info = consume_refresh_token(request.refresh_token, retry_key=request.retry_key)
    if user_info is None:
        if refresh_token_of_erased_account(request.refresh_token):
            # INT-010: the client must wipe its copy and stop, not fall back to /token.
            raise HTTPException(status_code=401, detail=dict(ACCOUNT_DELETED_DETAIL))
        raise HTTPException(status_code=401, detail="Refresh token is invalid, expired, or already used.")
    # A desktop app's refresh token is bound to its device (FSA-004): the re-issued pair keeps
    # the device claims, and a revoked device gets nothing even with a valid refresh token.
    try:
        if user_info.get("device_id") and user_info.get("channel") != CONSUMER_DESKTOP_CHANNEL:
            # An operator's desktop session ends below the console role, so the Access-page
            # assignment must actually be read: an outage read as "none assigned" would demote
            # them and sign them out with the token already spent (INT-104).
            with role_lookup_must_succeed():
                await stamp_role(user_info)
        else:
            await stamp_role(user_info)
    except RoleLookupUnavailable:
        _put_back(request.refresh_token, user_info)
        raise _auth_state_unavailable() from None
    if user_info.get("device_id") and not is_console_operator(user_info["role"]):
        # F-09: desktop sign-in is for operators -- and, since SaaS P1-E, entitled customers --
        # and a session issued to someone since demoted or downgraded must not outlive it. The
        # refresh token was spent above, so the session ends here; phone and web sessions carry
        # no device_id.
        if user_info.get("channel") == CONSUMER_DESKTOP_CHANNEL:
            try:
                entitled = await _engineer_entitled(http_request, str(user_info["uid"]))
            except _EntitlementUnavailable:
                _put_back(request.refresh_token, user_info)  # retry with the same token
                raise
            if entitled:
                return _reissue(request.refresh_token, user_info)
            raise HTTPException(status_code=401, detail=_UPGRADE_DETAIL, headers=_PLAN_REQUIRED)
        raise HTTPException(
            status_code=401,
            detail=f"Desktop sign-in now requires at least {CONSOLE_MIN_ROLE}; please sign in again.",
        )
    return _reissue(request.refresh_token, user_info)


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
async def start_desktop_pairing(request: Request, body: PairingStartRequest | None = None):
    # Unauthenticated whatever the customer switch says, and each call stores a pending pairing:
    # throttled per address either way (F39).
    _sliding_limit(_pair_start_hits, _consumer_client_key(request), DESKTOP_SIGN_INS_PER_MINUTE, 60.0,
                   "Too many pairing attempts; try again in a minute.")
    return device_pairing.start_pairing(device_name=body.device_name if body else None)


@router.post("/desktop/pair/approve", response_model=PairingApproveResponse)
async def approve_desktop_pairing(
    body: PairingApproveRequest, request: Request, user_info: dict = Depends(verify_access_token),
):
    """Requires the approving client's own valid access token -- this is what stands in for
    attestation here: proof of an already-authenticated session, not proof of the new device's
    integrity (ADR-011 explains why the latter has no desktop equivalent worth relying on).

    The approver must also be someone the host-agent socket will serve (INT-003): otherwise the
    daemon pairs, is closed with 4401 on its first connect, and gives up for good while the app
    shows "reconnecting". Refused here with the reason, before the code is consumed. With
    ``AURA_ENGINEER_FOR_CUSTOMERS`` the socket also serves an entitled customer (SaaS P1-E), who
    then carries the ``consumer_desktop`` cap into the daemon token. Per person: a bounded number
    of paired daemons, and a few wrong codes an hour."""
    user_info = dict(user_info)
    if not user_info.get("role"):
        await stamp_role(user_info)
    if _customers_open(request):
        try:
            _require_host_agent_capability(user_info)
        except HTTPException:
            if not await _engineer_entitled(request, str(user_info["uid"])):
                raise HTTPException(status_code=403, detail=_UPGRADE_DETAIL, headers=_PLAN_REQUIRED) from None
            user_info["channel"] = CONSUMER_DESKTOP_CHANNEL
            await stamp_role(user_info)
    else:
        _require_host_agent_capability(user_info)
    uid = str(user_info["uid"])
    daemons = [d for d in list_devices_for_uid(uid) if d.get("kind") != "app"]
    if _customers_open(request) and len(daemons) >= MAX_DESKTOP_DEVICES_PER_USER:
        raise HTTPException(status_code=409, detail=(
            f"This account already has {MAX_DESKTOP_DEVICES_PER_USER} paired desktops; revoke one in "
            "Settings first."))
    if _customers_open(request):
        # Only wrong codes spend the quota (Plan E review #3): it bounds guessing, not pairing.
        _check_limit(_pair_approval_hits, uid, PAIR_APPROVALS_PER_HOUR, 3600.0,
                     "Too many wrong pairing codes; try again later.")
    device_name = device_pairing.pending_device_name(body.user_code)
    device_id = device_pairing.approve_pairing(body.user_code, user_info)
    if device_id is None:
        if _customers_open(request):
            _record_hit(_pair_approval_hits, uid, 3600.0)
        raise HTTPException(status_code=400, detail="Pairing code is invalid, expired, or already used.")
    return {"status": "approved", "device_id": device_id, "device_name": device_name}


def _require_host_agent_capability(user_info: dict) -> None:
    """403 unless the socket's own rule (``aura_core/api/host_agent.may_host``) would accept this
    account, with its capabilities resolved exactly as the gateway's authenticator resolves them
    (``aura_core/security/role_capabilities``) -- reused, not restated, so the two cannot drift."""
    from aura_core.api import host_agent
    from aura_core.api.v3 import Principal
    from aura_core.config import load_settings
    from aura_core.domain.identifiers import UserId
    from aura_core.security.role_capabilities import ENGINEER_AGENT, capabilities_for_role

    role = get_user_role(user_info)
    principal = Principal(
        user_id=UserId(str(user_info["uid"])),
        email=user_info.get("email") or "",
        role=role,
        capabilities=capabilities_for_role(load_settings().security, role),
    )
    if host_agent.may_host(principal):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            f"Pairing a desktop agent requires the {ENGINEER_AGENT} capability, which your role "
            f"({role}) does not have on this deployment; the daemon would be refused when it "
            "connects. An operator grants it by adding the role to AURA_ENGINEER_AGENT_ROLES."
        ),
    )


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


@router.post("/desktop/exchange", response_model=DesktopExchangeResponse)
async def desktop_exchange(body: DesktopExchangeRequest, request: Request):
    """Complete the desktop app's Google sign-in: trade its one-time ``code`` for an ID token.

    The app cannot do this call itself. Google's token endpoint refuses the exchange for our
    "Desktop app" client without the client secret (``invalid_request: client_secret is
    missing``), and a secret shipped in the binary is a secret on every user's disk -- so the
    server holds it (``GOOGLE_DESKTOP_CLIENT_SECRET``) and adds it here. See
    ``services/google_oauth.py``.

    Public, with no session, because it runs *before* one exists: it is the step that produces
    the identity proof ``/desktop/login`` then verifies. What stops it being a relay for other
    people's codes is that the code is useless without the PKCE verifier only the requesting app
    holds, the redirect is pinned to the loopback form, and the client id is the server's own.
    It mints no session and returns nothing but the ID token; the operator gate and
    device-bound issuance stay in ``/desktop/login``.

    Throttled per client address before Google is called (F39): each call is an outbound request
    made on an unauthenticated caller's behalf.
    """
    _sliding_limit(_desktop_exchange_hits, _consumer_client_key(request), DESKTOP_SIGN_INS_PER_MINUTE,
                   60.0, "Too many sign-in attempts; try again in a minute.")
    try:
        id_token = await exchange_desktop_code(
            code=body.code,
            code_verifier=body.code_verifier,
            redirect_uri=body.redirect_uri,
        )
    except GoogleExchangeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"id_token": id_token}


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
    if _customers_open(request):  # the switch off leaves operator-only behaviour untouched
        _sliding_limit(_desktop_hits, _consumer_client_key(request), DESKTOP_SIGN_INS_PER_MINUTE, 60.0,
                       "Too many sign-in attempts; try again in a minute.")
    user_info = await verify_identity_token(request, authorization)
    return await _finish_desktop_sign_in(request, user_info, body.device_name if body else None)


async def _finish_desktop_sign_in(request: Request, user_info: dict, device_name: str | None) -> dict:
    """Everything after the identity is proven, shared by ``/desktop/login`` (Google, verified
    here) and ``/desktop/firebase-exchange`` (Firebase, verified at hand-off): the same gate, the
    same device-bound issuance. One path, so the two sign-ins cannot drift apart."""
    await stamp_role(user_info)
    await _desktop_gate(request, user_info, what="desktop sign-in", where="the desktop app")
    # Both callers are a person completing a browser sign-in; renewal is /refresh (F37).
    _admit_erased_subject(request, user_info, deliberate=True)
    await _persist_user(user_info)
    if user_info.get("channel") == CONSUMER_DESKTOP_CHANNEL:
        # Each sign-in is a new app device: keep the newest few rather than an unbounded pile of
        # live sessions (Plan E review #2). Paired daemons are never evicted here.
        apps = [d for d in list_devices_for_uid(str(user_info["uid"])) if d.get("kind") == "app"]
        for stale in apps[MAX_DESKTOP_DEVICES_PER_USER - 1:]:  # newest first
            revoke_device(stale["device_id"], requesting_uid=str(user_info["uid"]))
    device_id = secrets.token_urlsafe(16)
    return issue_desktop_session(user_info, device_id=device_id, device_name=device_name)


# --- Desktop sign-in through the browser (module readiness WP12; services/desktop_handoff.py) ---

#: Hand-offs and exchanges per client address per minute. Both are open entry points, whatever
#: the customer switch says: the hand-off verifies a token, the exchange spends a code.
DESKTOP_HANDOFFS_PER_MINUTE = 10
_handoff_hits: dict = {}
_handoff_exchange_hits: dict = {}

_PKCE_CHALLENGE = r"^[A-Za-z0-9_-]{43}$"  # base64url SHA-256, unpadded (RFC 7636 S256)
_PKCE_VERIFIER = r"^[A-Za-z0-9\-._~]+$"


class DesktopHandoffRequest(BaseModel):
    #: The app's PKCE challenge, S256 only: the code this returns is useless without its verifier.
    code_challenge: str = Field(min_length=43, max_length=43, pattern=_PKCE_CHALLENGE)
    #: Where the page will send the browser: the app's loopback listener, nothing else.
    redirect_uri: str = Field(min_length=1, max_length=64)


class DesktopHandoffResponse(BaseModel):
    code: str
    expires_in: int


class DesktopFirebaseExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    code_verifier: str = Field(min_length=43, max_length=128, pattern=_PKCE_VERIFIER)
    redirect_uri: str = Field(min_length=1, max_length=64)
    device_name: str | None = Field(default=None, max_length=128)


def _handoff_rate_limit(request: Request) -> None:
    _sliding_limit(_handoff_hits, _consumer_client_key(request), DESKTOP_HANDOFFS_PER_MINUTE, 60.0,
                   "Too many sign-in attempts; try again in a minute.")


async def _handoff_identity(
    request: Request, authorization: HTTPAuthorizationCredentials | None = Depends(_web_bearer),
) -> dict:
    # A dependency, so a request with no identity proof is refused (401) before its body is even
    # parsed; looked up at call time so tests can stand in for the Firebase check.
    return await verify_identity_token(request, authorization)


@router.post("/desktop/handoff", response_model=DesktopHandoffResponse,
             dependencies=[Depends(_handoff_rate_limit)])
async def desktop_handoff(
    body: DesktopHandoffRequest,
    user_info: dict = Depends(_handoff_identity),
):
    """Step 2 of the browser hand-off: the web page, signed in with Firebase, trades its ID token
    for a one-time code bound to the app's PKCE challenge and loopback redirect.

    It mints no session and grants nothing by itself; the desktop gate runs at the exchange, where
    the session is issued. The redirect must be the loopback form, so the page can never be made to
    send a code to another site -- and a code that leaks anyway is useless without the verifier."""
    if not is_loopback_redirect(body.redirect_uri):
        raise HTTPException(status_code=400, detail="The redirect must be http://127.0.0.1:<port>/.")
    code = desktop_handoff_store.create(
        user_info, code_challenge=body.code_challenge, redirect_uri=body.redirect_uri,
    )
    return {"code": code, "expires_in": desktop_handoff_store.HANDOFF_TTL_S}


@router.post("/desktop/firebase-exchange", response_model=DesktopLoginResponse)
async def desktop_firebase_exchange(request: Request, body: DesktopFirebaseExchangeRequest):
    """Step 3: the desktop app redeems the code with its PKCE verifier for a device-bound session,
    through the same gate as ``/desktop/login`` (operators; entitled customers when the switch is
    on; T-6). The code is spent by this call whatever its outcome."""
    _sliding_limit(_handoff_exchange_hits, _consumer_client_key(request), DESKTOP_HANDOFFS_PER_MINUTE,
                   60.0, "Too many sign-in attempts; try again in a minute.")
    try:
        user_info = desktop_handoff_store.redeem(
            body.code, code_verifier=body.code_verifier, redirect_uri=body.redirect_uri,
        )
    except desktop_handoff_store.HandoffError:
        raise HTTPException(
            status_code=400, detail="Sign-in code is invalid, expired, or already used. Sign in again.",
        ) from None
    return await _finish_desktop_sign_in(request, user_info, body.device_name)
