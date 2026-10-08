"""In-memory speech adapters (Phase H).

Real doubles for ``SpeechToText`` / ``TextToSpeech`` so default ``pytest`` exercises the same
session decode path production uses — no mocks inside test files, no model weights.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from typing import Mapping

from ..voice.wav_pcm import pcm16_mono_to_wav, wav_to_pcm16

__all__ = ["InMemoryStt", "InMemoryTts"]


class InMemoryStt:
    """Deterministic STT: map of content-hash / fixture key → transcript, else echo marker."""

    name = "in-memory-stt"

    def __init__(self, transcripts: Mapping[bytes, str] | None = None, *, default: str = "hello") -> None:
        self._map = dict(transcripts or {})
        self._default = default

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        if audio in self._map:
            return self._map[audio]
        # Accept raw PCM by wrapping; callers normally send WAV.
        try:
            pcm, _rate, _ch = wav_to_pcm16(audio)
        except Exception:
            pcm = audio
        for key, text in self._map.items():
            if key == pcm or key in audio:
                return text
        if not pcm:
            return ""
        return self._default


class InMemoryTts:
    """Return a minimal valid WAV (sine or silence) at a configurable sample rate."""

    name = "in-memory-tts"

    def __init__(
        self,
        *,
        sample_rate: int = 22_050,
        duration_ms: int = 40,
        frequency_hz: float = 440.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._duration_ms = duration_ms
        self._frequency_hz = frequency_hz

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        n = max(1, int(self._sample_rate * self._duration_ms / 1000))
        if not (text or "").strip():
            pcm = b"\x00\x00" * n
        else:
            pcm = bytearray()
            for i in range(n):
                sample = int(
                    0.2 * 32767 * math.sin(2 * math.pi * self._frequency_hz * i / self._sample_rate)
                )
                pcm.extend(struct.pack("<h", sample))
            pcm = bytes(pcm)
        return pcm16_mono_to_wav(pcm, self._sample_rate)
