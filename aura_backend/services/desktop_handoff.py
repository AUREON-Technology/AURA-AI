"""Desktop sign-in through the browser (module readiness WP12; G-28, FR-20, T-7).

FlutterFire's ``firebase_auth`` has no Linux implementation and no provider sign-in on Windows, so
the desktop app cannot sign in with Firebase itself. It hands off to the browser instead, the same
loopback shape RFC 8252 describes for native apps:

1. The app makes a PKCE verifier, listens on ``http://127.0.0.1:<port>/`` and opens the web app's
   ``/desktop-sign-in`` page with that redirect, a ``state`` and the verifier's S256 challenge.
2. The page signs the person in with Firebase (Google, Apple or email), then calls
   ``POST /api/v3/auth/desktop/handoff`` with the Firebase ID token. The server verifies the token
   and keeps the verified identity under a random one-time ``code`` (:func:`create`), bound to the
   challenge and the redirect. The page sends the browser to the loopback with ``code`` and
   ``state``.
3. The app checks ``state`` and calls ``POST /api/v3/auth/desktop/firebase-exchange`` with the
   code and the verifier. :func:`redeem` hands the identity back once. The route then applies the
   same desktop gate ``/desktop/login`` does and issues a device-bound session.

The ID token never travels in a URL. A code seen by anyone else -- browser history, another local
process on the loopback port -- is useless without the verifier, which never leaves the app. The
code is single-use even when the exchange fails, and lives 60 seconds.

Storage follows ``device_pairing``: Redis when configured, so the worker that redeems a code need
not be the one that issued it, with an in-process fallback. Losing a code on a restart only means
signing in again.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from infrastructure.redis_client import get_redis_client

logger = logging.getLogger("aura_backend.desktop_handoff")

#: How long a code may wait for the app to redeem it. The app is already listening, so this only
#: has to cover one redirect and one request.
HANDOFF_TTL_S = 60
_PREFIX = "desktop_handoff:"

#: In-process fallback: code -> (expires_at monotonic, record).
_local: Dict[str, Tuple[float, Dict[str, Any]]] = {}


class HandoffError(Exception):
    """Why a code could not be redeemed. One message for every case on purpose: a caller learns
    nothing about which part was wrong."""


def s256(verifier: str) -> str:
    """RFC 7636 S256: base64url(SHA-256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def create(user_info: Dict[str, Any], *, code_challenge: str, redirect_uri: str) -> str:
    """Keep a verified identity for one redemption and return the code that redeems it."""
    code = secrets.token_urlsafe(32)
    record = {"user_info": user_info, "code_challenge": code_challenge, "redirect_uri": redirect_uri}
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_client.setex(f"{_PREFIX}{code}", HANDOFF_TTL_S, json.dumps(record, default=str))
        return code
    now = time.monotonic()
    for stale in [k for k, (exp, _) in _local.items() if exp <= now]:
        del _local[stale]
    _local[code] = (now + HANDOFF_TTL_S, record)
    return code


def _take(code: str) -> Optional[Dict[str, Any]]:
    """Read and delete in one step, so two concurrent redemptions cannot both succeed."""
    redis_client = get_redis_client()
    if redis_client is not None:
        pipe = redis_client.pipeline()  # MULTI/EXEC: the GET and DEL run atomically
        pipe.get(f"{_PREFIX}{code}")
        pipe.delete(f"{_PREFIX}{code}")
        raw, _deleted = pipe.execute()
        return json.loads(raw) if raw else None
    entry = _local.pop(code, None)
    if entry is None or entry[0] <= time.monotonic():
        return None
    return entry[1]


def redeem(code: str, *, code_verifier: str, redirect_uri: str) -> Dict[str, Any]:
    """The identity behind ``code``, once. Spent whether or not the verifier matches."""
    record = _take(code)
    if record is None:
        raise HandoffError("expired or already used")
    expected = str(record.get("code_challenge") or "")
    if not hmac.compare_digest(s256(code_verifier), expected):
        raise HandoffError("verifier mismatch")
    if not hmac.compare_digest(str(record.get("redirect_uri") or ""), redirect_uri):
        raise HandoffError("redirect mismatch")
    return dict(record["user_info"])


def _reset() -> None:
    _local.clear()
