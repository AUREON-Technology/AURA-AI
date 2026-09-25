"""WebSocket session identity: a socket authenticated as one user stays that user.

Both long-lived sockets (``/api/v3/voice/live``, ``/api/v3/host/agent``) accept a ``reauth``
frame so a client can present a fresh access token before the old one expires. The frame was
checked for *validity* only: any valid token was accepted, and the session carried on as the
user it was opened for. A socket opened with a stolen, short-lived token could therefore be kept
alive indefinitely by re-authenticating with the attacker's own (valid) account token -- the
server kept acting as the victim. A reauth must present the same subject, or the session ends.

Initial-auth refusal (no/garbage/expired/forged token) is covered in ``test_route_auth.py``
against the production authenticator; here the authenticator is a simple token -> principal map
because what is under test is the socket's handling of identity, not token verification.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId

pytest.importorskip("fastapi")


def _app(container):
    from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
    from aura_core.voice import LiveVoiceConfig

    container.stt_engine = InMemoryStt(default="hello")
    container.tts_engine = InMemoryTts()
    container.live_voice = {
        "enabled": True,
        "config": LiveVoiceConfig(min_speech_ms=40, silence_hangover_ms=40,
                                  english_only_stt=True, hold_tts_until_done=True),
        "stt": container.stt_engine, "tts": container.tts_engine, "nlp": container.nlp,
        "max_sessions": 4, "kernel": container.kernel, "cache": container.cache_backend,
        "safety_guards_enabled": False,
    }

    async def authenticator(request):
        header = request.headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if token not in ("alice", "bob"):
            raise AuthenticationError("unknown")
        return Principal(user_id=UserId(token), capabilities=("engineer_agent",))

    return build_app(container, authenticator=authenticator)


async def _wait_for(predicate, *, attempts: int = 100) -> bool:
    for _ in range(attempts):
        if await predicate():
            return True
        await asyncio.sleep(0.01)
    return False


class TestVoiceLive:
    async def test_reauth_as_a_different_user_ends_the_session(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        with TestClient(_app(container)) as client:
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect("/api/v3/voice/live") as ws:
                    ws.send_json({"type": "auth", "token": "alice"})
                    ws.send_json({"type": "start", "session_id": "s-1", "language": "en"})
                    assert ws.receive_json()["type"] == "ready"
                    ws.receive_json()  # state
                    ws.send_json({"type": "reauth", "token": "bob"})
                    # Had the reauth been accepted, this ends the session normally (1000).
                    ws.send_json({"type": "stop"})
                    while True:
                        ws.receive_json()
            assert closed.value.code == 4401

    async def test_reauth_as_the_same_user_keeps_the_session(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        with TestClient(_app(container)) as client:
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect("/api/v3/voice/live") as ws:
                    ws.send_json({"type": "auth", "token": "alice"})
                    ws.send_json({"type": "start", "session_id": "s-1", "language": "en"})
                    ws.receive_json()
                    ws.receive_json()
                    ws.send_json({"type": "reauth", "token": "alice"})
                    ws.send_json({"type": "stop"})
                    while True:
                        ws.receive_json()
            assert closed.value.code != 4401


class TestHostAgent:
    async def test_reauth_as_a_different_user_unregisters_the_daemon(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        registry = container.host_agent_registry

        async def alice_connected() -> bool:
            return await registry.resolve(UserId("alice")) is not None

        async def alice_gone() -> bool:
            return await registry.resolve(UserId("alice")) is None

        with TestClient(_app(container)) as client:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "alice"})
                assert await _wait_for(alice_connected)
                ws.send_json({"type": "reauth", "token": "bob"})
                assert await _wait_for(alice_gone), (
                    "the daemon stayed registered as alice after re-authenticating as bob"
                )
        assert await registry.resolve(UserId("bob")) is None

    async def test_reauth_as_the_same_user_keeps_the_daemon(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        registry = container.host_agent_registry

        async def alice_connected() -> bool:
            return await registry.resolve(UserId("alice")) is not None

        with TestClient(_app(container)) as client:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "alice"})
                assert await _wait_for(alice_connected)
                ws.send_json({"type": "reauth", "token": "alice"})
                await asyncio.sleep(0.05)
                assert await alice_connected()
