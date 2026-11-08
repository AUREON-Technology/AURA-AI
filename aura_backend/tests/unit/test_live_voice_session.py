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


class _RecordingStreamer:
    """Holds one call on the first turn; records every request it is given."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def stream(self, request: Any) -> AsyncIterator[Mapping[str, Any]]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield {
                "type": "done",
                "needs_confirmation": ["propose_task"],
                "pending_tool_calls": [{
                    "call_id": "step_s1", "tool": "propose_task",
                    "arguments": {"title": "x"}, "approval_id": "apr.tok.n1",
                }],
            }
        else:
            yield {"type": "done", "needs_confirmation": []}


@pytest.mark.anyio
async def test_voice_confirms_by_the_held_approval_id_never_by_name() -> None:
    """F-04: a voice confirmation resumes the call this session is holding, by its approval id.
    Tool names a client sends authorise nothing, and an id it was not given is dropped."""
    streamer = _RecordingStreamer()
    session = LiveVoiceSession(
        stt=InMemoryStt(default="do it"), tts=InMemoryTts(), turn_streamer=streamer,
        user_id=UserId("u1"), session_id=SessionId("s1"),
        nlp=NlpEngine(extract_entities=False), config=LiveVoiceConfig(hold_tts_until_done=True),
    )
    await session.start()
    await session._run_kernel_turn(message="create a task")
    events: list[Any] = []
    while not session._outbound.empty():
        events.append(await session._outbound.get())
    (confirmation,) = [
        e for e in events if isinstance(e, dict) and e.get("type") == "confirmation"
    ]
    assert confirmation["calls"][0]["approval_id"] == "apr.tok.n1"
    assert confirmation["calls"][0]["arguments"] == {"title": "x"}

    await session.confirm(["propose_task", "apr.forged.id", "apr.tok.n1"])
    assert streamer.requests[-1].approval_ids == ("apr.tok.n1",)

    # One-shot on the session side too: nothing is left to approve.
    session.state = type(session.state).AWAITING_CONFIRMATION
    await session.confirm(None)
    assert len(streamer.requests) == 2
    await session.close()


def _idle_session(**config) -> LiveVoiceSession:
    return LiveVoiceSession(
        stt=InMemoryStt(default="hello"),
        tts=InMemoryTts(sample_rate=22_050),
        turn_streamer=EchoStreamer([{"type": "done", "needs_confirmation": []}]),
        user_id=UserId("u1"),
        session_id=SessionId("s1"),
        nlp=NlpEngine(),
        config=LiveVoiceConfig(**config),
    )


@pytest.mark.anyio
async def test_an_oversized_frame_is_refused_before_it_reaches_the_vad() -> None:
    """Audit FSA-007: one frame larger than `max_frame_bytes` is a protocol violation."""
    from aura_core.voice import PcmFrameError

    session = _idle_session()
    await session.start()
    with pytest.raises(PcmFrameError):
        await session.push_pcm(b"\0" * (session.config.max_frame_bytes + 2))
    # A frame at the limit is still audio.
    await session.push_pcm(b"\0" * session.config.max_frame_bytes)


@pytest.mark.anyio
async def test_the_utterance_buffer_is_capped_in_bytes_not_only_in_frames() -> None:
    """The endpointer counts every frame as `frame_ms`; a client sending 100 ms of audio per
    frame would otherwise buffer five times `max_utterance_ms` before MAX_UTTERANCE fired."""
    session = _idle_session(min_speech_ms=40, silence_hangover_ms=10_000, max_utterance_ms=1_000)
    await session.start()
    ceiling = session.max_utterance_bytes
    assert ceiling == 16_000 * 2  # one second of 16 kHz s16le mono

    # The energy VAD is tuned for 20 ms frames (its zero-crossing band would reject a 100 ms
    # sine outright), so stub it: every frame is speech. What is under test is the byte
    # ceiling, which must hold regardless of how the VAD classifies oversized frames.
    from aura_core.voice import VadDecision

    session._vad.decide = lambda frame, *, elevated=False: VadDecision(
        is_speech=True, rms=1000.0, zero_crossings=10
    )
    big_frame = _loud_pcm(100)  # 3 200 bytes, counted as 20 ms by the endpointer
    for frame in _frames(_loud_pcm(1_600), frame_bytes=len(big_frame)):
        await session.push_pcm(frame)
        assert len(session._pcm_buf) <= ceiling
    # The turn was begun on the capped buffer rather than waiting for the frame-count ceiling.
    async def _until_speech_ended() -> list:
        seen = []
        async for item in session.events():
            seen.append(item)
            if isinstance(item, dict) and item.get("type") == "speech_ended":
                return seen
        return seen

    events = await asyncio.wait_for(_until_speech_ended(), timeout=5.0)
    assert any(isinstance(e, dict) and e.get("type") == "speech_ended" for e in events)
    await session.close()
