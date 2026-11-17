"""``POST /api/v3/voice/transcribe`` takes the clip's language (Phase V, GAP-020).

Arabic speech sent to an English-only Whisper comes back as an English guess, so the route
refuses a language the configured model cannot hear instead of returning that guess. Without a
``language`` a multilingual model detects it. Either way the response says which language was
used. A client that sends no ``language`` still gets the same fields as before, plus
``language``.
"""

from __future__ import annotations

from aura_core.adapters.in_memory_speech import InMemoryStt
from aura_core.domain.identifiers import UserId

AUDIO = ("note.wav", b"RIFF-not-really-a-wav", "audio/wav")


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("voicer"), email="v@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


class _PlainStt:
    """A ``SpeechToText`` that implements only the base port: no language reporting."""

    name = "plain-stt"

    def __init__(self) -> None:
        self.last_language: str | None = "unset"

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        self.last_language = language
        return "plain words"


class TestTheRequestedLanguageReachesTheEngine:
    async def test_arabic_is_passed_through_and_reported(self, container) -> None:
        await container.startup()
        engine = InMemoryStt(default="مرحبا")
        container.stt_engine = engine
        with _client(container) as client:
            response = client.post(
                "/api/v3/voice/transcribe", files={"file": AUDIO}, data={"language": "ar"}
            )
        assert response.status_code == 200
        assert response.json() == {"text": "مرحبا", "engine": "in-memory-stt", "language": "ar"}
        assert engine.last_language == "ar"

    async def test_a_region_tag_is_folded_to_its_language(self, container) -> None:
        await container.startup()
        engine = InMemoryStt()
        container.stt_engine = engine
        with _client(container) as client:
            response = client.post(
                "/api/v3/voice/transcribe", files={"file": AUDIO}, data={"language": "AR-eg"}
            )
        assert response.status_code == 200
        assert engine.last_language == "ar"
        assert response.json()["language"] == "ar"


class TestWithoutALanguageTheEngineDetectsIt:
    async def test_the_detected_language_is_returned(self, container) -> None:
        await container.startup()
        engine = InMemoryStt(detected_language="ar")
        container.stt_engine = engine
        with _client(container) as client:
            response = client.post("/api/v3/voice/transcribe", files={"file": AUDIO})
        assert response.status_code == 200
        assert engine.last_language is None
        assert response.json()["language"] == "ar"

    async def test_an_engine_that_cannot_report_still_transcribes(self, container) -> None:
        await container.startup()
        engine = _PlainStt()
        container.stt_engine = engine
        with _client(container) as client:
            plain = client.post("/api/v3/voice/transcribe", files={"file": AUDIO})
            hinted = client.post(
                "/api/v3/voice/transcribe", files={"file": AUDIO}, data={"language": "ar"}
            )
        assert plain.status_code == 200
        assert plain.json() == {"text": "plain words", "engine": "plain-stt", "language": None}
        assert hinted.status_code == 200
        assert hinted.json()["language"] == "ar"
        assert engine.last_language == "ar"


class TestALanguageTheModelCannotHearIsRefused:
    async def test_an_unknown_code_is_422(self, container) -> None:
        await container.startup()
        engine = InMemoryStt()
        container.stt_engine = engine
        with _client(container) as client:
            response = client.post(
                "/api/v3/voice/transcribe", files={"file": AUDIO}, data={"language": "klingon"}
            )
        assert response.status_code == 422
        assert response.json()["details"]["reason"] == "language_unsupported"
        assert engine.last_language is None  # never reached the engine

    async def test_arabic_on_an_english_only_model_is_422(self, container) -> None:
        await container.startup()
        engine = InMemoryStt(multilingual=False)
        container.stt_engine = engine
        with _client(container) as client:
            arabic = client.post(
                "/api/v3/voice/transcribe", files={"file": AUDIO}, data={"language": "ar"}
            )
            english = client.post(
                "/api/v3/voice/transcribe", files={"file": AUDIO}, data={"language": "en"}
            )
        assert arabic.status_code == 422
        assert arabic.json()["details"]["language"] == "ar"
        assert english.status_code == 200
        assert english.json()["language"] == "en"


class TestTheWhisperAdapterKnowsWhichSizesAreEnglishOnly:
    def test_dot_en_sizes_are_english_only(self) -> None:
        from aura_core.adapters.whisper_stt import FasterWhisperEngine, faster_whisper_available

        if not faster_whisper_available():
            import pytest

            pytest.skip("faster-whisper is not installed")
        assert FasterWhisperEngine(model_size="tiny.en").multilingual is False
        assert FasterWhisperEngine(model_size="base").multilingual is True
        assert FasterWhisperEngine(model_size="small").multilingual is True
