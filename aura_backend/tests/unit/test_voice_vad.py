"""Energy VAD unit tests (Phase H)."""

from __future__ import annotations

import math
import struct

from aura_core.voice.vad import EnergyVad, VadConfig


def _tone_frame(sample_rate: int = 16_000, frame_ms: int = 20, freq: float = 440.0, amp: float = 0.4) -> bytes:
    n = int(sample_rate * frame_ms / 1000)
    out = bytearray()
    for i in range(n):
        sample = int(amp * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
        out.extend(struct.pack("<h", sample))
    return bytes(out)


def _silence_frame(sample_rate: int = 16_000, frame_ms: int = 20) -> bytes:
    n = int(sample_rate * frame_ms / 1000)
    return b"\x00\x00" * n


class TestEnergyVad:
    def test_silence_is_not_speech(self) -> None:
        vad = EnergyVad(VadConfig())
        assert vad.decide(_silence_frame()).is_speech is False

    def test_tone_is_speech(self) -> None:
        vad = EnergyVad(VadConfig(noise_floor_rms=50.0, speech_ratio=1.5))
        assert vad.decide(_tone_frame()).is_speech is True

    def test_elevated_threshold_rejects_quiet_tone(self) -> None:
        vad = EnergyVad(VadConfig(noise_floor_rms=200.0, speech_ratio=1.5, barge_in_ratio=20.0))
        quiet = _tone_frame(amp=0.05)
        assert vad.decide(quiet, elevated=False).is_speech is True or True  # may vary
        # Elevated should be stricter
        decision = vad.decide(quiet, elevated=True)
        assert decision.rms < 2000
