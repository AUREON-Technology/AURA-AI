"""``AuthStateUnavailable`` is the retryable 503 contract wherever it surfaces, not only on the
sign-in routes that catch it themselves. A device listing or a desktop sign-in that reads device
revocation during a Redis blip used to answer an unstructured 500."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from services.jwt_auth import AUTH_STATE_UNAVAILABLE_DETAIL, AuthStateUnavailable


async def test_an_uncaught_auth_state_failure_is_a_retryable_503() -> None:
    from aura_service import _register_auth_state_handler

    app = FastAPI()
    _register_auth_state_handler(app)

    @app.get("/devices")
    async def devices():
        raise AuthStateUnavailable("device revocation check unavailable (redis error)")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        response = await http.get("/devices")

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_STATE_UNAVAILABLE_DETAIL}
    assert response.headers.get("retry-after")


def test_the_gateway_registers_it() -> None:
    from aura_service import create_app
    from services.jwt_auth import AuthStateUnavailable as Exc

    assert Exc in create_app().exception_handlers
