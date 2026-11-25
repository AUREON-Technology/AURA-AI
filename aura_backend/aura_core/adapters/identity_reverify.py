"""``IdentityReverifier`` adapters for the control center's step-up (ADR-037).

``GoogleReverifier`` checks a Google ID token from a deliberate re-sign-in: signature, issuer,
expiry and one of our audiences, through ``google-auth`` (already locked; imported lazily).

Freshness comes from ``auth_time`` when the issuer reports it, else from ``iat``. Google never
reports ``auth_time``: its discovery document leaves it out of ``claims_supported`` and offers no
``claims`` parameter to ask for it, so every real Google re-sign-in takes the ``iat`` path. That is
sound for step-up because the token must also carry the one-time nonce the gateway issued for this
challenge (``StepUpService.redeem``): a token cannot be older than its challenge, so ``iat`` within
``max_age`` means "issued for this request, now". What the server cannot prove is that Google
asked for the password again -- the console requests ``prompt=login``, but nothing in the token
says it was honoured. A stolen console session still cannot buy a grant: it needs the person's
live Google account (ADR-037).

``StaticReverifier`` returns claims registered for given tokens. It is the substrate for the
default test suite and for a local gateway run with mock sign-in (``allow_mock_auth``, refused in
production by ``AuraSettings.validate``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Iterable, Mapping

from ..domain.errors import AuthenticationError
from ..ports.control_plane import ReauthClaims

__all__ = ["GoogleReverifier", "StaticReverifier"]


def _authenticated_at(claims: Mapping[str, object]) -> datetime | None:
    """``auth_time`` if the issuer sent one, else ``iat`` (all Google ever sends; see above)."""
    for key in ("auth_time", "iat"):
        value = claims.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None


class GoogleReverifier:
    def __init__(self, *, client_ids: Iterable[str]) -> None:
        self._client_ids = frozenset(c for c in client_ids if c)

    async def verify(self, id_token: str) -> ReauthClaims:
        if not self._client_ids:
            raise AuthenticationError("no console Google client is configured for re-sign-in")
        claims = await asyncio.to_thread(self._verify_sync, id_token)
        if claims.get("aud") not in self._client_ids:
            raise AuthenticationError("the re-sign-in token was issued for another application")
        if claims.get("email_verified") is not True:
            raise AuthenticationError("the re-sign-in account's email is not verified")
        when = _authenticated_at(claims)
        if when is None:
            raise AuthenticationError("the re-sign-in did not report when you authenticated")
        return ReauthClaims(
            subject=str(claims.get("sub") or ""),
            email=str(claims.get("email") or ""),
            auth_time=when,
            nonce=str(claims.get("nonce") or ""),
        )

    @staticmethod
    def _verify_sync(id_token: str) -> Mapping[str, object]:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        try:
            # Audience is checked against our set just after: one call cannot take several.
            return google_id_token.verify_oauth2_token(id_token, google_requests.Request())
        except Exception as exc:
            raise AuthenticationError("the re-sign-in token could not be verified") from exc


class StaticReverifier:
    """Claims registered per token. Unknown tokens are refused like a bad signature."""

    def __init__(self, tokens: Mapping[str, ReauthClaims] | None = None) -> None:
        self._tokens: dict[str, ReauthClaims] = dict(tokens or {})

    def register(self, token: str, claims: ReauthClaims) -> None:
        self._tokens[token] = claims

    async def verify(self, id_token: str) -> ReauthClaims:
        claims = self._tokens.get(id_token)
        if claims is None:
            raise AuthenticationError("the re-sign-in token could not be verified")
        return claims
