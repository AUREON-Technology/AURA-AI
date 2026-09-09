"""Desktop device pairing (ADR-011, docs/architecture/011-desktop-engineer-agent.md).

`services/security.py::verify_device_integrity` has no desktop equivalent -- there is no
Play-Integrity/App-Attest-grade attestation for a desktop OS worth relying on, and the mock
tokens for windows/macos/linux platforms it lists are debug-only
(`settings.allow_mock_attestation`, refused in staging/production). Requiring the same mobile
attestation path for desktop would either block it entirely or force weakening attestation
everywhere, so ADR-011 uses a pairing code instead, the same shape as OAuth's device
authorization grant (RFC 8628):

1. `start_pairing()` -- the daemon calls this, unauthenticated, and gets back a short
   human-readable `user_code` to show the person, plus a `pairing_id` it keeps to itself and
   polls with. Splitting these two matters: a short code guessable in reasonable attempts is fine
   to show on a screen because *approving* it needs a separate, already-authenticated session --
   nothing about knowing the code lets an attacker mint a token themselves.
2. `approve_pairing()` -- called from an endpoint that requires an existing, valid access token
   (mobile or web console -- whichever the person is already signed into). Binds the pairing
   session to that identity and mints a `device_id`.
3. `poll_pairing()` -- the daemon calls this, unauthenticated, until it sees `"approved"`. The
   pairing record is consumed on the first successful poll after approval (one token issued per
   approval, matching the `user_code`'s single use), not reusable if the daemon happens to poll
   again.

Same Redis-with-in-process-fallback idiom `services/jwt_auth.py` already uses twice (refresh
tokens, revoked devices) -- consistent, and correct for the same reason: this data is short-lived
and losing it on a restart just means the pairing attempt has to be retried, not a security hole.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from infrastructure.redis_client import get_redis_client

logger = logging.getLogger("aura_backend.device_pairing")

PAIRING_TTL = timedelta(minutes=10)
#: How often the daemon should poll while waiting for approval. Advisory -- the server does not
#: enforce it, but a well-behaved client honours it rather than hammering the endpoint.
POLL_INTERVAL_S = 4

_PAIRING_PREFIX = "device_pairing:"
_PAIRING_CODE_PREFIX = "device_pairing_code:"

# Characters chosen to avoid visual ambiguity when read off a terminal and typed into a phone:
# no 0/O, 1/I/L.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_local_pairings: Dict[str, Dict[str, Any]] = {}
_local_pairing_codes: Dict[str, str] = {}
_warned_no_redis = False


def _warn_once() -> None:
    global _warned_no_redis
    if not _warned_no_redis:
        logger.warning(
            "Redis unavailable: device pairing sessions are stored in-process only "
            "(lost on restart, not shared across workers -- a pairing started against one "
            "worker will not be found by a poll or approve landing on another)."
        )
        _warned_no_redis = True


def _pairing_set(pairing_id: str, record: Dict[str, Any], ttl: timedelta = PAIRING_TTL) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(f"{_PAIRING_PREFIX}{pairing_id}", int(ttl.total_seconds()), json.dumps(record))
        return
    _warn_once()
    _local_pairings[pairing_id] = {**record, "_expires_at": datetime.now(timezone.utc) + ttl}


def _pairing_get(pairing_id: str) -> Optional[Dict[str, Any]]:
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.get(f"{_PAIRING_PREFIX}{pairing_id}")
        return json.loads(raw) if raw else None
    entry = _local_pairings.get(pairing_id)
    if entry is None:
        return None
    if entry["_expires_at"] < datetime.now(timezone.utc):
        del _local_pairings[pairing_id]
        return None
    return {k: v for k, v in entry.items() if k != "_expires_at"}


def _pairing_delete(pairing_id: str) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.delete(f"{_PAIRING_PREFIX}{pairing_id}")
    _local_pairings.pop(pairing_id, None)


def _code_set(user_code: str, pairing_id: str, ttl: timedelta = PAIRING_TTL) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(f"{_PAIRING_CODE_PREFIX}{user_code}", int(ttl.total_seconds()), pairing_id)
        return
    _local_pairing_codes[user_code] = pairing_id


def _code_get(user_code: str) -> Optional[str]:
    redis_client = get_redis_client()
    if redis_client is not None:
        raw = redis_client.get(f"{_PAIRING_CODE_PREFIX}{user_code}")
        return raw if raw else None
    return _local_pairing_codes.get(user_code)


def _code_delete(user_code: str) -> None:
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.delete(f"{_PAIRING_CODE_PREFIX}{user_code}")
    _local_pairing_codes.pop(user_code, None)


def _generate_user_code() -> str:
    chars = [secrets.choice(_USER_CODE_ALPHABET) for _ in range(8)]
    return f"{''.join(chars[:4])}-{''.join(chars[4:])}"


def start_pairing() -> Dict[str, Any]:
    """Begins a pairing session. Unauthenticated by design -- like `/auth/nonce`, this is the raw
    material a real approval later gets bound to, requested before the daemon has any identity at
    all."""
    pairing_id = secrets.token_urlsafe(32)
    user_code = _generate_user_code()
    _pairing_set(pairing_id, {"status": "pending", "user_code": user_code})
    _code_set(user_code, pairing_id)
    return {
        "pairing_id": pairing_id,
        "user_code": user_code,
        "expires_in": int(PAIRING_TTL.total_seconds()),
        "interval": POLL_INTERVAL_S,
    }


def approve_pairing(user_code: str, user_info: dict) -> Optional[str]:
    """Binds a pending pairing session to `user_info` (the caller's own verified identity --
    see api/v2/device_pairing.py, which requires a valid access token to reach this).

    Returns the newly-minted `device_id` on success, or `None` if the code is unknown, expired, or
    was already approved -- callers should not distinguish those to the caller of the *approve*
    endpoint beyond "invalid or expired code", the same reasoning `refresh_token_pair` already
    uses for not revealing which specific way a token was bad.
    """
    normalized = user_code.strip().upper()
    pairing_id = _code_get(normalized)
    if pairing_id is None:
        return None
    record = _pairing_get(pairing_id)
    if record is None or record.get("status") != "pending":
        return None
    device_id = secrets.token_urlsafe(16)
    record["status"] = "approved"
    record["user_info"] = user_info
    record["device_id"] = device_id
    _pairing_set(pairing_id, record)
    # The code has done its job -- prevent a second approval of the same code even if the
    # pairing_id record itself is somehow still readable (defence in depth, not load-bearing:
    # approve_pairing's own status check above already refuses a non-"pending" record).
    _code_delete(normalized)
    return device_id


def poll_pairing(pairing_id: str) -> Dict[str, Any]:
    """The daemon's side of the exchange. Returns one of:

    - ``{"status": "pending"}`` -- keep polling, no closer than `POLL_INTERVAL_S` apart.
    - ``{"status": "approved", "user_info": {...}, "device_id": "..."}`` -- exactly once, the
      first poll after approval; the record is consumed immediately after this call returns, so a
      second poll (a retried request after a dropped response, say) sees ``"expired"`` rather than
      re-approving. The caller (api/v2/device_pairing.py) is responsible for turning this into an
      actual token *before* returning to the daemon -- if that mints the token and the response is
      then lost in transit, the daemon has no way to recover it and must re-pair, the same
      trade-off a one-time password reset link already makes.
    - ``{"status": "expired"}`` -- the pairing_id is unknown, timed out, or already consumed.
    """
    record = _pairing_get(pairing_id)
    if record is None:
        return {"status": "expired"}
    if record.get("status") == "pending":
        return {"status": "pending"}
    if record.get("status") == "approved":
        _pairing_delete(pairing_id)
        return {
            "status": "approved",
            "user_info": record.get("user_info") or {},
            "device_id": record.get("device_id"),
        }
    return {"status": "expired"}
