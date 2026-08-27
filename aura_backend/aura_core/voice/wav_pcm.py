"""PCM16 ↔ WAV helpers (Phase H).

Uses the stdlib ``wave`` module. Never assume a fixed 44-byte header — Piper/espeak WAV files
may include ``fact`` chunks.
"""

from __future__ import annotations

import io
import wave

__all__ = ["pcm16_mono_to_wav", "wav_to_pcm16"]


def pcm16_mono_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw little-endian PCM16 mono samples in a WAV container for batch STT."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int, int]:
    """Decode WAV to ``(pcm16_bytes, sample_rate, channels)``.

    Multi-channel audio is returned as interleaved PCM; callers that need mono should downmix.
    """
    if not wav_bytes:
        raise ValueError("empty wav")
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if sample_width != 2:
        raise ValueError(f"unsupported sample width {sample_width}; expected 16-bit PCM")
    return frames, sample_rate, channels
