"""LiveVoiceSession behaviour with in-memory speech adapters (Phase H)."""

from __future__ import annotations

import asyncio
import struct
from typing import Any, AsyncIterator, Mapping

import pytest

from aura_core.adapters.in_memory_speech import InMemoryStt, InMemoryTts
from aura_core.domain.identifiers import SessionId, UserId
from aura_core.nlp import NlpEngine
from aura_core.voice import LiveVoiceConfig, LiveVoiceSession
from aura_core.voice.wav_pcm import pcm16_mono_to_wav


class EchoStreamer:
    def __init__(self, frames: list[Mapping[str, Any]]) -> None:
        self._frames = frames

    async def stream(self, request: Any) -> AsyncIterator[Mapping[str, Any]]:
        for frame in self._frames:
            yield frame
            await asyncio.sleep(0)


def _loud_pcm(ms: int = 300, sample_rate: int = 16_000) -> bytes:
    import math

    n = int(sample_rate * ms / 1000)
    out = bytearray()
    for i in range(n):
        sample = int(0.5 * 32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
        out.extend(struct.pack("<h", sample))
    return bytes(out)


def _frames(pcm: bytes, frame_bytes: int = 640) -> list[bytes]:
    return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]


@pytest.mark.anyio
async def test_session_emits_ready_and_transcript_then_audio() -> None:
    pcm = _loud_pcm(400)
    wav = pcm16_mono_to_wav(pcm, 16_000)
    stt = InMemoryStt({wav: "Hello there."})
    tts = InMemoryTts(sample_rate=22_050)
    streamer = EchoStreamer(
        [
            {"type": "token", "text": "Hi. "},
            {"type": "token", "text": "World."},
            {"type": "done", "needs_confirmation": []},
        ]
    )
    session = LiveVoiceSession(
        stt=stt,
        tts=tts,
        turn_streamer=streamer,
        user_id=UserId("u1"),
        session_id=SessionId("s1"),
        nlp=NlpEngine(extract_entities=False),
        config=LiveVoiceConfig(
            min_speech_ms=40,
            silence_hangover_ms=40,
            hold_tts_until_done=True,
            english_only_stt=True,
        ),
        safety_guards_enabled=True,
    )
    await session.start()

    async def drain(n: int = 50) -> list[Any]:
        out: list[Any] = []
        for _ in range(n):
            try:
                item = await asyncio.wait_for(session._outbound.get(), timeout=0.5)
            except asyncio.TimeoutError:
                break
            out.append(item)
        return out

    # Collect ready/state first
    early = await drain(5)
    assert any(isinstance(x, dict) and x.get("type") == "ready" for x in early)

    for frame in _frames(pcm):
        await session.push_pcm(frame)
    # Hangover silence
    silence = b"\x00\x00" * 320
    for _ in range(4):
        await session.push_pcm(silence)

    # Allow turn task to run
    await asyncio.sleep(0.2)
    events = early + await drain(40)
    types = [e.get("type") for e in events if isinstance(e, dict)]
    assert "transcript" in types
    assert "done" in types or "audio_end" in types
    assert any(isinstance(e, (bytes, bytearray)) for e in events)
    await session.close()


@pytest.mark.anyio
async def test_confirmation_holds_answer_tts() -> None:
    streamer = EchoStreamer(
        [
            {"type": "token", "text": "I will create a task."},
            {"type": "done", "needs_confirmation": ["propose_task"]},
        ]
    )
    session = LiveVoiceSession(
        stt=InMemoryStt(default="do it"),
        tts=InMemoryTts(),
        turn_streamer=streamer,
        user_id=UserId("u1"),
        session_id=SessionId("s1"),
        nlp=NlpEngine(extract_entities=False),
        config=LiveVoiceConfig(hold_tts_until_done=True),
        safety_guards_enabled=True,
    )
    await session.start()
    # Drain ready
    while not session._outbound.empty():
        await session._outbound.get()

    await session._run_kernel_turn(message="create a task")
    events: list[Any] = []
    while not session._outbound.empty():
        events.append(await session._outbound.get())
    types = [e.get("type") for e in events if isinstance(e, dict)]
    assert "confirmation" in types
    assert session.state.value == "AWAITING_CONFIRMATION"
    await session.reject()
    assert session.state.value == "LISTENING"
    await session.close()


@pytest.mark.anyio
async def test_language_gate_rejects_arabic_on_english_only() -> None:
    session = LiveVoiceSession(
        stt=InMemoryStt(),
        tts=InMemoryTts(),
        turn_streamer=EchoStreamer([]),
        user_id=UserId("u1"),
        session_id=SessionId("s1"),
        language="ar",
        config=LiveVoiceConfig(english_only_stt=True),
    )
    assert session.check_language() is not None
