"""WebSocket ``/api/v3/voice/live`` endpoint tests (Phase H)."""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import UserId
from aura_core.voice import LiveVoiceConfig


def _app(container, *, live: bool = True):
    if live:
        container.stt_engine = InMemoryStt(default="hello")
        container.tts_engine = InMemoryTts()
        container.live_voice = {
            "enabled": True,
            "config": LiveVoiceConfig(
                min_speech_ms=40,
                silence_hangover_ms=40,
                english_only_stt=True,
                hold_tts_until_done=True,
            ),
            "stt": container.stt_engine,
            "tts": container.tts_engine,
            "nlp": container.nlp,
            "max_sessions": 2,
            "kernel": container.kernel,
            "cache": container.cache_backend,
            "safety_guards_enabled": False,
        }
    else:
        container.live_voice = None
        container.stt_engine = None
        container.tts_engine = None

    async def authenticator(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            from aura_core.domain.errors import AuthenticationError

            raise AuthenticationError("missing")
        token = header[7:]
        if token == "bad":
            from aura_core.domain.errors import AuthenticationError

            raise AuthenticationError("bad")
        return Principal(user_id=UserId("voicer"), email="v@example.com", role="User")

    return build_app(container, authenticator=authenticator)


@pytest.mark.anyio
async def test_auth_required(container) -> None:
    from fastapi.testclient import TestClient

    await container.startup()
    with TestClient(_app(container)) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v3/voice/live") as ws:
                ws.send_json({"type": "auth", "token": "bad"})
                ws.receive_json()
        assert exc.value.code in (4401, 1008)


@pytest.mark.anyio
async def test_engines_absent_closes_4503(container) -> None:
    from fastapi.testclient import TestClient

    await container.startup()
    with TestClient(_app(container, live=False)) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v3/voice/live") as ws:
                ws.receive_json()
        assert exc.value.code == 4503


@pytest.mark.anyio
async def test_happy_path_ready_frame(container) -> None:
    from fastapi.testclient import TestClient

    await container.startup()
    with TestClient(_app(container)) as client:
        ws = client.websocket_connect("/api/v3/voice/live")
        with ws:
            ws.send_json({"type": "auth", "token": "good"})
            ws.send_json({"type": "start", "session_id": "chat-1", "language": "en"})
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert ready["capture"]["sample_rate"] == 16_000
            assert "playback" in ready
            state = ws.receive_json()
            assert state["type"] == "state"
            ws.send_json({"type": "stop"})


@pytest.mark.anyio
async def test_english_only_rejects_arabic_start(container) -> None:
    from fastapi.testclient import TestClient

    await container.startup()
    with TestClient(_app(container)) as client:
        with client.websocket_connect("/api/v3/voice/live") as ws:
            ws.send_json({"type": "auth", "token": "good"})
            ws.send_json({"type": "start", "session_id": "chat-1", "language": "ar"})
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["error"] == "language_unsupported"


@pytest.mark.anyio
async def test_reauth_frame_accepted(container) -> None:
    from fastapi.testclient import TestClient

    await container.startup()
    with TestClient(_app(container)) as client:
        ws = client.websocket_connect("/api/v3/voice/live")
        with ws:
            ws.send_json({"type": "auth", "token": "good"})
            ws.send_json({"type": "start", "session_id": "chat-1", "language": "en"})
            ws.receive_json()  # ready
            ws.receive_json()  # state
            ws.send_json({"type": "reauth", "token": "good"})
            ws.send_json({"type": "stop"})


@pytest.mark.anyio
async def test_an_oversized_binary_frame_closes_the_socket_4400(container) -> None:
    """Audit FSA-007: the endpoint refuses, with a protocol error, rather than buffering it."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    await container.startup()
    with TestClient(_app(container)) as client:
        ws = client.websocket_connect("/api/v3/voice/live")
        with ws:
            ws.send_json({"type": "auth", "token": "good"})
            ws.send_json({"type": "start", "session_id": "chat-1", "language": "en"})
            assert ws.receive_json()["type"] == "ready"
            assert ws.receive_json()["type"] == "state"
            ws.send_bytes(b"\0" * (LiveVoiceConfig().max_frame_bytes + 2))
            with pytest.raises(WebSocketDisconnect) as closed:
                # Drain until the close arrives; a state frame or two may precede it.
                for _ in range(5):
                    ws.receive()
            assert closed.value.code == 4400
