"""``IdentityReverifier`` adapters for the control center's step-up (ADR-037).

``GoogleReverifier`` checks a Google ID token from a deliberate re-sign-in: signature, issuer,
expiry and one of our audiences, through ``google-auth`` (already locked; imported lazily). The
console requests it with ``prompt=login`` and ``max_age``, so Google re-authenticates the person
and includes ``auth_time``: the moment they actually proved who they are, which is what step-up
checks -- not ``iat``, which a silent token refresh also moves.

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
        auth_time = claims.get("auth_time")
        if not isinstance(auth_time, (int, float)):
            # Without max_age Google omits auth_time; refuse rather than fall back to iat.
            raise AuthenticationError("the re-sign-in did not report when you authenticated")
        return ReauthClaims(
            subject=str(claims.get("sub") or ""),
            email=str(claims.get("email") or ""),
            auth_time=datetime.fromtimestamp(float(auth_time), tz=timezone.utc),
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
