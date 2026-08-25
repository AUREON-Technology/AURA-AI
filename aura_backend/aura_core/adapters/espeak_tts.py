"""``espeak-ng`` last-resort implementation of the ``TextToSpeech`` port (Phase F2).

Self-hosted, real synthesis via the ``espeak-ng`` binary -- no vendor API. This is a formant
synthesizer, not a neural model; ``LanguageAwareTtsEngine`` uses it only when Piper's ONNX
for that language is missing. Defaults are chosen for comprehension, not espeak's stock
"as fast as a formant can go": US English ~142 wpm (stock is 175), Arabic ~130 wpm, a short
gap between words, and a slightly lower pitch. Operators override those via ``VoiceSettings``;
a request may name a voice but cannot change rate.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from ..domain.errors import DependencyUnavailableError
from .spoken_text import prepare_spoken_text

logger = logging.getLogger(__name__)

__all__ = [
    "EspeakTtsEngine",
    "espeak_available",
    "build_espeak_command",
    "resolve_espeak_voice",
    "DEFAULT_TTS_VOICE",
    "DEFAULT_TTS_RATE_WPM",
    "DEFAULT_TTS_WORD_GAP",
    "DEFAULT_TTS_PITCH",
    "DEFAULT_TTS_AMPLITUDE",
    "DEFAULT_TTS_AR_VOICE",
    "DEFAULT_TTS_AR_RATE_WPM",
]

_BINARY = "espeak-ng"
_VOICE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9+_\\-]{0,31}$")

# US English is clearer than espeak's default `en` (British RP) for this product's audience.
# 142 wpm is deliberate: 175 (espeak stock) is fine for a human narrator and muddy for a
# formant voice. Word-gap units are 10 ms at the default speed (`man espeak-ng`).
DEFAULT_TTS_VOICE = "en-us"
DEFAULT_TTS_RATE_WPM = 142
DEFAULT_TTS_WORD_GAP = 6
DEFAULT_TTS_PITCH = 45
DEFAULT_TTS_AMPLITUDE = 88
# Arabic espeak fallback when Piper is absent. Slower than the English profile: Arabic
# formant speech is denser per syllable, and 175 wpm (espeak stock) is unusable.
DEFAULT_TTS_AR_VOICE = "ar"
DEFAULT_TTS_AR_RATE_WPM = 130


def espeak_available() -> bool:
    return shutil.which(_BINARY) is not None


def resolve_espeak_voice(requested: str | None, default: str = DEFAULT_TTS_VOICE) -> str:
    """Accept an espeak voice code, or fall back. Rejects shell metacharacters."""
    candidate = (requested or default).strip()
    if _VOICE_NAME.fullmatch(candidate):
        return candidate
    return default


def build_espeak_command(
    out_path: str,
    text: str,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    rate_wpm: int = DEFAULT_TTS_RATE_WPM,
    word_gap: int = DEFAULT_TTS_WORD_GAP,
    pitch: int = DEFAULT_TTS_PITCH,
    amplitude: int = DEFAULT_TTS_AMPLITUDE,
) -> list[str]:
    """argv for one synthesis. Pure, so tests can lock the flags without the binary."""
    return [
        _BINARY,
        "-w",
        out_path,
        "-v",
        resolve_espeak_voice(voice),
        "-s",
        str(rate_wpm),
        "-g",
        str(word_gap),
        "-p",
        str(pitch),
        "-a",
        str(amplitude),
        text,
    ]


class EspeakTtsEngine:
    def __init__(
        self,
        *,
        voice: str = DEFAULT_TTS_VOICE,
        rate_wpm: int = DEFAULT_TTS_RATE_WPM,
        word_gap: int = DEFAULT_TTS_WORD_GAP,
        pitch: int = DEFAULT_TTS_PITCH,
        amplitude: int = DEFAULT_TTS_AMPLITUDE,
    ) -> None:
        if not espeak_available():
            raise DependencyUnavailableError(
                "the espeak-ng binary is not on PATH", dependency="espeak-ng",
            )
        self._voice = resolve_espeak_voice(voice)
        self._rate_wpm = rate_wpm
        self._word_gap = word_gap
        self._pitch = pitch
        self._amplitude = amplitude

    @property
    def name(self) -> str:
        return "espeak-ng"

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        spoken = prepare_spoken_text(text)
        if not spoken:
            return b""
        chosen = resolve_espeak_voice(voice, self._voice)
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "speech.wav"
            args = build_espeak_command(
                str(out_path),
                spoken,
                voice=chosen,
                rate_wpm=self._rate_wpm,
                word_gap=self._word_gap,
                pitch=self._pitch,
                amplitude=self._amplitude,
            )
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await process.communicate()
            if process.returncode != 0:
                logger.warning(
                    "espeak-ng failed", extra={"stderr": stderr.decode(errors="replace")[:500]},
                )
                raise RuntimeError(f"espeak-ng exited {process.returncode}")
            return out_path.read_bytes()
