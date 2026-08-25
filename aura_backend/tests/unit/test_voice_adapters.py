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

    def test_arabic_voice_and_slower_rate_are_on_the_argv(self) -> None:
        args = build_espeak_command(
            "speech.wav", "مرحبا بالعالم.", voice="ar", rate_wpm=130,
        )
        assert args[args.index("-v") + 1] == "ar"
        assert args[args.index("-s") + 1] == "130"
        assert args[-1] == "مرحبا بالعالم."

    def test_english_argv_stays_us_english_at_one_hundred_forty_two_wpm(self) -> None:
        args = build_espeak_command("speech.wav", "Hello there.")
        assert args[args.index("-v") + 1] == "en-us"
        assert args[args.index("-s") + 1] == "142"


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


class _RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        self.calls.append((text, voice))
        return b"RIFF"


class TestLanguageAwareRouting:
    def test_arabic_without_piper_selects_espeak_ar(self) -> None:
        from aura_core.adapters.language_aware_tts import select_tts_route

        route = select_tts_route("مرحبا بالعالم", voice=None, piper_ready=False)
        assert route.backend == "espeak"
        assert route.espeak_voice == "ar"
        assert route.rate_wpm == 130

    def test_arabic_with_piper_selects_piper(self) -> None:
        from aura_core.adapters.language_aware_tts import select_tts_route

        route = select_tts_route("مرحبا بالعالم", voice=None, piper_ready=True)
        assert route.backend == "piper"

    def test_english_selects_us_english_at_one_hundred_forty_two_wpm(self) -> None:
        from aura_core.adapters.language_aware_tts import select_tts_route

        route = select_tts_route("The report is ready.", voice=None, piper_ready=True)
        assert route.backend == "espeak"
        assert route.espeak_voice == "en-us"
        assert route.rate_wpm == 142

    def test_explicit_english_voice_wins_on_arabic_text(self) -> None:
        from aura_core.adapters.language_aware_tts import select_tts_route

        route = select_tts_route("مرحبا", voice="en-us", piper_ready=True)
        assert route.backend == "espeak"
        assert route.espeak_voice == "en-us"

    def test_unsafe_voice_falls_through_to_auto_select(self) -> None:
        from aura_core.adapters.language_aware_tts import select_tts_route

        route = select_tts_route("مرحبا", voice="-w /tmp/x", piper_ready=False)
        assert route.espeak_voice == "ar"

    async def test_arabic_text_is_sent_to_the_arabic_espeak_engine(self) -> None:
        from aura_core.adapters.language_aware_tts import LanguageAwareTtsEngine

        english, arabic = _RecordingEngine(), _RecordingEngine()
        engine = LanguageAwareTtsEngine(english=english, arabic_fallback=arabic)
        assert engine.name == "espeak-ng"
        await engine.synthesize("مرحبا بالعالم")
        assert arabic.calls and not english.calls
        assert arabic.calls[0][1] == "ar"

    async def test_english_text_is_sent_to_the_english_espeak_engine(self) -> None:
        from aura_core.adapters.language_aware_tts import LanguageAwareTtsEngine

        english, arabic = _RecordingEngine(), _RecordingEngine()
        engine = LanguageAwareTtsEngine(english=english, arabic_fallback=arabic)
        await engine.synthesize("Hello there.")
        assert english.calls and not arabic.calls
        assert english.calls[0][1] == "en-us"

    async def test_arabic_text_is_sent_to_piper_when_present(self) -> None:
        from aura_core.adapters.language_aware_tts import LanguageAwareTtsEngine

        english, arabic, piper = _RecordingEngine(), _RecordingEngine(), _RecordingEngine()
        engine = LanguageAwareTtsEngine(
            english=english, arabic_fallback=arabic, piper=piper,
        )
        assert engine.name == "piper-ar+espeak-ng"
        await engine.synthesize("مرحبا بالعالم")
        assert piper.calls and not english.calls and not arabic.calls


class TestPiperCommand:
    def test_model_and_wav_path_are_flags_not_positional_text(self) -> None:
        from aura_core.adapters.piper_tts import DEFAULT_PIPER_AR_MODEL, build_piper_command

        args = build_piper_command("speech.wav", model_path=DEFAULT_PIPER_AR_MODEL)
        assert args[0] == "piper"
        assert args[args.index("--model") + 1] == DEFAULT_PIPER_AR_MODEL
        assert args[args.index("--output_file") + 1] == "speech.wav"


class TestPiperUnavailable:
    def test_missing_binary_or_model_raises_dependency_unavailable(self, monkeypatch) -> None:
        import aura_core.adapters.piper_tts as mod

        monkeypatch.setattr(mod, "piper_available", lambda model_path=None: False)
        with pytest.raises(DependencyUnavailableError):
            mod.PiperTtsEngine()


@pytest.mark.skipif(
    not __import__("aura_core.adapters.piper_tts", fromlist=["piper_available"]).piper_available(),
    reason="piper binary and ar_JO-kareem-medium model not present",
)
class TestPiperTtsEngine:
    def test_satisfies_the_text_to_speech_port(self) -> None:
        from aura_core.adapters.piper_tts import PiperTtsEngine

        assert isinstance(PiperTtsEngine(), TextToSpeech)

    def test_name(self) -> None:
        from aura_core.adapters.piper_tts import PiperTtsEngine

        assert PiperTtsEngine().name == "piper-ar"

    async def test_synthesizes_real_wav_bytes(self) -> None:
        from aura_core.adapters.piper_tts import PiperTtsEngine

        audio = await PiperTtsEngine().synthesize("مرحبا")
        assert audio[:4] == b"RIFF"
        assert audio[8:12] == b"WAVE"
        assert len(audio) > 1000
