"""WAV ↔ PCM helpers (Phase H)."""

from __future__ import annotations

import struct

from aura_core.voice.wav_pcm import pcm16_mono_to_wav, wav_to_pcm16


class TestWavPcm:
    def test_round_trip(self) -> None:
        pcm = struct.pack("<4h", 0, 1000, -1000, 0)
        wav = pcm16_mono_to_wav(pcm, 16_000)
        out, rate, channels = wav_to_pcm16(wav)
        assert out == pcm
        assert rate == 16_000
        assert channels == 1

    def test_playback_rate_can_differ(self) -> None:
        pcm = b"\x00\x00" * 10
        wav = pcm16_mono_to_wav(pcm, 22_050)
        _out, rate, _ch = wav_to_pcm16(wav)
        assert rate == 22_050
        # Capture clock is typically 16 kHz — must not assume equality.
        assert rate != 16_000

    def test_header_not_fixed_44(self) -> None:
        pcm = b"\x00\x00" * 8
        wav = pcm16_mono_to_wav(pcm, 16_000)
        # stdlib wave may write a standard header; decode must still work via wave module.
        out, rate, _ = wav_to_pcm16(wav)
        assert len(out) == len(pcm)
        assert rate == 16_000
