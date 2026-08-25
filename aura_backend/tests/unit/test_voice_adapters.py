"""``FasterWhisperEngine`` and ``EspeakTtsEngine`` (Phase F2): real, self-hosted speech adapters.

Skipped cleanly wherever the underlying binary/package genuinely is not present, the same
convention ``test_tesseract_ocr.py`` uses for Tesseract.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.espeak_tts import (
    DEFAULT_TTS_RATE_WPM,
    DEFAULT_TTS_VOICE,
    EspeakTtsEngine,
    build_espeak_command,
    espeak_available,
    resolve_espeak_voice,
)
from aura_core.adapters.whisper_stt import FasterWhisperEngine, faster_whisper_available
from aura_core.domain.errors import DependencyUnavailableError
from aura_core.ports.speech import SpeechToText, TextToSpeech


class TestEspeakCommand:
    def test_narration_flags_are_slower_and_us_english(self) -> None:
        args = build_espeak_command("speech.wav", "Hello there.")
        assert args[0] == "espeak-ng"
        assert "-v" in args and args[args.index("-v") + 1] == DEFAULT_TTS_VOICE
        assert "-s" in args and args[args.index("-s") + 1] == str(DEFAULT_TTS_RATE_WPM)
        assert int(args[args.index("-s") + 1]) < 175
        assert "-g" in args
        assert "-p" in args
        assert args[-1] == "Hello there."

    def test_rejects_a_voice_that_looks_like_a_shell_flag(self) -> None:
        assert resolve_espeak_voice("-w /tmp/x") == DEFAULT_TTS_VOICE
        assert resolve_espeak_voice("en-us+m3") == "en-us+m3"


@pytest.mark.skipif(not espeak_available(), reason="espeak-ng binary not on PATH")
class TestEspeakTtsEngine:
    def test_satisfies_the_text_to_speech_port(self) -> None:
        assert isinstance(EspeakTtsEngine(), TextToSpeech)

    def test_name(self) -> None:
        assert EspeakTtsEngine().name == "espeak-ng"

    async def test_synthesizes_real_wav_bytes(self) -> None:
        engine = EspeakTtsEngine()
        audio = await engine.synthesize("The quarterly report is ready.")
        assert audio[:4] == b"RIFF"  # a real WAV file, not a stub
        assert audio[8:12] == b"WAVE"
        assert len(audio) > 1000

    async def test_blank_text_synthesizes_to_nothing(self) -> None:
        engine = EspeakTtsEngine()
        assert await engine.synthesize("   ") == b""

    async def test_longer_text_produces_more_audio(self) -> None:
        engine = EspeakTtsEngine()
        short = await engine.synthesize("Hello.")
        long = await engine.synthesize("Hello. " * 20)
        assert len(long) > len(short)

    async def test_a_slower_rate_produces_longer_audio(self) -> None:
        text = "The quarterly report is ready for review by the finance team."
        fast = await EspeakTtsEngine(rate_wpm=200).synthesize(text)
        slow = await EspeakTtsEngine(rate_wpm=110).synthesize(text)
        assert len(slow) > len(fast)


class TestEspeakUnavailable:
    def test_missing_binary_raises_dependency_unavailable(self, monkeypatch) -> None:
        import aura_core.adapters.espeak_tts as mod

        monkeypatch.setattr(mod, "espeak_available", lambda: False)
        with pytest.raises(DependencyUnavailableError):
            mod.EspeakTtsEngine()


@pytest.mark.skipif(
    not (espeak_available() and faster_whisper_available()),
    reason="requires both espeak-ng and faster-whisper for a real round trip",
)
class TestFasterWhisperEngine:
    def test_satisfies_the_speech_to_text_port(self) -> None:
        assert isinstance(FasterWhisperEngine(), SpeechToText)

    def test_name_before_the_model_ever_loads(self) -> None:
        """Construction is lazy -- the name is available without paying the model-load cost."""
        assert FasterWhisperEngine(model_size="tiny.en").name == "faster-whisper-tiny.en"

    async def test_recognises_real_synthesized_speech(self) -> None:
        tts = EspeakTtsEngine()
        stt = FasterWhisperEngine()
        audio = await tts.synthesize("The invoice total is one hundred dollars.")
        text = await stt.transcribe(audio)
        # Whisper may normalise "one hundred dollars" to "$100" -- both are a correct
        # recognition, so check for the word that cannot be renormalised away.
        assert "invoice" in text.lower()
        assert "100" in text or "hundred" in text.lower()

    async def test_silence_transcribes_to_empty_or_near_empty(self) -> None:
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)  # one second of silence

        stt = FasterWhisperEngine()
        text = await stt.transcribe(buf.getvalue())
        assert text.strip() == ""


class TestFasterWhisperUnavailable:
    def test_missing_package_raises_dependency_unavailable(self, monkeypatch) -> None:
        import aura_core.adapters.whisper_stt as mod

        monkeypatch.setattr(mod, "faster_whisper_available", lambda: False)
        with pytest.raises(DependencyUnavailableError):
            mod.FasterWhisperEngine()
