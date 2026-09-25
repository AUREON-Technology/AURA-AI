"""The voice scenario's audio fixture.

Generated deterministically at runtime with the stdlib rather than committed as a binary: a
2-second, 16 kHz, mono, 16-bit PCM WAV (~64 KB) of vowel-like voiced segments (a 120 Hz glottal
pulse train shaped by three formant resonances, with pauses between "syllables"). It exercises
the STT engine's real decode path -- faster-whisper's cost scales with audio duration, not
content -- but it is not intelligible speech, so the transcript is usually empty or a short
hallucination. The scenario then sends a fixed spoken-style question to the kernel instead and
counts the substitution (``transcript_fallbacks``).

For representative STT numbers, pass a real recording with ``--voice-wav``: 16 kHz mono WAV of
a 3-8 second question, which is what the Flutter client uploads.
"""

from __future__ import annotations

import io
import math
import struct
import wave

SAMPLE_RATE = 16_000

# (F1, F2, F3) in Hz for a few vowels -- enough spectral shape that the audio is not a pure tone.
_VOWELS = ((730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480))


def synth_wav(seconds: float = 2.0, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    total = int(seconds * sample_rate)
    syllable = int(0.28 * sample_rate)
    gap = int(0.08 * sample_rate)
    period = sample_rate / 120.0
    frames = bytearray()
    for n in range(total):
        pos = n % (syllable + gap)
        if pos >= syllable:
            frames += struct.pack("<h", 0)
            continue
        vowel = _VOWELS[(n // (syllable + gap)) % len(_VOWELS)]
        t = n / sample_rate
        # Glottal pulse phase: a decaying burst each pitch period, filtered by the formants.
        phase = (n % period) / sample_rate
        value = 0.0
        for i, formant in enumerate(vowel):
            value += (0.6 / (i + 1)) * math.exp(-phase * 400.0 * (i + 1)) * math.sin(
                2 * math.pi * formant * t
            )
        envelope = math.sin(math.pi * pos / syllable)
        sample = max(-1.0, min(1.0, value * envelope * 0.8))
        frames += struct.pack("<h", int(sample * 32000))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(bytes(frames))
    return buffer.getvalue()


def load_wav(path: str | None) -> tuple[bytes, str]:
    """``(bytes, description)`` -- the operator's recording, or the synthesized fixture."""
    if path:
        with open(path, "rb") as handle:
            data = handle.read()
        if data[:4] != b"RIFF":
            raise ValueError(f"{path} is not a RIFF/WAV file")
        return data, f"operator-supplied {path} ({len(data):,} bytes)"
    data = synth_wav()
    return data, f"synthesized 2.0 s 16 kHz mono vowel fixture ({len(data):,} bytes, not intelligible speech)"
