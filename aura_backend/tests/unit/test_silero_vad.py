"""Silero voice-activity detection for live voice (Phase V, GAP-020).

The energy detector counts any loud, low-pitched sound as speech: a fan or a mains hum held
near the mic starts an utterance and sends it to Whisper. Silero is a speech model. These
tests run the real ONNX model that faster-whisper ships, on real signals: no doubles.
"""

from __future__ import annotations

import asyncio
import math
import struct

import pytest

from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
from aura_core.adapters.silero_vad import SileroVad, build_silero_vad, silero_vad_available
from aura_core.domain.identifiers import SessionId, UserId
from aura_core.voice import LiveVoiceConfig, LiveVoiceSession
from aura_core.voice.vad import EnergyVad

pytestmark = pytest.mark.skipif(
    not silero_vad_available(), reason="onnxruntime or faster-whisper's Silero model missing"
)

RATE = 16_000
FRAME = 640  # 20 ms of 16 kHz s16le mono


def _hum(seconds: float = 2.0, hz: float = 150.0, amplitude: int = 4000) -> bytes:
    n = int(RATE * seconds)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * hz * i / RATE))) for i in range(n)
    )


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + FRAME] for i in range(0, len(pcm) - FRAME + 1, FRAME)]


def _speech() -> bytes:
    """A spoken sentence from espeak-ng, resampled to 16 kHz (skips without the binary)."""
    from aura_core.adapters.espeak_tts import EspeakTtsEngine, espeak_available
    from aura_core.voice.wav_pcm import wav_to_pcm16

    if not espeak_available():
        pytest.skip("espeak-ng binary not on PATH")
    wav = asyncio.run(EspeakTtsEngine().synthesize("The meeting starts at noon, be on time."))
    pcm, rate, _channels = wav_to_pcm16(wav)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    n = int(len(samples) * RATE / rate)
    out = bytearray()
    for i in range(n):  # linear resample; plenty for a detector
        pos = i * (len(samples) - 1) / max(n - 1, 1)
        lo = int(pos)
        hi = min(lo + 1, len(samples) - 1)
        out += struct.pack("<h", int(samples[lo] + (samples[hi] - samples[lo]) * (pos - lo)))
    return bytes(out)


def _silero(**kwargs) -> SileroVad:
    vad = build_silero_vad(
        sample_rate=RATE,
        threshold=kwargs.get("threshold", 0.5),
        barge_in_threshold=kwargs.get("barge_in_threshold", 0.7),
    )
    assert vad is not None
    return vad


class TestSileroHearsSpeechNotNoise:
    def test_a_steady_hum_is_speech_to_the_energy_detector_but_not_to_silero(self) -> None:
        frames = _frames(_hum())
        energy, silero = EnergyVad(), _silero()
        assert sum(energy.decide(f).is_speech for f in frames) > len(frames) // 2
        assert sum(silero.decide(f).is_speech for f in frames) == 0

    def test_silence_is_not_speech(self) -> None:
        silero = _silero()
        assert not any(silero.decide(f).is_speech for f in _frames(bytes(RATE * 2)))

    def test_a_spoken_sentence_is_speech(self) -> None:
        frames = _frames(_speech())
        silero = _silero()
        assert sum(silero.decide(f).is_speech for f in frames) > len(frames) // 2

    def test_the_elevated_bar_applies_while_aura_is_busy(self) -> None:
        frames = _frames(_speech())
        listening = _silero(threshold=0.05, barge_in_threshold=0.99)
        busy = _silero(threshold=0.05, barge_in_threshold=0.99)
        assert sum(listening.decide(f).is_speech for f in frames) > 0
        assert sum(busy.decide(f, elevated=True).is_speech for f in frames) < sum(
            listening.decide(f).is_speech for f in frames
        )

    def test_reset_forgets_the_last_score(self) -> None:
        silero = _silero()
        for f in _frames(_speech()):
            silero.decide(f)
        silero.reset()
        assert silero.probability == 0.0
        assert not silero.decide(bytes(FRAME)).is_speech

    def test_an_odd_length_frame_does_not_raise(self) -> None:
        silero = _silero()
        assert silero.decide(b"\x01\x02\x03").is_speech is False


class TestSileroIsOnlyBuiltWhereItCanServe:
    def test_a_capture_rate_other_than_16k_falls_back(self) -> None:
        assert build_silero_vad(sample_rate=48_000, threshold=0.5, barge_in_threshold=0.7) is None

    def test_each_session_gets_its_own_state(self) -> None:
        a, b = _silero(), _silero()
        for f in _frames(_speech()):
            a.decide(f)
        assert b.probability == 0.0


@pytest.mark.anyio
async def test_a_live_session_with_silero_does_not_start_an_utterance_on_a_hum() -> None:
    """The point of the change, end to end through the session: with the energy detector a
    held hum becomes an utterance; with Silero it never does."""

    class _NoTurns:
        async def stream(self, request):
            raise AssertionError("no turn should run")
            yield {}  # pragma: no cover

    def session(vad) -> LiveVoiceSession:
        return LiveVoiceSession(
            stt=InMemoryStt(),
            tts=InMemoryTts(),
            turn_streamer=_NoTurns(),
            user_id=UserId("u1"),
            session_id=SessionId("s1"),
            config=LiveVoiceConfig(min_speech_ms=240),
            vad=vad,
        )

    async def started(s: LiveVoiceSession) -> bool:
        await s.start()
        for frame in _frames(_hum(seconds=1.0)):
            await s.push_pcm(frame)
        seen = []
        while not s._outbound.empty():
            seen.append(s._outbound.get_nowait())
        return any(isinstance(e, dict) and e.get("type") == "speech_started" for e in seen)

    assert await started(session(None)) is True  # energy default
    assert await started(session(_silero())) is False
