"""``GET /api/v3/keys/data`` (ADR-032): who may fetch a data key, and how it leaves the server.

Through the real ASGI app, like ``test_api_surface.py``: the properties that matter -- a caller
can only ever get their own key, nothing caches it, services and anonymous callers are refused --
live in the wiring.
"""

from __future__ import annotations

import base64
import dataclasses

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId

pytest.importorskip("fastapi")


def _client(container, *, user: str = "user-a", authenticator=None):
    from fastapi.testclient import TestClient

    async def as_user(request):
        return Principal(user_id=UserId(user), email=f"{user}@example.com", role="User",
                         capabilities=())

    return TestClient(build_app(container, authenticator=authenticator or as_user))


def _fetch(client):
    response = client.get("/api/v3/keys/data")
    return response, response.json()


class TestDataKeyEndpoint:
    def test_returns_the_callers_key_uncached(self, container) -> None:
        with _client(container) as client:
            response, body = _fetch(client)

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["pragma"] == "no-cache"
        assert body["format"] == "gcm2"
        (key,) = body["keys"]
        assert key["kid"] == body["primary"]
        assert len(base64.b64decode(key["key"])) == 32

    async def test_it_is_the_key_the_server_encrypts_that_users_fields_with(self, container) -> None:
        with _client(container) as client:
            _, body = _fetch(client)
        ring = await container.user_data_keys.keyring(UserId("user-a"), create=False)
        assert base64.b64decode(body["keys"][0]["key"]) == ring.primary.key

    def test_the_same_user_gets_the_same_key_and_another_user_never_does(self, container) -> None:
        with _client(container, user="user-a") as a:
            _, first = _fetch(a)
            _, second = _fetch(a)
        with _client(container, user="user-b") as b:
            _, other = _fetch(b)

        assert first["keys"] == second["keys"]
        assert other["keys"][0]["key"] != first["keys"][0]["key"]
        # No request parameter selects an owner: asking for someone else's is not expressible.
        with _client(container, user="user-b") as b:
            response = b.get("/api/v3/keys/data", params={"user_id": "user-a", "owner": "user-a"})
        assert response.json()["keys"] == other["keys"]

    def test_unauthenticated_callers_are_refused(self, container) -> None:
        async def refusing(request):
            raise AuthenticationError("missing bearer token")

        with _client(container, authenticator=refusing) as client:
            response = client.get("/api/v3/keys/data")

        assert response.status_code == 401
        assert "keys" not in response.json()

    def test_service_keys_are_refused_even_when_their_routes_would_admit_it(
        self, container
    ) -> None:
        class _Key:
            key_id, name, rate_per_minute, principal_id = "svc-1", "flame", 60, "svc-owner"

            @staticmethod
            def allows(method, path):
                return True

        async def as_service(request):
            return Principal.for_service(_Key())

        with _client(container, authenticator=as_service) as client:
            response = client.get("/api/v3/keys/data")

        assert response.status_code == 403
        assert "no-store" in response.headers["cache-control"]

    def test_it_has_its_own_rate_limit(self, container) -> None:
        with _client(container, user="greedy") as client:
            statuses = [client.get("/api/v3/keys/data").status_code for _ in range(12)]
        assert statuses[:10] == [200] * 10
        assert statuses[10:] == [429, 429]

    def test_plain_http_is_refused_in_production_like_environments(self, container) -> None:
        from aura_core.config.settings import Environment

        production = dataclasses.replace(container.settings, environment=Environment.PRODUCTION)
        container.settings, original = production, container.settings
        try:
            with _client(container) as client:
                insecure = client.get("/api/v3/keys/data", headers={"x-forwarded-proto": "http"})
                secure = client.get("/api/v3/keys/data", headers={"x-forwarded-proto": "https"})
        finally:
            container.settings = original

        assert insecure.status_code == 403
        assert "tls_required" in insecure.text
        assert secure.status_code == 200
