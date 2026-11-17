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

# A paired *daemon* (services/device_pairing.py, aura_hostd) has no refresh token at all --
# ADR-011's "device-scoped, revocable, and narrower than a full session token" is met
# differently there than for a mobile/web session: one long-lived, self-contained access token
# scoped to the host-agent socket (HOST_AGENT_SCOPE) instead of a short-lived access token
# renewed by a long-lived refresh token. Long enough that a daemon someone uses day to day
# doesn't need to be re-paired constantly (aura_hostd has no refresh-on-401 logic); "revocable"
# comes from the device-id check below. The desktop *app's* own sign-in used to get this same
# 30-day form unscoped -- a month-long full session that could fetch /keys/data (audit FSA-004).
# It now gets an ordinary short access token plus a device-bound refresh token
# (`issue_desktop_session`), so this TTL applies to the scoped daemon token only.
DEVICE_ACCESS_TOKEN_TTL = timedelta(days=30)
_REVOKED_DEVICE_PREFIX = "revoked_device:"

# Dev-mode fallback: a random secret generated once per process if
# JWT_SECRET isn't configured. Every existing session is invalidated on
# restart in this mode — fine for local dev, not for production (see
# config.py's jwt_secret doc comment).
_dev_secret = secrets.token_urlsafe(32)
if not (settings.jwt_secret or os.environ.get("JWT_SECRET") or os.environ.get("AURA_JWT_SECRET")):
    logger.warning("JWT_SECRET is not configured — using a random per-process dev secret. Set JWT_SECRET for stable sessions across restarts.")


def configured_secret() -> str:
    """The operator-configured signing secret, or "" when only the dev fallback exists."""
    # Read env at call time so a secret hydrated after Settings() was
    # constructed (AURA_JWT_SECRET from the Docker secret file) is still used
    # instead of the per-process random fallback.
    return (
        settings.jwt_secret
        or os.environ.get("JWT_SECRET")
        or os.environ.get("AURA_JWT_SECRET")
        or ""
    )


def _secret() -> str:
    return configured_secret() or _dev_secret


# --- Environment binding (F-03) -------------------------------------------------------------
# The prod and dev stacks share a VPS. A token one of them mints must never authenticate on the
# other, so every token carries `iss` (which deployment minted it) and `aud` (which API it is
# for), and verification requires both to match this process's own values. Separate signing keys
# per stack (deploy/docker-compose.dev.yml's dev_jwt_secret) are the primary control; these
# claims are the second, and the only one left if the keys are ever shared again.

#: Until when a token with neither `iss` nor `aud` -- one minted before this binding shipped --
#: is still honoured. Mobile/web access tokens live 15 minutes and are re-minted with the claims on
#: the next refresh, so in practice this window exists for DEVICE_ACCESS_TOKEN_TTL (30-day) desktop
#: device tokens, which have no refresh path and would otherwise force every paired desktop to
#: re-pair on deploy. Fixed rather than "deploy time + 30 days" because the process has no durable
#: record of when it was first deployed; overridable via AURA_JWT_LEGACY_TOKENS_UNTIL.
_DEFAULT_LEGACY_TOKENS_UNTIL = datetime(2026, 12, 15, tzinfo=timezone.utc)
_warned_bad_legacy_cutoff = False


def _environment() -> str:
    raw = os.environ.get("AURA_ENVIRONMENT") or os.environ.get("ENVIRONMENT") or "local"
    return raw.strip().lower() or "local"


def _issuer() -> str:
    # Call time, like _secret(): Settings() is constructed at import, before tests or secret
    # hydration may have set the environment.
    return settings.jwt_issuer or os.environ.get("AURA_JWT_ISSUER") or f"aura-{_environment()}"


def _audience() -> str:
    return settings.jwt_audience or os.environ.get("AURA_JWT_AUDIENCE") or f"aura-api-{_environment()}"


def _legacy_tokens_until() -> datetime:
    global _warned_bad_legacy_cutoff
    raw = (settings.jwt_legacy_tokens_until or os.environ.get("AURA_JWT_LEGACY_TOKENS_UNTIL") or "").strip()
    if not raw:
        return _DEFAULT_LEGACY_TOKENS_UNTIL
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        # Fail closed: a typo must not silently extend acceptance of unbound tokens.
        if not _warned_bad_legacy_cutoff:
            logger.warning("AURA_JWT_LEGACY_TOKENS_UNTIL=%r is not ISO-8601; refusing legacy tokens.", raw)
            _warned_bad_legacy_cutoff = True
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decode_access_token(token: str) -> Dict[str, Any]:
    """Signature, expiry, issuer and audience -- with one migration exception.

    Rule for tokens minted before `iss`/`aud` existed: a token carrying NEITHER claim is accepted
    as a legacy token until ``_legacy_tokens_until()`` and refused after it. A token carrying
    either claim is held to both: present-and-wrong is refused, and so is a token with only one
    of the two (nothing this server ever minted has that shape, so it can only be forged or from
    another issuer). The legacy window cannot be used to replay a *dev* token on prod: dev now
    signs with its own key, so such a token already fails the signature check above it. What the
    window does concede is that a holder of a pre-split signing key can mint claimless tokens
    until the cut-off -- which is why the cut-off is bounded and an operator can pull it forward.
    """
    try:
        return jwt.decode(
            token, _secret(), algorithms=[ALGORITHM],
            issuer=_issuer(), audience=_audience(),
            options={"require": ["iss", "aud"]},
        )
    except jwt.MissingRequiredClaimError as exc:
        if exc.claim not in ("iss", "aud"):
            raise
        missing = exc
    # Signature and expiry are still verified; only the audience check is off, because PyJWT
    # refuses a token that has `aud` when no audience is expected -- handled explicitly below.
    payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM], options={"verify_aud": False})
    if "iss" in payload or "aud" in payload:
        raise missing
    if datetime.now(timezone.utc) >= _legacy_tokens_until():
        raise jwt.InvalidTokenError("token predates issuer/audience binding and its grace period has ended")
    return payload


# In-process fallback store for refresh tokens if Redis is unreachable.
# Does not survive restart and isn't shared across the Dockerfile's 4
# uvicorn workers — logged clearly so this limitation is visible in prod.
_local_refresh_store: Dict[str, Dict[str, Any]] = {}
_warned_no_redis = False

# --- Revocation index and refresh-token families (audit FSA-015) -----------------------------
# Every refresh token is also a member of three sets -- its user's, its device's (desktop
# sessions) and its *family's* -- so revoking an account, a device or a compromised rotation
# chain reads one set instead of SCANning every key in Redis and decoding every value. A family
# is one rotation chain: the token minted at sign-in and every successor `consume_refresh_token`
# rotated it into. A consumed token leaves a `refresh_used:` marker naming its family for as long
# as it could have lived; presenting it again is a replay (two holders of one chain, one of them a
# thief), and the whole family is revoked. Records written before the index existed (no set
# membership, bare user_info under the key) are still swept by the old scan, but only until the
# legacy-token cut-off `_legacy_tokens_until` -- after it, only indexed records count.
_REFRESH_BY_UID_PREFIX = "refresh_by_uid:"
_REFRESH_BY_DEVICE_PREFIX = "refresh_by_device:"
_REFRESH_FAMILY_PREFIX = "refresh_family:"
_REFRESH_USED_PREFIX = "refresh_used:"
_DEVICES_BY_UID_PREFIX = "devices_by_uid:"
#: Private key carried on the identity `consume_refresh_token` returns, so the pair minted next
#: joins the same family. Never a claim: `create_access_token` copies named keys only.
_FAMILY_KEY = "_refresh_family"
_local_refresh_used: Dict[str, str] = {}
_local_refresh_families: Dict[str, set] = {}


#: When the indexed code first ran against this Redis, written once (SET NX) and kept. The
#: fallback scan for records written before the index stays on until every such record has
#: expired -- the longest record lifetime after that moment -- whatever the calendar says.
_INDEX_SINCE_KEY = "refresh_index:since"
_INDEX_FREE_RECORD_TTL = max(REFRESH_TOKEN_TTL, DEVICE_ACCESS_TOKEN_TTL)
_index_marker_noted = False


def _note_index_deployed(redis_client) -> datetime:
    """Record (once) when the indexed code first ran, and return that moment. A marker that
    cannot be parsed counts as now: the conservative reading, which keeps the scan on."""
    global _index_marker_noted
    now = datetime.now(timezone.utc)
    if not _index_marker_noted:
        try:
            redis_client.set(_INDEX_SINCE_KEY, now.isoformat(), nx=True)
            _index_marker_noted = True
        except Exception:  # noqa: BLE001 -- a lost marker only keeps the scan on longer
            logger.warning("could not record the revocation-index deploy marker", exc_info=True)
    raw = redis_client.get(_INDEX_SINCE_KEY)
    if not raw:
        return now
    try:
        since = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else str(raw))
    except ValueError:
        return now
    return since if since.tzinfo else since.replace(tzinfo=timezone.utc)


def _index_free_records_possible() -> bool:
    """Whether records predating the revocation index may still be live, so the old full scan
    must back the index up: until the claimless-token cut-off (F-03), and in any case until
    every record written before the indexed code first ran here has expired (FSA-015). The
    cut-off is a fixed date; a deploy that lands late must not strand a 30-day daemon pairing
    outside both the index and the scan."""
    now = datetime.now(timezone.utc)
    if now < _legacy_tokens_until():
        return True
    redis_client = get_redis_client()
    if redis_client is None:
        return False
    return now < _note_index_deployed(redis_client) + _INDEX_FREE_RECORD_TTL


def _refresh_store_set(token: str, user_info: dict, ttl: timedelta) -> None:
    global _warned_no_redis
    info = dict(user_info)
    family = info.pop(_FAMILY_KEY, None) or secrets.token_urlsafe(16)
    ttl_s = int(ttl.total_seconds())
    uid = str(info.get("uid", ""))
    device_id = info.get("device_id")
    redis_client = get_redis_client()
    if redis_client is not None:
        if not _index_marker_noted:
            _note_index_deployed(redis_client)
        redis_client.setex(
            f"{_REFRESH_PREFIX}{token}", ttl_s, json.dumps({"user_info": info, "family": family})
        )
        for key in (
            f"{_REFRESH_BY_UID_PREFIX}{uid}",
            f"{_REFRESH_FAMILY_PREFIX}{family}",
            *([f"{_REFRESH_BY_DEVICE_PREFIX}{device_id}"] if device_id else []),
        ):
            redis_client.sadd(key, token)
            # The set outlives its newest member by nothing: a stale member is harmless (the
            # token key itself is gone), an unbounded set is not.
            redis_client.expire(key, ttl_s)
        return
    if not _warned_no_redis:
        logger.warning("Redis unavailable: refresh tokens are stored in-process only (lost on restart, not shared across workers).")
        _warned_no_redis = True
    _local_refresh_store[token] = {
        "user_info": info, "expires_at": datetime.now(timezone.utc) + ttl, "family": family,
    }
    _local_refresh_families.setdefault(family, set()).add(token)


_local_refresh_lock = threading.Lock()


def _parse_refresh_entry(raw: str) -> tuple[dict, Optional[str]]:
    """``(user_info, family)`` from a stored value, in either shape: the indexed one
    (``{"user_info", "family"}``) or the bare user_info written before the index."""
    entry = json.loads(raw)
    if isinstance(entry, dict) and "user_info" in entry:
        return dict(entry["user_info"]), entry.get("family")
    return dict(entry), None


def _unindex(redis_client: Any, token: str, user_info: dict, family: Optional[str]) -> None:
    redis_client.srem(f"{_REFRESH_BY_UID_PREFIX}{user_info.get('uid', '')}", token)
    if user_info.get("device_id"):
        redis_client.srem(f"{_REFRESH_BY_DEVICE_PREFIX}{user_info['device_id']}", token)
    if family:
        redis_client.srem(f"{_REFRESH_FAMILY_PREFIX}{family}", token)


def _refresh_store_take(token: str) -> Optional[dict]:
    """Atomically read *and* consume a refresh token -- the only safe primitive for rotation.

    A GET followed by a DEL is two round trips: concurrent refreshes of one token (the gateway
    runs several workers sharing Redis) could all read it before any deleted it, and each mint a
    session -- the stolen-token race rotation exists to close. GETDEL (Redis >= 6.2; production
    runs 7) is one command, so exactly one caller gets the value.

    The identity comes back carrying ``_FAMILY_KEY`` so the successor joins the same family, and
    the consumed token leaves a ``refresh_used:`` marker for replay detection.
    """
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.getdel(f"{_REFRESH_PREFIX}{token}")
        if not raw:
            return None
        user_info, family = _parse_refresh_entry(raw)
        _unindex(redis_client, token, user_info, family)
        if family:
            redis_client.setex(f"{_REFRESH_USED_PREFIX}{token}", int(REFRESH_TOKEN_TTL.total_seconds()), family)
            user_info[_FAMILY_KEY] = family
        return user_info
    with _local_refresh_lock:
        entry = _local_refresh_store.pop(token, None)
    if entry is None or entry["expires_at"] < datetime.now(timezone.utc):
        return None
    family = entry.get("family")
    user_info = dict(entry["user_info"])
    if family:
        _local_refresh_families.get(family, set()).discard(token)
        _local_refresh_used[token] = family
        user_info[_FAMILY_KEY] = family
    return user_info


def restore_refresh_token(token: str, user_info: dict) -> None:
    """Put back a refresh token ``consume_refresh_token`` just spent, when the request then could
    not be answered for a reason that is not the caller's (a plan lookup failed): the client may
    retry with the same token. ``user_info`` is what consumption returned, family included."""
    _refresh_store_set(token, user_info, REFRESH_TOKEN_TTL)


def _refresh_store_delete(token: str) -> bool:
    """Drop one refresh token and its index entries. True when it existed."""
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.getdel(f"{_REFRESH_PREFIX}{token}")
        if not raw:
            return False
        user_info, family = _parse_refresh_entry(raw)
        _unindex(redis_client, token, user_info, family)
        return True
    entry = _local_refresh_store.pop(token, None)
    if entry is None:
        return False
    if entry.get("family"):
        _local_refresh_families.get(entry["family"], set()).discard(token)
    return True


def _used_family(token: str) -> Optional[str]:
    """The family of an already-consumed token, if it was consumed within its lifetime."""
    redis_client = get_redis_client()
    if redis_client is not None:
        return redis_client.get(f"{_REFRESH_USED_PREFIX}{token}")
    return _local_refresh_used.get(token)


def _revoke_family(family: str) -> int:
    """Drop every live token in a rotation chain. Returns how many."""
    redis_client = get_redis_client()
    if redis_client is not None:
        tokens = set(redis_client.smembers(f"{_REFRESH_FAMILY_PREFIX}{family}"))
        revoked = sum(1 for token in tokens if _refresh_store_delete(token))
        redis_client.delete(f"{_REFRESH_FAMILY_PREFIX}{family}")
        return revoked
    tokens = _local_refresh_families.pop(family, set())
    return sum(1 for token in list(tokens) if _refresh_store_delete(token))


def _scan_refresh_tokens(redis_client: Any, predicate) -> list:
    """The pre-index sweep: every refresh key whose stored identity satisfies ``predicate``."""
    found = []
    for key in redis_client.scan_iter(f"{_REFRESH_PREFIX}*"):
        raw = redis_client.get(key)
        if not raw:
            continue
        try:
            user_info, _family = _parse_refresh_entry(raw)
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        if predicate(user_info):
            found.append(str(key)[len(_REFRESH_PREFIX):])
    return found


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
    # Server-set from the verified identity token (services.security.verify_identity_token):
    # team actions require a verified address (SaaS P1-B, spec 5.1).
    payload["email_verified"] = user_info.get("email_verified") is True
    if user_info.get("channel"):
        # Server-set, never client-set: rbac.get_user_role caps a consumer_web identity at User
        # on every request, so this claim must survive into the access token (SaaS P1-B).
        payload["channel"] = user_info["channel"]
    if extra_claims:
        payload.update(extra_claims)
    # After extra_claims so a caller can never mint a token bound to another deployment (F-03).
    payload["iss"] = _issuer()
    payload["aud"] = _audience()
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
#: What a device *is*, beside who owns it: ``{"name", "kind", "created_at"}`` so the account
#: owner can tell their laptop from an unknown machine when listing or revoking (FSA-004).
#: ``kind`` is ``"app"`` (the desktop app's own sign-in) or ``"daemon"`` (a paired aura_hostd).
_DEVICE_META_PREFIX = "device_meta:"
_local_device_meta: Dict[str, Dict[str, Any]] = {}
DEVICE_NAME_MAX_CHARS = 80


def _clean_device_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    cleaned = "".join(ch for ch in str(name) if ch.isprintable()).strip()
    return cleaned[:DEVICE_NAME_MAX_CHARS] or None


def _register_device_meta(device_id: str, *, name: Optional[str], kind: str) -> None:
    """Record name/kind/created_at for ``device_id`` unless already recorded (a refresh must not
    rename a device or reset its pairing date). Same TTL as the owner record."""
    if _device_meta(device_id) is not None:
        return
    meta = {
        "name": _clean_device_name(name),
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(
            f"{_DEVICE_META_PREFIX}{device_id}",
            int(DEVICE_ACCESS_TOKEN_TTL.total_seconds()),
            json.dumps(meta),
        )
        return
    _local_device_meta[device_id] = meta


def _device_meta(device_id: str) -> Optional[Dict[str, Any]]:
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.get(f"{_DEVICE_META_PREFIX}{device_id}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return _local_device_meta.get(device_id)


def _owned_device_ids(uid: str) -> list:
    owned: list = []
    redis_client = get_redis_client()
    if redis_client is not None:
        # The index may name a device whose owner record has since expired; the record decides.
        owned += [
            device_id for device_id in sorted(redis_client.smembers(f"{_DEVICES_BY_UID_PREFIX}{uid}"))
            if redis_client.get(f"{_DEVICE_OWNER_PREFIX}{device_id}") == uid
        ]
        if _index_free_records_possible():
            for key in redis_client.scan_iter(f"{_DEVICE_OWNER_PREFIX}*"):
                if redis_client.get(key) == uid:
                    owned.append(str(key)[len(_DEVICE_OWNER_PREFIX):])
    owned += [device for device, owner in _local_device_owners.items() if owner == uid]
    return list(dict.fromkeys(owned))


def list_devices_for_uid(uid: str) -> list:
    """Every unrevoked desktop device (app sign-in or paired daemon) ``uid`` owns, as
    ``{"device_id", "name", "kind", "created_at"}`` -- what Settings shows beside a Revoke
    button (FSA-004). Devices registered before metadata existed appear with ``name`` None and
    ``kind`` "unknown" rather than being hidden: an unnamed entry the owner cannot place is
    exactly the one they should be able to revoke."""
    devices = []
    for device_id in _owned_device_ids(uid):
        if is_device_revoked(device_id):
            continue
        meta = _device_meta(device_id) or {}
        devices.append({
            "device_id": device_id,
            "name": meta.get("name"),
            "kind": meta.get("kind") or "unknown",
            "created_at": meta.get("created_at"),
        })
    devices.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return devices


def _register_device_owner(device_id: str, uid: str) -> None:
    """So a revoke request can be checked against who actually owns the device (below) -- without
    this, any authenticated caller who learned or guessed a device_id could revoke someone else's
    paired desktop. TTL matches the token's own lifetime: once the token would have expired via
    its own `exp` claim anyway, there is nothing left for a revoke to protect against."""
    redis_client = get_redis_client()
    if redis_client is not None:
        ttl_s = int(DEVICE_ACCESS_TOKEN_TTL.total_seconds())
        redis_client.setex(f"{_DEVICE_OWNER_PREFIX}{device_id}", ttl_s, uid)
        redis_client.sadd(f"{_DEVICES_BY_UID_PREFIX}{uid}", device_id)
        redis_client.expire(f"{_DEVICES_BY_UID_PREFIX}{uid}", ttl_s)
        return
    _local_device_owners[device_id] = uid


def _device_owner(device_id: str) -> Optional[str]:
    redis_client = get_redis_client()
    if redis_client is not None:
        return redis_client.get(f"{_DEVICE_OWNER_PREFIX}{device_id}")
    return _local_device_owners.get(device_id)


#: The only scope a token may carry today: the host-agent WebSocket and nothing else.
HOST_AGENT_SCOPE = "host_agent"


def issue_device_token(
    user_info: dict, *, device_id: str, scope: Optional[str] = None,
    device_name: Optional[str] = None,
) -> Dict[str, Any]:
    """A single long-lived access token for a paired desktop device -- no refresh token, see
    DEVICE_ACCESS_TOKEN_TTL's doc comment. Called once a pairing has been approved by the user on
    an already-authenticated client (``scope=HOST_AGENT_SCOPE``: the daemon needs only its
    socket), and by the desktop app's own sign-in (no scope: the app uses the whole API).

    A scoped token is refused by every route that does not name its scope (F-09). A paired
    daemon's token used to be a full 30-day session -- chat, sync, admin for an admin -- obtained
    by approving a pairing code, which made a phished code worth the whole account."""
    extra: Dict[str, Any] = {"platform": "desktop", "device_id": device_id}
    if scope:
        extra["scope"] = scope
    access_token = create_access_token(user_info, ttl=DEVICE_ACCESS_TOKEN_TTL, extra_claims=extra)
    _register_device_owner(device_id, str(user_info["uid"]))
    _register_device_meta(device_id, name=device_name, kind="daemon" if scope else "app")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": int(DEVICE_ACCESS_TOKEN_TTL.total_seconds()),
        "device_id": device_id,
    }


def issue_desktop_session(
    user_info: dict, *, device_id: str, device_name: Optional[str] = None,
) -> Dict[str, Any]:
    """The desktop *app's* session (audit FSA-004): an ordinary ``ACCESS_TOKEN_TTL`` access
    token carrying ``platform``/``device_id`` claims, plus a refresh token bound to the device.

    A stolen access token is therefore worth minutes, not a month; the refresh token rotates on
    every use like a mobile/web one (``consume_refresh_token``), and because the stored identity
    carries ``device_id``, ``/auth/refresh`` re-issues this same shape and refuses once the device
    is revoked -- revoking from Settings ends the session at the next refresh at the latest, and
    ``revoke_device`` also drops the device's outstanding refresh tokens so it cannot renew at
    all. ``device_name`` (the machine's hostname, supplied by the app) is what the owner sees in
    the device list."""
    uid = str(user_info["uid"])
    extra: Dict[str, Any] = {"platform": "desktop", "device_id": device_id}
    access_token = create_access_token(user_info, extra_claims=extra)
    refresh_token = secrets.token_urlsafe(48)
    stored = dict(user_info)
    stored["device_id"] = device_id
    _refresh_store_set(refresh_token, stored, REFRESH_TOKEN_TTL)
    _register_device_owner(device_id, uid)
    _register_device_meta(device_id, name=device_name, kind="app")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
        "device_id": device_id,
    }


def reissue_for_refreshed_identity(user_info: dict) -> Optional[Dict[str, Any]]:
    """Mint the right pair for an identity ``consume_refresh_token`` returned: a desktop-device
    session when it carries ``device_id`` (``None`` if that device has since been revoked), an
    ordinary pair otherwise. The ``/auth/refresh`` route and ``refresh_token_pair`` share this
    so neither can forget the device binding."""
    device_id = user_info.get("device_id")
    if not device_id:
        return issue_token_pair(user_info)
    if is_device_revoked(device_id):
        return None
    clean = {k: v for k, v in user_info.items() if k != "device_id"}
    return issue_desktop_session(clean, device_id=str(device_id))


_local_revoked_devices: set = set()
_warned_no_redis_revocation = False


def _revoke_refresh_tokens_for_device(device_id: str) -> int:
    """Drop every refresh token bound to ``device_id`` (issued by ``issue_desktop_session``)."""
    revoked = 0
    redis_client = get_redis_client()
    if redis_client is not None:
        indexed = set(redis_client.smembers(f"{_REFRESH_BY_DEVICE_PREFIX}{device_id}"))
        revoked += sum(1 for token in indexed if _refresh_store_delete(token))
        redis_client.delete(f"{_REFRESH_BY_DEVICE_PREFIX}{device_id}")
        if _index_free_records_possible():
            for token in _scan_refresh_tokens(redis_client, lambda info: info.get("device_id") == device_id):
                if token not in indexed and _refresh_store_delete(token):
                    revoked += 1
    stale = [
        token for token, entry in _local_refresh_store.items()
        if entry.get("user_info", {}).get("device_id") == device_id
    ]
    for token in stale:
        if _refresh_store_delete(token):
            revoked += 1
    return revoked


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
        # A desktop app session's refresh tokens die with the device (FSA-004).
        _revoke_refresh_tokens_for_device(device_id)
        return True
    if not _warned_no_redis_revocation:
        logger.warning("Redis unavailable: device revocations are stored in-process only (lost on restart, not shared across workers).")
        _warned_no_redis_revocation = True
    _local_revoked_devices.add(device_id)
    _revoke_refresh_tokens_for_device(device_id)
    return True


def revoke_all_devices_for_uid(uid: str) -> int:
    """Revoke every paired device ``uid`` owns. Returns how many.

    A device token is a 30-day access token with no refresh (see DEVICE_ACCESS_TOKEN_TTL), so
    revoking someone's refresh tokens alone leaves a paired desktop signed in -- with whatever role
    it was issued under -- for the rest of that month. Demotion and erasure call this too.
    """
    return sum(1 for device_id in _owned_device_ids(uid) if revoke_device(device_id, requesting_uid=uid))


_ERASED_ACCOUNT_PREFIX = "erased_account:"
_local_erased_accounts: Dict[str, float] = {}


def mark_account_erased(uid: str) -> None:
    """Refuse every access token issued to ``uid`` up to now (account erasure).

    Erasure revokes the refresh tokens and paired devices, but an ordinary access token stays
    valid until it expires (``ACCESS_TOKEN_TTL``): another phone still signed in to the account
    could sync or chat in that window, and ``get_or_create_user`` would recreate the ``users``
    row and write data back into an account that was just deleted. ``verify_access_token``
    refuses a token whose ``iat`` is not after this moment. Kept for the longest access-token
    lifetime (a device token's), after which no such token can exist. Signing in again later
    issues newer tokens and starts a fresh account, as it should.
    """
    now = datetime.now(timezone.utc).timestamp()
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(
            f"{_ERASED_ACCOUNT_PREFIX}{uid}", int(DEVICE_ACCESS_TOKEN_TTL.total_seconds()), str(now)
        )
        return
    _local_erased_accounts[uid] = now


def _account_erased_at(uid: str) -> Optional[float]:
    redis_client = get_redis_client()
    if redis_client is not None:
        try:
            raw = redis_client.get(f"{_ERASED_ACCOUNT_PREFIX}{uid}")
        except Exception:
            # The same store backs refresh tokens; if it is down, sign-in is down too. Do not
            # turn every authenticated request into a 500 over this one check.
            logger.warning("erased-account check unavailable (redis error)")
            return None
        if raw is None:
            return None
        return float(raw.decode() if isinstance(raw, bytes) else raw)
    return _local_erased_accounts.get(uid)


def is_device_revoked(device_id: str) -> bool:
    redis_client = get_redis_client()
    if redis_client is not None:
        return redis_client.exists(f"{_REVOKED_DEVICE_PREFIX}{device_id}") > 0
    return device_id in _local_revoked_devices


def consume_refresh_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Spend a refresh token (single use) and return the identity it was issued for, with the
    previously stamped role removed, ready to be re-resolved; None when it is not valid.

    The stored ``role`` is the claim this server stamped at the previous issue. Left in, it is
    one of the candidates ``get_user_role`` takes the max of, so a role once granted would
    survive every rotation for as long as the user keeps refreshing -- removing someone from
    ADMIN_EMAILS or roles.json, or demoting them on the Access page, would never take effect.
    """
    user_info = _refresh_store_take(refresh_token)
    if user_info is None:
        family = _used_family(refresh_token)
        if family:
            # Replay (audit FSA-015): this token was already rotated. Whoever holds the live
            # successor may be the thief, so the whole chain ends and the user signs in again.
            revoked = _revoke_family(family)
            logger.warning(
                "refresh token replayed after rotation; its family was revoked",
                extra={"revoked": revoked},
            )
        return None
    fresh = dict(user_info)
    fresh.pop("role", None)
    return fresh


def refresh_token_pair(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Rotates the refresh token on every use (the old one stops working
    immediately) so a stolen-but-unused refresh token has a shrinking
    window of validity rather than living for the full 30 days.

    Resolves the role without the Access page's assignment, which needs the database; the
    ``/refresh`` endpoint uses ``consume_refresh_token`` and ``services.rbac.stamp_role``."""
    fresh = consume_refresh_token(refresh_token)
    if fresh is None:
        return None
    from services.rbac import stamp_resolved_role

    return reissue_for_refreshed_identity(stamp_resolved_role(fresh))


def revoke_refresh_token(refresh_token: str) -> None:
    _refresh_store_delete(refresh_token)


def revoke_all_for_uid(uid: str) -> int:
    """Drop every refresh token belonging to ``uid``. Returns how many were removed."""
    revoked = 0
    redis_client = get_redis_client()
    if redis_client is not None:
        indexed = set(redis_client.smembers(f"{_REFRESH_BY_UID_PREFIX}{uid}"))
        revoked += sum(1 for token in indexed if _refresh_store_delete(token))
        redis_client.delete(f"{_REFRESH_BY_UID_PREFIX}{uid}")
        if _index_free_records_possible():
            for token in _scan_refresh_tokens(redis_client, lambda info: info.get("uid") == uid):
                if token not in indexed and _refresh_store_delete(token):
                    revoked += 1
    stale = [
        token
        for token, entry in _local_refresh_store.items()
        if entry.get("user_info", {}).get("uid") == uid
    ]
    for token in stale:
        if _refresh_store_delete(token):
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
        payload = _decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token expired. Use /api/v3/auth/refresh.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token.")

    subject = payload.get("sub")
    erased_at = _account_erased_at(str(subject)) if subject else None
    if erased_at is not None and float(payload.get("iat") or 0) <= erased_at:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This account has been deleted.")

    device_id = payload.get("device_id")
    if device_id and is_device_revoked(device_id):
        # A device token has no refresh token to decline to renew (issue_device_token's doc
        # comment) -- this check is the entire revocation mechanism for it, so it must run on
        # every call, not just at issue time.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device access has been revoked.")

    scope = payload.get("scope")
    if scope and scope not in getattr(request, "allowed_token_scopes", ()):
        # A scoped token is good only where the caller explicitly accepts that scope -- the
        # host-agent socket's header carrier does; no HTTP route does.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"This token is limited to {scope} and cannot be used here.",
        )

    return {
        "uid": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "picture": payload.get("picture"),
        "role": payload.get("role"),
        "device_id": device_id,
        "channel": payload.get("channel"),
        "email_verified": payload.get("email_verified") is True,
    }
