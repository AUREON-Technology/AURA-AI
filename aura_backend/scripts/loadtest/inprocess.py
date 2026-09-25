"""The harness's own target for self-validation: the real v3 app on the in-memory container.

Used by the default-suite smoke test and ``run --in-process``. It is the real ``build_app`` with
the real kernel, retrieval, safety pipeline and planner, on the in-memory adapters and the echo
provider -- so it proves the harness drives the real routes and parses the real frames, and
proves nothing about capacity. Driven over ``httpx.ASGITransport`` with
``raise_app_exceptions=False`` so a server exception becomes a 500 sample, as it would over a
socket, rather than an exception inside the load generator.

Auth mirrors the gateway's shape without its legacy dependency: bearer tokens are HS256 JWTs
minted by :class:`~.auth.TokenSource` and verified here with the same secret, so the mint path
is exercised end to end. Imports ``aura_core`` lazily -- the rest of the package never does.
"""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from .auth import TokenSource


@dataclass
class InProcessTarget:
    transport: Any
    tokens: TokenSource
    container: Any
    base_url: str = "http://aura-inprocess"
    metrics_path: str = "/metrics"


def _authenticator(secret: str):
    import jwt

    from aura_core.api import Principal
    from aura_core.domain.errors import AuthenticationError
    from aura_core.domain.identifiers import UserId

    async def authenticate(request: Any) -> Any:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise AuthenticationError("a bearer token is required")
        try:
            claims = jwt.decode(header[7:].strip(), secret, algorithms=["HS256"])
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("invalid or expired credentials") from exc
        return Principal(
            user_id=UserId(str(claims["sub"])),
            email=claims.get("email") or "",
            role=claims.get("role") or "User",
        )

    return authenticate


@asynccontextmanager
async def inprocess_target(container: Any = None) -> AsyncIterator[InProcessTarget]:
    import httpx

    from aura_core.api import build_app

    if container is None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        container = Container(load_settings(refresh=True, validate=False))
    secret = secrets.token_urlsafe(32)
    app = build_app(container, authenticator=_authenticator(secret))
    # ASGITransport sends no lifespan events, so start the container the way the app's own
    # startup hook would.
    await container.startup()
    try:
        yield InProcessTarget(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            tokens=TokenSource.minted(secret=secret, role="User"),
            container=container,
            metrics_path=container.settings.observability.metrics_path,
        )
    finally:
        await container.shutdown()
