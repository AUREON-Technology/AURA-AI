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
from aura_core.adapters.language_aware_tts import (
    LanguageAwareTtsEngine,
    select_tts_route,
    tts_engine_name,
)
from aura_core.adapters.piper_tts import (
    DEFAULT_PIPER_AR_MODEL,
    DEFAULT_PIPER_EN_MODEL,
    DEFAULT_PIPER_LENGTH_SCALE,
    PiperTtsEngine,
    DEFAULT_PIPER_NOISE_SCALE,
    DEFAULT_PIPER_NOISE_W,
    DEFAULT_PIPER_SENTENCE_SILENCE,
    build_piper_command,
    piper_available,
    piper_voice_name,
    tashkeel_model_for,
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
        route = select_tts_route(
            "مرحبا بالعالم", voice=None, piper_en_ready=False, piper_ar_ready=False,
        )
        assert route.backend == "espeak"
        assert route.language == "ar"
        assert route.espeak_voice == "ar"
        assert route.rate_wpm == 130

    def test_arabic_with_piper_selects_piper(self) -> None:
        route = select_tts_route(
            "مرحبا بالعالم", voice=None, piper_en_ready=True, piper_ar_ready=True,
        )
        assert route.backend == "piper"
        assert route.language == "ar"

    def test_english_with_piper_selects_piper(self) -> None:
        route = select_tts_route(
            "The report is ready.", voice=None, piper_en_ready=True, piper_ar_ready=True,
        )
        assert route.backend == "piper"
        assert route.language == "en"

    def test_english_without_piper_selects_espeak_en_us(self) -> None:
        route = select_tts_route(
            "The report is ready.", voice=None, piper_en_ready=False, piper_ar_ready=True,
        )
        assert route.backend == "espeak"
        assert route.language == "en"
        assert route.espeak_voice == "en-us"
        assert route.rate_wpm == 142

    def test_explicit_english_voice_wins_on_arabic_text(self) -> None:
        route = select_tts_route(
            "مرحبا", voice="en-us", piper_en_ready=True, piper_ar_ready=True,
        )
        assert route.backend == "piper"
        assert route.language == "en"

    def test_unsafe_voice_falls_through_to_auto_select(self) -> None:
        route = select_tts_route(
            "مرحبا", voice="-w /tmp/x", piper_en_ready=False, piper_ar_ready=False,
        )
        assert route.language == "ar"
        assert route.espeak_voice == "ar"

    def test_health_name_lists_piper_voices_that_exist(self) -> None:
        assert tts_engine_name(piper_en=True, piper_ar=True) == "piper-en+piper-ar"
        assert tts_engine_name(piper_en=True, piper_ar=False) == "piper-en"
        assert tts_engine_name(piper_en=False, piper_ar=True) == "piper-ar"
        assert tts_engine_name(piper_en=False, piper_ar=False) == "espeak-ng"

    async def test_arabic_text_is_sent_to_the_arabic_espeak_engine(self) -> None:
        english, arabic = _RecordingEngine(), _RecordingEngine()
        engine = LanguageAwareTtsEngine(english=english, arabic_fallback=arabic)
        assert engine.name == "espeak-ng"
        await engine.synthesize("مرحبا بالعالم")
        assert arabic.calls and not english.calls
        assert arabic.calls[0][1] == "ar"

    async def test_english_text_is_sent_to_the_english_espeak_engine(self) -> None:
        english, arabic = _RecordingEngine(), _RecordingEngine()
        engine = LanguageAwareTtsEngine(english=english, arabic_fallback=arabic)
        await engine.synthesize("Hello there.")
        assert english.calls and not arabic.calls
        assert english.calls[0][1] == "en-us"

    async def test_arabic_text_is_sent_to_piper_when_present(self) -> None:
        english, arabic, piper_ar = _RecordingEngine(), _RecordingEngine(), _RecordingEngine()
        engine = LanguageAwareTtsEngine(
            english=english, arabic_fallback=arabic, piper_ar=piper_ar,
        )
        assert engine.name == "piper-ar"
        await engine.synthesize("مرحبا بالعالم")
        assert piper_ar.calls and not english.calls and not arabic.calls

    async def test_english_text_is_sent_to_piper_when_present(self) -> None:
        english, arabic, piper_en = _RecordingEngine(), _RecordingEngine(), _RecordingEngine()
        engine = LanguageAwareTtsEngine(
            english=english, arabic_fallback=arabic, piper_en=piper_en,
        )
        assert engine.name == "piper-en"
        await engine.synthesize("The report is ready.")
        assert piper_en.calls and not english.calls and not arabic.calls


class TestPiperCommand:
    def test_model_length_scale_and_wav_path_are_flags_not_positional_text(self) -> None:
        args = build_piper_command("speech.wav", model_path=DEFAULT_PIPER_EN_MODEL)
        assert args[0] == "piper"
        assert args[args.index("--model") + 1] == DEFAULT_PIPER_EN_MODEL
        assert args[args.index("--output_file") + 1] == "speech.wav"
        assert args[args.index("--length-scale") + 1] == str(DEFAULT_PIPER_LENGTH_SCALE)
        assert DEFAULT_PIPER_LENGTH_SCALE == 1.08

    def test_arabic_model_path_is_on_the_argv(self) -> None:
        args = build_piper_command(
            "speech.wav", model_path=DEFAULT_PIPER_AR_MODEL, length_scale=1.08,
        )
        assert args[args.index("--model") + 1] == DEFAULT_PIPER_AR_MODEL
        assert args[args.index("--length-scale") + 1] == "1.08"

    def test_voice_name_follows_the_model_filename(self) -> None:
        assert piper_voice_name(DEFAULT_PIPER_EN_MODEL) == "piper-en"
        assert piper_voice_name(DEFAULT_PIPER_AR_MODEL) == "piper-ar"

    def test_every_prosody_flag_reaches_the_argv(self) -> None:
        # Piper silently ignores flags it does not recognise, so a typo here would leave the
        # voice at stock prosody with nothing in the logs to say so. These spellings are the
        # ones `piper --help` lists for the 2023.11.14 binary.
        args = build_piper_command("speech.wav", model_path=DEFAULT_PIPER_EN_MODEL)
        assert args[args.index("--noise-scale") + 1] == str(DEFAULT_PIPER_NOISE_SCALE)
        assert args[args.index("--noise-w") + 1] == str(DEFAULT_PIPER_NOISE_W)
        assert args[args.index("--sentence-silence") + 1] == str(
            DEFAULT_PIPER_SENTENCE_SILENCE
        )

    def test_prosody_defaults_move_away_from_piper_stock(self) -> None:
        # Stock, per the voice's own .onnx.json and `piper --help`: 0.667 / 0.8 / 0.2s.
        # If these ever equal stock again the whole change has silently reverted.
        assert DEFAULT_PIPER_NOISE_SCALE != 0.667
        assert DEFAULT_PIPER_NOISE_W > 0.8
        assert DEFAULT_PIPER_SENTENCE_SILENCE > 0.2
        # Guard rails: past these the voice slurs / artefacts rather than sounding natural.
        assert DEFAULT_PIPER_NOISE_SCALE <= 0.8
        assert DEFAULT_PIPER_NOISE_W <= 1.0

    def test_prosody_is_overridable(self) -> None:
        args = build_piper_command(
            "speech.wav",
            model_path=DEFAULT_PIPER_EN_MODEL,
            noise_scale=0.5,
            noise_w=0.6,
            sentence_silence=0.1,
        )
        assert args[args.index("--noise-scale") + 1] == "0.5"
        assert args[args.index("--noise-w") + 1] == "0.6"
        assert args[args.index("--sentence-silence") + 1] == "0.1"

    def test_tashkeel_is_absent_unless_asked_for(self) -> None:
        args = build_piper_command("speech.wav", model_path=DEFAULT_PIPER_EN_MODEL)
        assert "--tashkeel-model" not in args

    def test_tashkeel_is_passed_when_supplied(self) -> None:
        args = build_piper_command(
            "speech.wav",
            model_path=DEFAULT_PIPER_AR_MODEL,
            tashkeel_model="/opt/piper/libtashkeel_model.ort",
        )
        assert args[args.index("--tashkeel-model") + 1] == (
            "/opt/piper/libtashkeel_model.ort"
        )


class TestTashkeelSelection:
    """Arabic only, and only when the file is really there -- Piper fails outright on a
    missing tashkeel model, so a partial image must lose diacritics, not lose speech."""

    def test_english_voice_never_gets_a_tashkeel_model(self, tmp_path) -> None:
        present = tmp_path / "libtashkeel_model.ort"
        present.write_bytes(b"stub")
        assert tashkeel_model_for(DEFAULT_PIPER_EN_MODEL, str(present)) is None

    def test_arabic_voice_gets_it_when_the_file_exists(self, tmp_path) -> None:
        present = tmp_path / "libtashkeel_model.ort"
        present.write_bytes(b"stub")
        assert tashkeel_model_for(DEFAULT_PIPER_AR_MODEL, str(present)) == str(present)

    def test_missing_file_degrades_to_no_tashkeel(self, tmp_path) -> None:
        missing = tmp_path / "absent.ort"
        assert tashkeel_model_for(DEFAULT_PIPER_AR_MODEL, str(missing)) is None

    def test_unset_path_is_not_an_error(self) -> None:
        assert tashkeel_model_for(DEFAULT_PIPER_AR_MODEL, None) is None
        assert tashkeel_model_for(DEFAULT_PIPER_AR_MODEL, "") is None


class TestPiperUnavailable:
    def test_missing_binary_or_model_raises_dependency_unavailable(self, monkeypatch) -> None:
        import aura_core.adapters.piper_tts as mod

        monkeypatch.setattr(mod, "piper_available", lambda model_path: False)
        with pytest.raises(DependencyUnavailableError):
            mod.PiperTtsEngine(model_path=DEFAULT_PIPER_EN_MODEL)


@pytest.mark.skipif(
    not piper_available(DEFAULT_PIPER_AR_MODEL),
    reason="piper binary and ar_JO-kareem-medium model not present",
)
class TestPiperArabicTtsEngine:
    def test_satisfies_the_text_to_speech_port(self) -> None:
        assert isinstance(PiperTtsEngine(model_path=DEFAULT_PIPER_AR_MODEL), TextToSpeech)

    def test_name(self) -> None:
        assert PiperTtsEngine(model_path=DEFAULT_PIPER_AR_MODEL).name == "piper-ar"

    async def test_synthesizes_real_wav_bytes(self) -> None:
        audio = await PiperTtsEngine(model_path=DEFAULT_PIPER_AR_MODEL).synthesize("مرحبا")
        assert audio[:4] == b"RIFF"
        assert audio[8:12] == b"WAVE"
        assert len(audio) > 1000


@pytest.mark.skipif(
    not piper_available(DEFAULT_PIPER_EN_MODEL),
    reason="piper binary and en_US-lessac-medium model not present",
)
class TestPiperEnglishTtsEngine:
    def test_name(self) -> None:
        assert PiperTtsEngine(model_path=DEFAULT_PIPER_EN_MODEL).name == "piper-en"

    async def test_synthesizes_real_wav_bytes(self) -> None:
        audio = await PiperTtsEngine(model_path=DEFAULT_PIPER_EN_MODEL).synthesize(
            "The report is ready.",
        )
        assert audio[:4] == b"RIFF"
        assert audio[8:12] == b"WAVE"
        assert len(audio) > 1000
