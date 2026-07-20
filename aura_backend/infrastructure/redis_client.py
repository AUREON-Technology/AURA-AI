"""Single lazily-initialized Redis connection, shared by every feature that
needs one (attestation nonce store, distributed rate limiting, refresh
token store). Previously each of those established its own connection
block with the same `REDIS_HOST` env var + ping-and-fall-back-to-None
pattern duplicated three times — consolidated here so there's one place
that owns the connection and one place to change if the pattern needs to
evolve (e.g., connection pooling, TLS).
"""
import logging
import os

import redis

logger = logging.getLogger("aura_backend.redis")

_client: "redis.Redis | None" = None
_attempted = False


def get_redis_client() -> "redis.Redis | None":
    """Returns a connected client, or None if Redis is unreachable — every
    caller must handle the None case by degrading gracefully (in-process
    fallback), never by crashing. Connection is attempted once per process
    and cached; callers that need to react to Redis recovering mid-process
    should catch exceptions on use and treat them the same as None.
    """
    global _client, _attempted
    if _attempted:
        return _client

    _attempted = True
    try:
        host = os.getenv("REDIS_HOST", "localhost")
        candidate = redis.Redis(host=host, port=6379, db=0, decode_responses=True, socket_connect_timeout=2)
        candidate.ping()
        _client = candidate
        logger.info("Connected to Redis.")
    except Exception as e:
        logger.warning(f"Redis unavailable, dependent features will use in-process fallbacks: {e}")
        _client = None

    return _client
