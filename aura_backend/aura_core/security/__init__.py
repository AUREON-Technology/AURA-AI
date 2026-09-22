"""Security hardening for production deployment.

Implements ADR-012 security requirements: auth, authorization, secrets, encryption,
rate limiting, SSRF protection, prompt injection defenses, tool injection defenses,
sandbox isolation, and audit logs.

All features are opt-in via settings and degrade gracefully when infrastructure
is unavailable (in-memory fallbacks, no external deps at import time).
"""

from .policy import SecurityPolicy
from .authenticator import Authenticator, Principal, AuthenticationError
from .rate_limiter import RateLimiter, RateLimitManager, RateLimitExceeded
from .secret_manager import SecretManager

# Export security violation types for convenience
from .secret_manager import SecurityViolation, SecurityViolationType

__all__ = [
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