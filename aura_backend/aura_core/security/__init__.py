"""Security: cost-weighted rate limiting and proxy-aware client resolution.

Guards, PII handling, and output validation live in :mod:`aura_core.safety`; audit sinks are
adapters behind :class:`aura_core.ports.runtime.AuditSink`.

The gateway's live rate limiter is :class:`SlidingWindowRateLimiter` (``rate_limit.py``). The
``policy``/``authenticator``/``rate_limiter``/``secret_manager`` modules are standalone helpers
re-exported for callers that import them from here; nothing in the request path uses them yet.
"""

from .authenticator import AuthenticationError, Authenticator, Principal
from .policy import SecurityPolicy
from .rate_limit import (
    ROUTE_COSTS,
    RateLimitPolicy,
    SlidingWindowRateLimiter,
    extract_client_ip,
)
from .rate_limiter import RateLimiter, RateLimitExceeded, RateLimitManager
from .secret_manager import SecretManager, SecurityViolation, SecurityViolationType

__all__ = [
    "SlidingWindowRateLimiter",
    "RateLimitPolicy",
    "ROUTE_COSTS",
    "extract_client_ip",
    "SecurityPolicy",
    "Authenticator",
    "Principal",
    "AuthenticationError",
    "RateLimiter",
    "RateLimitManager",
    "RateLimitExceeded",
    "SecretManager",
    "SecurityViolation",
    "SecurityViolationType",
]
