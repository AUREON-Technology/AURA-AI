"""Server-side half of the desktop app's Google sign-in (ADR-011 Unit 7).

The desktop app runs a loopback OAuth 2.0 + PKCE flow against Google
(``aura_client/lib/core/auth/desktop_oauth_client_io.dart``). The browser step ends with a
one-time authorization ``code``; turning it into an ID token needs a call to Google's token
endpoint, and Google answers that call for our registered "Desktop app" client with
``invalid_request: client_secret is missing`` unless the client's secret is sent too (confirmed
live 2026-11-17). A secret compiled into the app is a secret every user's disk holds -- one was
already leaked and rotated that way -- so the exchange happens here, where the secret lives in the
environment (``settings.google_desktop_client_secret``).

What this module hands back is *only* the ID token. Google's response also carries an access
token and, for some clients, a refresh token; neither is returned or stored, because nothing in
AURA acts on the user's Google account. The ID token then goes through the unchanged
``POST /api/v3/auth/desktop/login`` (``services.security.verify_identity_token``), so audience
checks, the operator gate and device-bound session issuance are exactly what they were.

Nothing here logs the code, the PKCE verifier, the secret or Google's raw response body: the code
and verifier are single-use credentials in flight, and Google's body can echo request values.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from config import settings

logger = logging.getLogger("aura.auth.google_oauth")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

#: The only redirect URIs the desktop app can use: RFC 8252 section 7.3's loopback-IP form on the
#: ephemeral port it bound. Anything else is refused before Google is called, so this route is
#: not a general-purpose "exchange any code for anyone's client" relay.
_LOOPBACK_REDIRECT = re.compile(r"^http://127\.0\.0\.1:(\d{1,5})/?$")

_TIMEOUT_S = 20.0


class GoogleExchangeError(Exception):
    """A failed exchange, already translated into what the caller should say.

    ``status_code`` is the HTTP status the route should answer with; ``detail`` is safe to show a
    user and contains no credential, request value or raw provider body.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def is_loopback_redirect(redirect_uri: str) -> bool:
    match = _LOOPBACK_REDIRECT.match(redirect_uri)
    return bool(match) and 0 < int(match.group(1)) <= 65535


async def _post_form(data: Mapping[str, str]) -> tuple[int, Mapping[str, Any]]:
    """POST to Google's token endpoint; the one function tests replace. Returns the status and
    the decoded JSON object (``{}`` when the body was not one)."""
    import httpx

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL, data=data, headers={"Accept": "application/json"}
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload if isinstance(payload, dict) else {}


async def exchange_desktop_code(*, code: str, code_verifier: str, redirect_uri: str) -> str:
    """Exchange a desktop sign-in ``code`` for Google's ID token.

    Raises :class:`GoogleExchangeError` for every failure; returns the raw ID token (a signed
    JWT, not yet verified -- verification is ``/desktop/login``'s job) on success.
    """
    if not is_loopback_redirect(redirect_uri):
        raise GoogleExchangeError(400, "redirect_uri must be the app's loopback address.")

    client_id = settings.google_desktop_client_id
    if not client_id:
        raise GoogleExchangeError(503, "Desktop sign-in is not configured on this server.")

    secret = settings.google_desktop_client_secret.get_secret_value()
    form = {
        "client_id": client_id,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    # Sent only when configured, and omitted rather than blank when not: Google treats
    # `client_secret=` (present but empty) as a wrong secret, not an absent one.
    if secret:
        form["client_secret"] = secret

    try:
        status, payload = await _post_form(form)
    except Exception as exc:  # noqa: BLE001 -- any transport failure is "Google unreachable"
        # The exception class only: httpx messages can include the request URL and, on some
        # versions, form fragments.
        logger.warning("desktop code exchange could not reach Google: %s", type(exc).__name__)
        raise GoogleExchangeError(502, "Could not reach Google to complete sign-in.") from exc

    if status == 200:
        id_token = payload.get("id_token")
        if isinstance(id_token, str) and id_token:
            return id_token
        logger.warning("desktop code exchange: Google answered 200 with no id_token")
        raise GoogleExchangeError(502, "Google did not return an identity token.")

    error = str(payload.get("error") or "")
    description = str(payload.get("error_description") or "")
    logger.warning("desktop code exchange refused by Google: HTTP %s %s", status, error[:60])

    if "client_secret is missing" in description and not secret:
        raise GoogleExchangeError(
            503,
            "Desktop sign-in is not configured on this server: Google requires a client secret "
            "and GOOGLE_DESKTOP_CLIENT_SECRET is not set.",
        )
    if error in {"invalid_client", "unauthorized_client"} or "client_secret" in description:
        # The server's own credentials were refused -- the operator's problem, not the user's.
        raise GoogleExchangeError(
            503,
            "Desktop sign-in is misconfigured on this server: Google rejected its client "
            "credentials. Check GOOGLE_DESKTOP_CLIENT_ID and GOOGLE_DESKTOP_CLIENT_SECRET.",
        )
    if error in {"invalid_grant", "invalid_request", "redirect_uri_mismatch"}:
        raise GoogleExchangeError(
            400, f"Google rejected the sign-in code ({error}). Please sign in again."
        )
    raise GoogleExchangeError(502, "Google could not complete the sign-in.")
