"""INT-104 on the voice socket: a recheck that could not run is a server error, not a revocation.

The live-voice socket read ``LiveCredential.still_valid`` -- ``False`` for any exception -- so a
database or Redis blip at its periodic recheck closed it 4401, which the app reads as "signed
out". It now follows the host-agent socket (stream B3): 4401 only when the token is refused,
1011 when the check itself failed, so the client reconnects instead.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
from aura_core.api import Principal, build_app, ws_auth
from aura_core.domain.identifiers import UserId
from aura_core.voice import LiveVoiceConfig


class _Flaky:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.failing = False

    async def __call__(self, request):
        if self.failing:
            raise self.error
        return Principal(user_id=UserId("speaker-1"))


@pytest.fixture
def fast_recheck(monkeypatch):
    monkeypatch.setattr(ws_auth, "DEFAULT_RECHECK_S", 0.05)


async def _close_code_after_failure(container, error: BaseException) -> int:
    from fastapi.testclient import TestClient

    container.live_voice = {
        "enabled": True, "config": LiveVoiceConfig(), "stt": InMemoryStt(default="x"),
        "tts": InMemoryTts(), "nlp": container.nlp, "max_sessions": 2,
        "kernel": container.kernel, "cache": container.cache_backend,
    }
    auth = _Flaky(error)
    await container.startup()
    with TestClient(build_app(container, authenticator=auth)) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/api/v3/voice/live") as ws:
                ws.send_json({"type": "auth", "token": "t"})
                ws.send_json({"type": "start", "session_id": "s-1", "language": "en"})
                assert ws.receive_json()["type"] == "ready"
                auth.failing = True
                while True:
                    ws.receive_json()
    return closed.value.code


async def test_a_server_failure_at_recheck_closes_1011(container, fast_recheck) -> None:
    assert await _close_code_after_failure(container, RuntimeError("db down")) == 1011


async def test_an_unreadable_revocation_at_recheck_closes_1011(container, fast_recheck) -> None:
    error = HTTPException(status_code=503, detail={"error": "auth_state_unavailable"})
    assert await _close_code_after_failure(container, error) == 1011


async def test_a_revocation_at_recheck_is_still_4401(container, fast_recheck) -> None:
    assert await _close_code_after_failure(container, HTTPException(status_code=401, detail="x")) == 4401


# --- the same rule for a `reauth` frame (audit AUD-05) -----------------------------------------
# The JWT layer refuses an expired, revoked or deleted-account token with an HTTP 401, not an
# AuthenticationError. `reauth` caught only the latter, so that refusal fell through to the
# catch-all: a stack trace, an `internal_error` frame and a normal close the app reads as
# "disconnected" instead of "session expired".


async def _close_code_after_reauth_failure(container, error: BaseException) -> int:
    from fastapi.testclient import TestClient

    container.live_voice = {
        "enabled": True, "config": LiveVoiceConfig(), "stt": InMemoryStt(default="x"),
        "tts": InMemoryTts(), "nlp": container.nlp, "max_sessions": 2,
        "kernel": container.kernel, "cache": container.cache_backend,
    }
    auth = _Flaky(error)
    await container.startup()
    with TestClient(build_app(container, authenticator=auth)) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/api/v3/voice/live") as ws:
                ws.send_json({"type": "auth", "token": "t"})
                ws.send_json({"type": "start", "session_id": "s-1", "language": "en"})
                assert ws.receive_json()["type"] == "ready"
                auth.failing = True
                ws.send_json({"type": "reauth", "token": "t2"})
                while True:
                    ws.receive_json()
    return closed.value.code


async def test_a_refused_token_at_reauth_closes_4401(container) -> None:
    error = HTTPException(status_code=401, detail="Device access has been revoked.")
    assert await _close_code_after_reauth_failure(container, error) == 4401


async def test_an_unreadable_revocation_at_reauth_closes_1011(container) -> None:
    error = HTTPException(status_code=503, detail={"error": "auth_state_unavailable"})
    assert await _close_code_after_reauth_failure(container, error) == 1011


async def test_a_server_failure_at_reauth_closes_1011(container) -> None:
    assert await _close_code_after_reauth_failure(container, RuntimeError("db down")) == 1011
