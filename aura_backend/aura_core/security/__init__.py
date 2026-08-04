"""Security: cost-weighted rate limiting and proxy-aware client resolution.

Guards, PII handling, and output validation live in :mod:`aura_core.safety`; audit sinks are
adapters behind :class:`aura_core.ports.runtime.AuditSink`.
"""

from .rate_limit import (
    ROUTE_COSTS,
    RateLimitPolicy,
    SlidingWindowRateLimiter,
    extract_client_ip,
)

__all__ = [
    "SlidingWindowRateLimiter",
    "RateLimitPolicy",
    "ROUTE_COSTS",
    "extract_client_ip",
]
