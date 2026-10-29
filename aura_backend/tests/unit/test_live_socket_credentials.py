"""Long-lived sockets are held to the same credential rules as HTTP requests (F-09).

A WebSocket authenticated once and could then stay open indefinitely: an expired token, a revoked
device or a lost role never reached it. And a paired daemon's device token was a full 30-day
session for the whole API. Now each socket re-checks its token through the authenticator on a
schedule, and pairing-issued tokens are scoped to the host-agent socket.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.websockets import WebSocketDisconnect

import services.jwt_auth as jwt_auth
from aura_core.api import Principal, build_app, ws_auth
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId


class _Revocable:
    """A stub authenticator whose tokens can be revoked mid-session."""

    def __init__(self) -> None:
        self.revoked: set[str] = set()

    async def __call__(self, request):
        token = request.headers.get("authorization", "")[7:]
        if not token or token in self.revoked:
            raise AuthenticationError("revoked")
        return Principal(user_id=UserId(token), capabilities=("engineer_agent",))


@pytest.fixture
def fast_recheck(monkeypatch):
    monkeypatch.setattr(ws_auth, "DEFAULT_RECHECK_S", 0.05)


class TestHostAgentSocket:
    async def test_a_revoked_credential_closes_an_open_socket(self, container, fast_recheck) -> None:
        from fastapi.testclient import TestClient

        auth = _Revocable()
        await container.startup()
        registry = container.host_agent_registry
        with TestClient(build_app(container, authenticator=auth)) as client:
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect("/api/v3/host/agent") as ws:
                    ws.send_json({"type": "auth", "token": "engineer-1"})
                    for _ in range(100):
                        if await registry.resolve(UserId("engineer-1")) is not None:
                            break
                        await asyncio.sleep(0.01)
                    assert await registry.resolve(UserId("engineer-1")) is not None

                    auth.revoked.add("engineer-1")
                    ws.receive_json()
            assert closed.value.code == 4401
        assert await registry.resolve(UserId("engineer-1")) is None

    async def test_a_reauth_renews_the_credential_that_is_rechecked(self, container, fast_recheck) -> None:
        from fastapi.testclient import TestClient

        auth = _Revocable()
        await container.startup()
        registry = container.host_agent_registry
        with TestClient(build_app(container, authenticator=auth)) as client:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "engineer-1"})
                ws.send_json({"type": "reauth", "token": "engineer-1"})
                await asyncio.sleep(0.3)
                assert await registry.resolve(UserId("engineer-1")) is not None


class TestDeviceTokenScope:
    def _decode(self, token: str, request) -> dict:
        return asyncio.run(jwt_auth.verify_access_token(
            request, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        ))

    @pytest.fixture(autouse=True)
    def _no_redis(self, monkeypatch):
        monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)

    def test_a_pairing_token_is_refused_on_http_routes(self) -> None:
        token = jwt_auth.issue_device_token(
            {"uid": "u1", "email": "u@example.com"}, device_id="d1",
            scope=jwt_auth.HOST_AGENT_SCOPE,
        )["access_token"]

        class _HttpRequest:
            headers: dict = {}

        with pytest.raises(HTTPException) as refused:
            self._decode(token, _HttpRequest())
        assert refused.value.status_code == 401

    def test_a_pairing_token_is_accepted_by_the_host_agent_socket(self) -> None:
        from aura_core.api.host_agent import _HeaderCarrier

        token = jwt_auth.issue_device_token(
            {"uid": "u2", "email": "u@example.com"}, device_id="d2",
            scope=jwt_auth.HOST_AGENT_SCOPE,
        )["access_token"]

        claims = self._decode(token, _HeaderCarrier({"authorization": f"Bearer {token}"}))
        assert claims["uid"] == "u2"

    def test_the_desktop_apps_own_token_is_not_scoped(self) -> None:
        token = jwt_auth.issue_device_token(
            {"uid": "u3", "email": "u@example.com"}, device_id="d3"
        )["access_token"]

        class _HttpRequest:
            headers: dict = {}

        assert self._decode(token, _HttpRequest())["uid"] == "u3"
