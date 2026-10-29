"""Short-lived, single-use CSRF state for the GitHub OAuth connect flow
(api/v2/github_integration.py).

Mirrors services/security.py's store_nonce(): GitHub's callback (GET /callback?code=...&state=...)
carries no Authorization header at all -- it is a plain browser redirect from github.com, not a
call the client makes with its JWT -- so the acting user has to be recovered from the ``state``
value /connect handed out, not from a bearer token the callback never receives. Redis-backed
where available (shared across workers, survives a restart mid-flow); an in-process dict
otherwise, same degrade-not-crash shape store_nonce uses.
"""
import json
import logging
import time

from infrastructure.redis_client import get_redis_client

logger = logging.getLogger("aura.github_oauth_state")

_PREFIX = "github_oauth_state:"
_local_store: dict[str, tuple[str, float]] = {}
_warned_no_redis = False


def _purge_expired() -> None:
    now = time.monotonic()
    expired = [key for key, (_, expires_at) in _local_store.items() if expires_at <= now]
    for key in expired:
        _local_store.pop(key, None)


def store_state(state: str, user_id: str, *, ttl_seconds: int = 600) -> None:
    global _warned_no_redis
    client = get_redis_client()
    if client is not None:
        client.setex(f"{_PREFIX}{state}", ttl_seconds, json.dumps({"user_id": user_id}))
        return
    if not _warned_no_redis:
        logger.warning(
            "Redis unavailable: GitHub OAuth state is stored in-process only "
            "(lost on restart, not shared across workers)."
        )
        _warned_no_redis = True
    _purge_expired()
    _local_store[state] = (user_id, time.monotonic() + ttl_seconds)


def peek_state(state: str) -> str | None:
    """The user a live ``state`` belongs to, without spending it. The callback page uses this
    to name the AURA account before the user confirms; only the confirmation consumes it."""
    client = get_redis_client()
    if client is not None:
        raw = client.get(f"{_PREFIX}{state}")
        if raw is None:
            return None
        try:
            return json.loads(raw)["user_id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
    _purge_expired()
    entry = _local_store.get(state)
    return entry[0] if entry is not None else None


def consume_state(state: str) -> str | None:
    """Single-use: a state value is deleted the first time it is read, whether or not it was
    found. A replayed ``state`` (a captured callback URL retried by an attacker) must not
    succeed a second time."""
    client = get_redis_client()
    if client is not None:
        key = f"{_PREFIX}{state}"
        raw = client.get(key)
        if raw is None:
            return None
        client.delete(key)
        try:
            return json.loads(raw)["user_id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
    _purge_expired()
    entry = _local_store.pop(state, None)
    return entry[0] if entry is not None else None
