"""HTTP adapters.

The gateway is thin by design: authenticate, rate-limit, translate to a TurnRequest, translate
domain errors to status codes. Everything of substance is in the kernel, which is why the same
kernel runs from a Celery worker and a CLI without a web framework.
"""

from .v3 import Principal, build_app, build_router

__all__ = ["build_router", "build_app", "Principal"]
