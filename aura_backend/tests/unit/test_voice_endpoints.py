"""``POST /api/v3/voice/speak`` and ``POST /api/v3/voice/transcribe`` (Phase F2): the HTTP entry
points for the self-hosted speech adapters, driven the same ``container``-backed ``build_app``
way ``test_knowledge_upload_endpoint.py`` drives the OCR upload route.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.espeak_tts import espeak_available
from aura_core.adapters.whisper_stt import faster_whisper_available
from aura_core.domain.identifiers import UserId


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("voicer"), email="v@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


@pytest.mark.skipif(not espeak_available(), reason="espeak-ng binary not on PATH")
class TestSpeakEndpoint:
    async def test_returns_real_wav_audio(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.post("/api/v3/voice/speak", json={"text": "Hello there."})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.content[:4] == b"RIFF"
        assert response.headers["x-engine"] in {
            "espeak-ng",
            "piper-en",
            "piper-ar",
            "piper-en+piper-ar",
        }

    async def test_blank_text_is_rejected_by_request_validation(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.post("/api/v3/voice/speak", json={"text": ""})
        assert response.status_code == 422

    async def test_url_only_text_is_rejected_as_unspeakable(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.post(
                "/api/v3/voice/speak", json={"text": "https://example.com/silent"},
            )
        assert response.status_code == 422


class TestSpeakEndpointNoEngine:
    async def test_503_when_no_tts_engine_is_configured(self, container) -> None:
        await container.startup()
        container.tts_engine = None
        with _client(container) as client:
            response = client.post("/api/v3/voice/speak", json={"text": "Hello there."})
        assert response.status_code == 503
        assert response.json()["error"] == "dependency_unavailable"

    async def test_readiness_reports_whether_tts_is_wired(self, container) -> None:
        await container.startup()
        health = await container.health()
        assert "voice" in health
        assert "tts" in health["voice"]
        assert "stt" in health["voice"]
        container.tts_engine = None
        health = await container.health()
        assert health["voice"]["tts"] is None


@pytest.mark.skipif(
    not (espeak_available() and faster_whisper_available()),
    reason="requires both espeak-ng and faster-whisper for a real round trip",
)
class TestTranscribeEndpoint:
    async def test_transcribes_real_synthesized_audio(self, container) -> None:
        await container.startup()
        audio = await container.tts_engine.synthesize("The meeting starts at noon.")
        with _client(container) as client:
            response = client.post(
                "/api/v3/voice/transcribe",
                files={"file": ("note.wav", audio, "audio/wav")},
            )
        assert response.status_code == 200
        body = response.json()
        assert "meeting" in body["text"].lower()
        assert body["engine"].startswith("faster-whisper-")


class TestTranscribeEndpointNoEngine:
    async def test_503_when_no_stt_engine_is_configured(self, container) -> None:
        await container.startup()
        container.stt_engine = None
        with _client(container) as client:
            response = client.post(
                "/api/v3/voice/transcribe", files={"file": ("note.wav", b"RIFF....", "audio/wav")},
            )
        assert response.status_code == 503
        assert response.json()["error"] == "dependency_unavailable"
