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


def _redis_password() -> str | None:
    """Docker mounts the Redis password as a secret file (AURA_REDIS_PASSWORD_FILE) rather
    than a plain env var (SEC-08). Without it, production Redis -- which always runs with
    --requirepass -- rejects every command from this client, and since ping() failing just
    looks like "Redis unavailable" below, that rejection was silent: attestation-nonce replay
    protection and rate limiting were degrading to in-process-only with no visible error."""
    path = os.environ.get("AURA_REDIS_PASSWORD_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return os.environ.get("AURA_REDIS_PASSWORD")


def get_redis_client() -> "redis.Redis | None":
    """Returns a connected client, or None if Redis is unreachable — every
    caller must handle the None case by degrading gracefully (in-process
    fallback), never by crashing. Connection is attempted once per process
    and cached; callers that need to react to Redis recovering mid-process
    should catch exceptions on use and treat them the same as None.

    Prefers a full connection URL (``AURA_REDIS_URL``, matching
    ``aura_core/config/settings.py::InfrastructureSettings.redis_url`` and the working
    :class:`~aura_core.adapters.redis_cache.RedisCache` adapter's own ``redis.from_url`` call) --
    this is what Railway's managed Redis add-on provides (host, port, and password all in one
    string, e.g. ``redis://default:PASSWORD@redis.railway.internal:6379``), and is also accepted
    on the VPS if ever set there. Falls back to the discrete ``REDIS_HOST``/``AURA_REDIS_PASSWORD``
    construction docker-compose.prod.yml sets instead. Without this fallback-first-URL ordering,
    a deploy target that only sets the URL form silently connects to ``localhost:6379`` (refused)
    and every caller here -- device pairing, refresh tokens, nonce replay protection -- degrades to
    an in-process store that isn't shared across workers (confirmed live on Railway: a pairing
    approval landing on a different worker than the one polling for it always saw "expired").
    """
    global _client, _attempted
    if _attempted:
        return _client

    _attempted = True
    try:
        url = os.getenv("AURA_REDIS_URL") or os.getenv("REDIS_URL")
        if url:
            candidate = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=2,
            )
        else:
            host = os.getenv("REDIS_HOST", "localhost")
            password = _redis_password()
            candidate = redis.Redis(
                host=host, port=6379, db=0, password=password,
                decode_responses=True, socket_connect_timeout=2,
            )
        candidate.ping()
        _client = candidate
        logger.info("Connected to Redis.")
    except Exception as e:
        logger.warning(f"Redis unavailable, dependent features will use in-process fallbacks: {e}")
        _client = None

    return _client
