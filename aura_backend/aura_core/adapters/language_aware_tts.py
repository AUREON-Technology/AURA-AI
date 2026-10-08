"""Language-aware ``TextToSpeech``: Piper first for English and Arabic, espeak last-resort.

Detection is on the reply text. English uses ``en_US-lessac-medium`` when that ONNX is on
disk; Arabic uses ``ar_JO-kareem-medium``. Missing a language's model falls through to
espeak-ng (``en-us`` ~142 wpm / ``ar`` ~130 wpm). An explicit ``voice`` still wins when it
is a safe espeak code; omit ``voice`` for auto-select.
"""

from __future__ import annotations

from dataclasses import dataclass

from .espeak_tts import (
    DEFAULT_TTS_AR_RATE_WPM,
    DEFAULT_TTS_AR_VOICE,
    DEFAULT_TTS_RATE_WPM,
    DEFAULT_TTS_VOICE,
    EspeakTtsEngine,
    resolve_espeak_voice,
)
from .piper_tts import PiperTtsEngine
from .speech_language import detect_speech_language

__all__ = [
    "LanguageAwareTtsEngine",
    "TtsRoute",
    "select_tts_route",
    "is_arabic_espeak_voice",
    "tts_engine_name",
]


@dataclass(frozen=True, slots=True)
class TtsRoute:
    """Which backend should speak this text, and with which espeak profile if any."""

    backend: str  # "piper" | "espeak"
    language: str  # "en" | "ar"
    espeak_voice: str
    rate_wpm: int


def is_arabic_espeak_voice(code: str) -> bool:
    """True for espeak Arabic voices (``ar``, ``ar-jo``, ``ar+m3``), not ``arabic``."""
    normalized = code.strip().lower().replace("_", "-")
    return (
        normalized == "ar"
        or normalized.startswith("ar-")
        or normalized.startswith("ar+")
    )


def tts_engine_name(*, piper_en: bool, piper_ar: bool) -> str:
    """Health ``voice.tts`` label: piper voices that exist, else ``espeak-ng``."""
    parts: list[str] = []
    if piper_en:
        parts.append("piper-en")
    if piper_ar:
        parts.append("piper-ar")
    if parts:
        return "+".join(parts)
    return "espeak-ng"


def select_tts_route(
    text: str,
    *,
    voice: str | None,
    piper_en_ready: bool,
    piper_ar_ready: bool,
    english_voice: str = DEFAULT_TTS_VOICE,
    english_rate_wpm: int = DEFAULT_TTS_RATE_WPM,
    arabic_voice: str = DEFAULT_TTS_AR_VOICE,
    arabic_rate_wpm: int = DEFAULT_TTS_AR_RATE_WPM,
) -> TtsRoute:
    """Pure routing so tests can lock the decision without any binary."""
    language = detect_speech_language(text)
    requested = (voice or "").strip()
    explicit_english_voice = english_voice
    if requested:
        safe = resolve_espeak_voice(requested, english_voice)
        if safe == requested:
            if is_arabic_espeak_voice(safe):
                language = "ar"
            else:
                language = "en"
                explicit_english_voice = safe
        # Unsafe string: fall through to auto-select rather than honour a shell-like token.

    if language == "ar":
        if piper_ar_ready:
            return TtsRoute("piper", "ar", arabic_voice, arabic_rate_wpm)
        return TtsRoute("espeak", "ar", arabic_voice, arabic_rate_wpm)
    if piper_en_ready:
        return TtsRoute("piper", "en", explicit_english_voice, english_rate_wpm)
    return TtsRoute("espeak", "en", explicit_english_voice, english_rate_wpm)


class LanguageAwareTtsEngine:
    def __init__(
        self,
        *,
        english: EspeakTtsEngine,
        arabic_fallback: EspeakTtsEngine,
        piper_en: PiperTtsEngine | None = None,
        piper_ar: PiperTtsEngine | None = None,
        english_voice: str = DEFAULT_TTS_VOICE,
        english_rate_wpm: int = DEFAULT_TTS_RATE_WPM,
        arabic_voice: str = DEFAULT_TTS_AR_VOICE,
        arabic_rate_wpm: int = DEFAULT_TTS_AR_RATE_WPM,
    ) -> None:
        self._english = english
        self._arabic = arabic_fallback
        self._piper_en = piper_en
        self._piper_ar = piper_ar
        self._english_voice = english_voice
        self._english_rate_wpm = english_rate_wpm
        self._arabic_voice = arabic_voice
        self._arabic_rate_wpm = arabic_rate_wpm

    @property
    def name(self) -> str:
        return tts_engine_name(
            piper_en=self._piper_en is not None,
            piper_ar=self._piper_ar is not None,
        )

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        route = select_tts_route(
            text,
            voice=voice,
            piper_en_ready=self._piper_en is not None,
            piper_ar_ready=self._piper_ar is not None,
            english_voice=self._english_voice,
            english_rate_wpm=self._english_rate_wpm,
            arabic_voice=self._arabic_voice,
            arabic_rate_wpm=self._arabic_rate_wpm,
        )
        if route.backend == "piper":
            piper = self._piper_ar if route.language == "ar" else self._piper_en
            if piper is not None:
                return await piper.synthesize(text, voice=voice)
        if route.language == "ar":
            return await self._arabic.synthesize(text, voice=route.espeak_voice)
        return await self._english.synthesize(text, voice=route.espeak_voice)
