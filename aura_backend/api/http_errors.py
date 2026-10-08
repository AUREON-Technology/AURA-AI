"""Safe HTTP error mapping for leftover platform routers.

Never put ``str(exc)`` on the wire — log it with a correlation id instead.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException

logger = logging.getLogger("aura_backend.api")

_INTERNAL_ERROR = "internal_error"


def http_internal_error(
    exc: Exception, *, where: str, user_email: str | None = None
) -> HTTPException:
    """Log the real exception with a correlation id; never put ``str(e)`` on the wire."""
    correlation_id = uuid.uuid4().hex
    logger.error(
        "%s failed correlation_id=%s user=%s: %s",
        where,
        correlation_id,
        user_email,
        exc,
        exc_info=True,
    )
    return HTTPException(status_code=500, detail=_INTERNAL_ERROR)
