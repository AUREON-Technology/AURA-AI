"""``POST /api/v3/voice/transcribe`` with ``detail=segments`` (ADR-040).

The default response is untouched (``tests/unit/test_voice_transcribe_language.py`` pins it). With
``detail=segments`` the route returns the engine's timestamped transcript: segments with
start/end/text/confidence and optional words, the language and duration, and an explicit statement
that speakers are not identified.

The engine here is ``FasterWhisperEngine`` itself with its model replaced by the faster-whisper result
shape (the package is not installable in this environment), so the real mapping runs end to end;
``InMemoryStt`` stands in for an engine that cannot report timestamps.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from aura_core.adapters.in_memory_speech import InMemoryStt
from aura_core.domain.identifiers import UserId

AUDIO = ("note.wav", b"RIFF-not-really-a-wav", "audio/wav")


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("voicer"), email="v@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


class _Model:
    """What ``faster_whisper.WhisperModel.transcribe`` returns: a lazy segment iterable and an info object."""

    def __init__(self) -> None:
        self.kwargs: dict = {}

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        words = [NS(start=0.0, end=0.4, word=" Marhaba", probability=0.93),
                 NS(start=0.4, end=0.9, word=" friends", probability=0.88)] if kwargs.get("word_timestamps") else None
        segments = iter([
            NS(start=0.0, end=1.2, text=" Marhaba friends ", avg_logprob=-0.1, no_speech_prob=0.02, words=words),
            NS(start=1.4, end=2.6, text=" welcome back ", avg_logprob=-0.5, no_speech_prob=0.1, words=None),
        ])
        return segments, NS(language="ar", language_probability=0.91, duration=2.75)


def _whisper(multilingual: bool = True):
    from aura_core.adapters.whisper_stt import FasterWhisperEngine

    engine = FasterWhisperEngine.__new__(FasterWhisperEngine)  # no faster-whisper import: the model is injected
    model = _Model()
    engine._model_size = "small" if multilingual else "tiny.en"
    engine._revision = ""
    engine._model = model
    engine._lock = __import__("asyncio").Lock()
    return engine, model


class TestSegments:
    async def test_the_timestamped_transcript(self, container) -> None:
        await container.startup()
        engine, model = _whisper()
        container.stt_engine = engine
        with _client(container) as client:
            response = client.post("/api/v3/voice/transcribe", files={"file": AUDIO}, data={"detail": "segments"})
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["detail"] == "segments" and body["engine"] == "faster-whisper-small"
        assert body["text"] == "Marhaba friends welcome back"
        assert (body["language"], body["language_probability"], body["duration"]) == ("ar", 0.91, 2.75)
        first, second = body["segments"]
        assert (first["start"], first["end"], first["text"]) == (0.0, 1.2, "Marhaba friends")
        assert first["words"] == [
            {"start": 0.0, "end": 0.4, "text": "Marhaba", "confidence": 0.93},
            {"start": 0.4, "end": 0.9, "text": "friends", "confidence": 0.88},
        ]
        assert 0.8 < first["confidence"] < 0.95 and second["confidence"] < first["confidence"]
        assert second["words"] == []
        assert all(s["speaker"] is None for s in body["segments"])
        assert body["diarization"]["available"] is False and "speaker" in body["diarization"]["reason"]
        assert model.kwargs["word_timestamps"] is True

    async def test_words_can_be_turned_off(self, container) -> None:
        await container.startup()
        engine, model = _whisper()
        container.stt_engine = engine
        with _client(container) as client:
            body = client.post("/api/v3/voice/transcribe", files={"file": AUDIO},
                               data={"detail": "segments", "words": "false"}).json()
        assert "words" not in body["segments"][0] and model.kwargs["word_timestamps"] is False

    async def test_a_language_hint_reaches_the_engine_and_an_english_only_model_ignores_it(self, container) -> None:
        await container.startup()
        engine, model = _whisper()
        container.stt_engine = engine
        with _client(container) as client:
            client.post("/api/v3/voice/transcribe", files={"file": AUDIO}, data={"detail": "segments", "language": "AR-eg"})
        assert model.kwargs["language"] == "ar"

        english, model = _whisper(multilingual=False)
        container.stt_engine = english
        with _client(container) as client:
            body = client.post("/api/v3/voice/transcribe", files={"file": AUDIO},
                               data={"detail": "segments", "language": "ar"}).json()
        assert model.kwargs["language"] is None and body["language"] == "en"

    async def test_an_engine_without_timestamps_is_unavailable_not_untimed(self, container) -> None:
        await container.startup()
        container.stt_engine = InMemoryStt()
        with _client(container) as client:
            response = client.post("/api/v3/voice/transcribe", files={"file": AUDIO}, data={"detail": "segments"})
        assert response.status_code == 503
        assert "timestamps" in response.text and "segments" not in response.json()

    async def test_an_unknown_detail_is_a_422_and_the_default_is_unchanged(self, container) -> None:
        await container.startup()
        container.stt_engine = InMemoryStt(default="plain words")
        with _client(container) as client:
            bad = client.post("/api/v3/voice/transcribe", files={"file": AUDIO}, data={"detail": "everything"})
            default = client.post("/api/v3/voice/transcribe", files={"file": AUDIO})
        assert bad.status_code == 422
        assert default.json() == {"text": "plain words", "engine": "in-memory-stt", "language": "en"}
