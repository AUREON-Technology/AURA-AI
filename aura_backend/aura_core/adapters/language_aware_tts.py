"""Language-aware ``TextToSpeech``: Piper Arabic when present, else espeak-ng.

English stays on the slower, clearer US English espeak profile. Arabic uses Piper
``ar_JO-kareem-medium`` when the binary and model are on disk, and espeak ``-v ar`` at a
deliberately slower ~130 wpm when they are not. Detection is on the reply text; an explicit
``voice`` still wins when it is a safe espeak code.
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
]


@dataclass(frozen=True, slots=True)
class TtsRoute:
    """Which backend should speak this text, and with which espeak profile if any."""

    backend: str  # "piper" | "espeak"
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


def select_tts_route(
    text: str,
    *,
    voice: str | None,
    piper_ready: bool,
    english_voice: str = DEFAULT_TTS_VOICE,
    english_rate_wpm: int = DEFAULT_TTS_RATE_WPM,
    arabic_voice: str = DEFAULT_TTS_AR_VOICE,
    arabic_rate_wpm: int = DEFAULT_TTS_AR_RATE_WPM,
) -> TtsRoute:
    """Pure routing so tests can lock the decision without any binary."""
    requested = (voice or "").strip()
    if requested:
        safe = resolve_espeak_voice(requested, english_voice)
        if safe == requested:
            if is_arabic_espeak_voice(safe):
                if piper_ready:
                    return TtsRoute("piper", arabic_voice, arabic_rate_wpm)
                return TtsRoute("espeak", arabic_voice, arabic_rate_wpm)
            return TtsRoute("espeak", safe, english_rate_wpm)
        # Unsafe string: fall through to auto-select rather than honour a shell-like token.

    if detect_speech_language(text) == "ar":
        if piper_ready:
            return TtsRoute("piper", arabic_voice, arabic_rate_wpm)
        return TtsRoute("espeak", arabic_voice, arabic_rate_wpm)
    return TtsRoute("espeak", english_voice, english_rate_wpm)


class LanguageAwareTtsEngine:
    def __init__(
        self,
        *,
        english: EspeakTtsEngine,
        arabic_fallback: EspeakTtsEngine,
        piper: PiperTtsEngine | None = None,
        english_voice: str = DEFAULT_TTS_VOICE,
        english_rate_wpm: int = DEFAULT_TTS_RATE_WPM,
        arabic_voice: str = DEFAULT_TTS_AR_VOICE,
        arabic_rate_wpm: int = DEFAULT_TTS_AR_RATE_WPM,
    ) -> None:
        self._english = english
        self._arabic = arabic_fallback
        self._piper = piper
        self._english_voice = english_voice
        self._english_rate_wpm = english_rate_wpm
        self._arabic_voice = arabic_voice
        self._arabic_rate_wpm = arabic_rate_wpm

    @property
    def name(self) -> str:
        if self._piper is not None:
            return "piper-ar+espeak-ng"
        return "espeak-ng"

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        route = select_tts_route(
            text,
            voice=voice,
            piper_ready=self._piper is not None,
            english_voice=self._english_voice,
            english_rate_wpm=self._english_rate_wpm,
            arabic_voice=self._arabic_voice,
            arabic_rate_wpm=self._arabic_rate_wpm,
        )
        if route.backend == "piper" and self._piper is not None:
            return await self._piper.synthesize(text, voice=voice)
        if is_arabic_espeak_voice(route.espeak_voice):
            return await self._arabic.synthesize(text, voice=route.espeak_voice)
        return await self._english.synthesize(text, voice=route.espeak_voice)
