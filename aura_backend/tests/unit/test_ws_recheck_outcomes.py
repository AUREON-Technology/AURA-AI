"""INT-104 (stream B3): an open socket's credential recheck tells "revoked" from "could not check".

``LiveCredential.still_valid`` answered ``False`` for any exception, so a database or Redis blip
during the host-agent socket's periodic recheck closed it with 4401 -- which the daemon reads as
"your pairing is dead" and stops for good, so one blip unpaired the person's computer. The first
authentication already told the two apart (4401 for a rejection, 1011 for a server failure); the
recheck now does too, through ``LiveCredential.recheck``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from aura_core.api import Principal, build_app, ws_auth
from aura_core.api.ws_auth import LiveCredential, Recheck
from aura_core.domain.errors import AuthenticationError, AuthorizationError
from aura_core.domain.identifiers import UserId


def _principal(uid: str = "engineer-1") -> Principal:
    return Principal(user_id=UserId(uid), capabilities=("engineer_agent",))


def _credential(check) -> LiveCredential:
    return LiveCredential(token="t", principal=_principal(), check=check, recheck_s=0.0)


def _raising(error: BaseException):
    async def check(_token):
        raise error

    return check


async def _ok(_token):
    return _principal()


class TestRecheckOutcomes:
    @pytest.mark.parametrize("error", [
        AuthenticationError("expired"),
        AuthorizationError("not a member"),
        HTTPException(status_code=401, detail="expired"),
        HTTPException(status_code=403, detail="revoked"),
    ], ids=["authentication", "authorization", "http-401", "http-403"])
    async def test_a_rejection_is_revoked(self, error) -> None:
        assert await _credential(_raising(error)).recheck(lambda _p: True) is Recheck.REVOKED

    @pytest.mark.parametrize("error", [
        RuntimeError("db down"),
        ConnectionError("redis down"),
        HTTPException(status_code=503, detail={"error": "auth_state_unavailable"}),
    ], ids=["runtime", "connection", "http-503"])
    async def test_a_server_failure_is_unavailable_not_revoked(self, error) -> None:
        assert await _credential(_raising(error)).recheck(lambda _p: True) is Recheck.UNAVAILABLE

    async def test_a_failing_acceptance_check_is_unavailable(self) -> None:
        def accept(_principal):
            raise RuntimeError("plan store down")

        assert await _credential(_ok).recheck(accept) is Recheck.UNAVAILABLE

    async def test_a_refused_principal_is_revoked(self) -> None:
        assert await _credential(_ok).recheck(lambda _p: False) is Recheck.REVOKED

    async def test_another_user_is_revoked(self) -> None:
        async def someone_else(_token):
            return _principal("other")

        assert await _credential(someone_else).recheck(lambda _p: True) is Recheck.REVOKED

    async def test_a_good_token_is_valid_and_resets_the_clock(self) -> None:
        now = [0.0]
        credential = LiveCredential(token="t", principal=_principal(), check=_ok, recheck_s=10.0,
                                    clock=lambda: now[0])
        now[0] = 11.0
        assert await credential.recheck(lambda _p: True) is Recheck.VALID
        assert credential.seconds_until_due() == 10.0

    async def test_an_unavailable_check_keeps_the_last_good_principal_and_is_retried(self) -> None:
        now = [0.0]
        principal = _principal()
        credential = LiveCredential(token="t", principal=principal, check=_raising(RuntimeError("x")),
                                    recheck_s=10.0, clock=lambda: now[0])
        now[0] = 11.0
        assert await credential.recheck(lambda _p: True) is Recheck.UNAVAILABLE
        assert credential.principal is principal
        assert credential.seconds_until_due() == 0.0, "still due: nothing was confirmed"

    async def test_still_valid_keeps_its_boolean_contract(self) -> None:
        """Other sockets (voice live) still read a bool: anything but VALID is False."""
        assert await _credential(_ok).still_valid(lambda _p: True) is True
        assert await _credential(_raising(RuntimeError("x"))).still_valid(lambda _p: True) is False
        assert await _credential(_raising(AuthenticationError("x"))).still_valid(lambda _p: True) is False


class _Flaky:
    """Authenticates ``engineer-1`` until told to fail, then raises ``error`` on every check."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.failing = False

    async def __call__(self, request):
        if self.failing:
            raise self.error
        token = request.headers.get("authorization", "")[7:]
        return Principal(user_id=UserId(token), capabilities=("engineer_agent",))


@pytest.fixture
def fast_recheck(monkeypatch):
    monkeypatch.setattr(ws_auth, "DEFAULT_RECHECK_S", 0.05)


async def _close_code_after_failure(container, error: BaseException) -> int:
    from fastapi.testclient import TestClient

    auth = _Flaky(error)
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
                auth.failing = True
                ws.receive_json()
    return closed.value.code


class TestHostAgentRecheck:
    async def test_a_server_failure_at_recheck_closes_1011_so_the_daemon_retries(
        self, container, fast_recheck,
    ) -> None:
        assert await _close_code_after_failure(container, RuntimeError("db down")) == 1011

    async def test_an_unreadable_revocation_at_recheck_closes_1011(self, container, fast_recheck) -> None:
        error = HTTPException(status_code=503, detail={"error": "auth_state_unavailable"})
        assert await _close_code_after_failure(container, error) == 1011

    async def test_a_revocation_at_recheck_is_still_4401(self, container, fast_recheck) -> None:
        assert await _close_code_after_failure(container, HTTPException(status_code=401, detail="x")) == 4401
