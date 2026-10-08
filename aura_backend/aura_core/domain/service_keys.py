"""Service keys: non-human credentials scoped to named routes (AR-4, a narrow AP12a).

A *service* -- another system, not a person -- calls AURA with a bearer token of the form
``aurask_<key_id>_<secret>``. The first such caller is Flame in Freefall asking AURA to
interpret an analysis (``POST /api/v3/chat``); the shape is general.

What a key can do is decided here, not by the caller:

* **routes** -- an explicit allowlist drawn from :data:`SERVICE_ROUTES`. Anything else is a 403
  at the gateway, whatever the route's own checks would have said.
* **rate** -- its own per-minute turn budget, independent of any user's.
* **no personal state** -- a service principal has no memory, no session and no tools; the
  gateway forces those off (``api/v3.py``), so a key can never write to anyone's memory.

Only a SHA-256 of the whole token is ever stored. The token is shown once, at issue, and the
store cannot give it back. Stdlib only (ADR-001).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime

from .errors import ValidationError

__all__ = [
    "ServiceKey",
    "SERVICE_ROUTES",
    "DEFAULT_SERVICE_ROUTES",
    "TOKEN_PREFIX",
    "new_token",
    "parse_token",
    "token_digest",
    "digests_match",
    "looks_like_service_token",
]

TOKEN_PREFIX = "aurask_"
#: Every route a service key may ever be granted. Adding one is a deliberate code change.
SERVICE_ROUTES = frozenset({"POST /api/v3/chat", "POST /api/v3/knowledge/documents"})
DEFAULT_SERVICE_ROUTES = frozenset({"POST /api/v3/chat"})

_KEY_ID = re.compile(r"^[0-9a-f]{16}$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
_TOKEN = re.compile(r"^aurask_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_RATE = 600


def new_token() -> tuple[str, str]:
    """A fresh ``(key_id, token)``. The token is returned to the issuer once and never stored."""
    key_id = secrets.token_hex(8)
    return key_id, f"{TOKEN_PREFIX}{key_id}_{secrets.token_urlsafe(32)}"


def looks_like_service_token(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


def parse_token(token: str) -> str | None:
    """The key id a well-formed token names, or None. Says nothing about whether it is valid."""
    match = _TOKEN.fullmatch(token)
    return match.group(1) if match else None


def token_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def digests_match(stored: str, token: str) -> bool:
    """Constant-time comparison of a stored digest with a presented token's."""
    return hmac.compare_digest(stored.encode("ascii"), token_digest(token).encode("ascii"))


@dataclass(frozen=True, slots=True)
class ServiceKey:
    """One issued key. ``digest`` is the only trace of the token; it is never returned by the API."""

    key_id: str
    name: str
    digest: str
    created_at: datetime
    created_by: str
    routes: frozenset[str] = field(default_factory=lambda: DEFAULT_SERVICE_ROUTES)
    rate_per_minute: int = 10
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    def __post_init__(self) -> None:
        if not _KEY_ID.match(self.key_id):
            raise ValidationError("key_id must be 16 lower-case hex characters")
        if not _NAME.match(self.name):
            raise ValidationError(
                "a service name is a lower-case slug of 2-48 characters, starting with a letter",
                details={"name": self.name[:48]},
            )
        if not _DIGEST.match(self.digest):
            raise ValidationError("digest must be sha256:<hex>")
        routes = frozenset(self.routes)
        if not routes:
            raise ValidationError("a service key needs at least one route")
        unknown = sorted(routes - SERVICE_ROUTES)
        if unknown:
            raise ValidationError("routes outside the service allowlist",
                                  details={"routes": unknown, "allowed": sorted(SERVICE_ROUTES)})
        object.__setattr__(self, "routes", routes)
        if isinstance(self.rate_per_minute, bool) or not 1 <= self.rate_per_minute <= _MAX_RATE:
            raise ValidationError(f"rate_per_minute must be 1..{_MAX_RATE}")
        if not self.created_by:
            raise ValidationError("created_by is required")

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def principal_id(self) -> str:
        """The ``UserId`` the key acts as. Namespaced so it can never equal a person's subject."""
        return f"service:{self.name}"

    def allows(self, method: str, path: str) -> bool:
        return f"{method.upper()} {path}" in self.routes
