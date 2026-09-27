"""Response hardening that does not depend on a reverse proxy being in front.

`deploy/nginx/templates/aura.conf.template` sets a full header set and refuses `/metrics` with
`deny all; return 404;`. That is the right place for it *when nginx is in the path* -- which is
true of the Contabo VPS target and false of the Railway Control Plane, where the ASGI app is the
edge and Railway's router adds nothing.

A live probe of the Railway deployment found exactly that gap: no HSTS, no `X-Content-Type-Options`,
no `X-Frame-Options`, no `Referrer-Policy`, no `Permissions-Policy`, and `/metrics` readable
unauthenticated by anyone -- publishing which safety bypasses are enabled
(`aura_insecure_mode`), which dependencies are down, and retrieval quality histograms. The
project had already decided none of that should be public; one deploy target simply had no
component enforcing the decision.

So the floor moves into the application, where every target inherits it. nginx keeps its own
headers and `proxy_hide_header` strips the app's copies (see the template), because two
`X-Frame-Options` values are worse than one -- some browsers discard a duplicated one entirely,
which would have made hardening the app *weaken* the VPS.

CSP is deliberately not sent from here by default. The API serves JSON and SSE, so the edge's
`default-src 'none'` is correct for it, but the same origin also fronts the operator console and
Grafana with far looser policies; a blanket app-level CSP would either break those or have to be
so permissive it means nothing. It stays an explicit opt-in.
"""

from __future__ import annotations

import hmac
from typing import Any

#: Sent on every response. Values match the nginx template so a request that traverses both sees
#: one consistent policy rather than two competing ones.
DEFAULT_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

#: HSTS is conditional: it is meaningless over plaintext and actively harmful to a local
#: http://127.0.0.1 workflow, since a browser that caches it will refuse plain HTTP to that host
#: for two years. Only sent when the request arrived over TLS.
HSTS_HEADER = ("Strict-Transport-Security", "max-age=63072000; includeSubDomains")


def _is_secure(scope: dict[str, Any]) -> bool:
    """Whether the original client request was HTTPS.

    Behind a proxy the ASGI scheme is the *internal* hop, so `X-Forwarded-Proto` is what carries
    the client's. Trusting that header unconditionally would let a client forge it, but the only
    thing it gates here is whether to *add* a header that hardens the response -- a forged value
    can make a response stricter, never weaker.
    """
    if scope.get("scheme") in ("https", "wss"):
        return True
    for name, value in scope.get("headers") or ():
        if name == b"x-forwarded-proto":
            return value.decode("latin-1").split(",")[0].strip() == "https"
    return False


class SecurityHeadersMiddleware:
    """Add the header floor to every response, without overwriting a deliberate one.

    Pure ASGI rather than Starlette's ``BaseHTTPMiddleware``: that wrapper buffers streamed
    responses through a queue, and this app streams chat tokens and SSE where added latency is
    directly visible to the user.
    """

    def __init__(self, app: Any, *, headers: dict[str, str] | None = None, csp: str = "") -> None:
        self.app = app
        self._headers = dict(DEFAULT_HEADERS if headers is None else headers)
        if csp:
            self._headers["Content-Security-Policy"] = csp

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = list(self._headers.items())
        if _is_secure(scope):
            extra.append(HSTS_HEADER)

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                raw = message.setdefault("headers", [])
                present = {name.lower() for name, _ in raw}
                for name, value in extra:
                    # A route that set its own value meant it. Never clobber it.
                    if name.lower().encode("latin-1") not in present:
                        raw.append((name.encode("latin-1"), value.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class MetricsAuthMiddleware:
    """Require a bearer token on the metrics path, when one is configured.

    Deliberately opt-in rather than enforced. The application cannot tell "reachable only on a
    private Docker network" (the VPS, where nginx 404s the public path and Prometheus scrapes
    `gateway:8000/metrics` directly) from "reachable from the internet" (Railway). Making a token
    mandatory would break the correctly-configured deployment to fix the misconfigured one.

    So: set ``AURA_METRICS_TOKEN`` on any deployment where the app is the edge.
    ``AuraSettings.validate()`` warns when a production-like environment leaves it unset.
    """

    def __init__(self, app: Any, *, path: str, token: str) -> None:
        self.app = app
        self._path = path
        self._token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != self._path:
            await self.app(scope, receive, send)
            return

        if not self._authorised(scope):
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            # 404, not 401: a scraping endpoint that announces itself to an unauthenticated
            # caller tells them the stack and that there is something here worth guessing at.
            # nginx returns 404 for the same path, so the two targets behave identically.
            await send({"type": "http.response.body", "body": b'{"detail":"Not Found"}'})
            return

        await self.app(scope, receive, send)

    def _authorised(self, scope: dict[str, Any]) -> bool:
        for name, value in scope.get("headers") or ():
            if name == b"authorization":
                supplied = value.decode("latin-1")
                prefix = "Bearer "
                if not supplied.startswith(prefix):
                    return False
                # Constant-time: a timing oracle on a scrape token is still a token leak.
                return hmac.compare_digest(supplied[len(prefix) :], self._token)
        return False
