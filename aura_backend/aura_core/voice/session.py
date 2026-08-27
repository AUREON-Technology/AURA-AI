"""Live voice session orchestrator (Phase H engine).

Depends on a ``TurnStreamer`` protocol rather than the concrete ``AIKernel`` so tests can inject
an echo-backed stream without an adapter import.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence

from ..domain.identifiers import CorrelationId, SessionId, UserId
from ..kernel.kernel import TurnRequest
from ..nlp import NlpEngine, normalize_for_turn
from .sentences import SentenceAccumulator
from .turn_taking import EndpointConfig, EndpointDetector, EndpointEvent
from .vad import EnergyVad, VadConfig
from .wav_pcm import pcm16_mono_to_wav, wav_to_pcm16

__all__ = [
    "TurnStreamer",
    "LiveVoiceConfig",
    "LiveVoiceSession",
    "SessionState",
]


class TurnStreamer(Protocol):
    """Narrow protocol over ``AIKernel.stream`` for the voice engine."""

    def stream(self, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]: ...


class SessionState(str, Enum):
    CONNECTING = "CONNECTING"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class LiveVoiceConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    silence_hangover_ms: int = 600
    min_speech_ms: int = 240
    barge_in_ms: int = 280
    max_utterance_ms: int = 15_000
    max_output_tokens: int = 400
    playback_sample_rate_hint: int = 22_050
    hold_tts_until_done: bool = True
    english_only_stt: bool = True


@dataclass
class LiveVoiceSession:
    """Orchestrates VAD → STT → NLP → kernel → sentence TTS."""

    stt: Any
    tts: Any
    turn_streamer: TurnStreamer
    user_id: UserId
    session_id: SessionId
    nlp: NlpEngine | None = None
    config: LiveVoiceConfig = field(default_factory=LiveVoiceConfig)
    language: str = "en"
    voice: str | None = None
    capabilities: tuple[str, ...] = ()
    safety_guards_enabled: bool = True

    def __post_init__(self) -> None:
        vad_cfg = VadConfig(
            sample_rate=self.config.sample_rate,
            frame_ms=self.config.frame_ms,
        )
        self._vad = EnergyVad(vad_cfg)
        self._endpoint = EndpointDetector(
            EndpointConfig(
                frame_ms=self.config.frame_ms,
                min_speech_ms=self.config.min_speech_ms,
                silence_hangover_ms=self.config.silence_hangover_ms,
                barge_in_ms=self.config.barge_in_ms,
                max_utterance_ms=self.config.max_utterance_ms,
            )
        )
        self.state = SessionState.CONNECTING
        self._pcm_buf = bytearray()
        self._outbound: asyncio.Queue[Mapping[str, Any] | bytes] = asyncio.Queue()
        self._turn_task: asyncio.Task[None] | None = None
        self._playback_rate = self.config.playback_sample_rate_hint
        self._closed = False

    async def start(self) -> None:
        self.state = SessionState.LISTENING
        await self._emit(
            {
                "type": "ready",
                "capture": {
                    "sample_rate": self.config.sample_rate,
                    "channels": 1,
                    "format": "pcm_s16le",
                },
                "playback": {
                    "sample_rate": self._playback_rate,
                    "channels": 1,
                    "format": "pcm_s16le",
                },
            }
        )
        await self._emit_state()

    async def close(self) -> None:
        self._closed = True
        await self._cancel_turn()
        self.state = SessionState.CONNECTING

    async def events(self) -> AsyncIterator[Mapping[str, Any] | bytes]:
        while not self._closed:
            try:
                item = await asyncio.wait_for(self._outbound.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            yield item

    async def push_pcm(self, frame: bytes) -> None:
        if self._closed or self.state is SessionState.AWAITING_CONFIRMATION:
            return
        busy = self.state in (SessionState.THINKING, SessionState.SPEAKING)
        decision = self._vad.decide(frame, elevated=busy)
        event = self._endpoint.feed(decision.is_speech, session_busy=busy)
        if event is EndpointEvent.BARGE_IN:
            await self._handle_barge_in()
            return
        if event is EndpointEvent.SPEECH_STARTED:
            self._pcm_buf.clear()
            self._pcm_buf.extend(frame)
            await self._emit({"type": "speech_started"})
            return
        if self._endpoint.phase.value == "in_speech" or event in (
            EndpointEvent.SPEECH_ENDED,
            EndpointEvent.MAX_UTTERANCE,
        ):
            self._pcm_buf.extend(frame)
        if event in (EndpointEvent.SPEECH_ENDED, EndpointEvent.MAX_UTTERANCE):
            await self._emit({"type": "speech_ended"})
            await self._begin_turn(bytes(self._pcm_buf))
            self._pcm_buf.clear()

    async def commit(self) -> None:
        if self._pcm_buf:
            await self._emit({"type": "speech_ended"})
            await self._begin_turn(bytes(self._pcm_buf))
            self._pcm_buf.clear()
            self._endpoint.reset()

    async def barge_in(self) -> None:
        await self._handle_barge_in()

    async def confirm(self, tools: Sequence[str]) -> None:
        if self.state is not SessionState.AWAITING_CONFIRMATION:
            return
        await self._run_kernel_turn(
            message="continue",
            confirmed_tools=tuple(tools),
        )

    async def reject(self) -> None:
        if self.state is not SessionState.AWAITING_CONFIRMATION:
            return
        self.state = SessionState.LISTENING
        await self._emit_state()

    def check_language(self) -> str | None:
        lang = (self.language or "en").lower()
        if self.config.english_only_stt and lang not in ("en", "en-us", "en-gb", "english"):
            return (
                "live voice STT is English-only with the configured model; "
                "set a multilingual Whisper size to enable other languages"
            )
        return None

    async def _begin_turn(self, pcm: bytes) -> None:
        if not pcm:
            return
        err = self.check_language()
        if err:
            await self._emit({"type": "error", "error": "language_unsupported", "message": err})
            return
        wav = pcm16_mono_to_wav(pcm, self.config.sample_rate)
        try:
            transcript = await self.stt.transcribe(wav, language=self.language)
        except Exception as exc:
            await self._emit(
                {"type": "error", "error": "stt_failed", "message": type(exc).__name__}
            )
            return
        transcript = (transcript or "").strip()
        if not transcript:
            return
        text = await normalize_for_turn(transcript, engine=self.nlp)
        await self._emit({"type": "transcript", "text": text, "final": True})
        await self._run_kernel_turn(message=text)

    async def _run_kernel_turn(
        self,
        *,
        message: str = "",
        confirmed_tools: tuple[str, ...] = (),
    ) -> None:
        await self._cancel_turn()
        self.state = SessionState.THINKING
        await self._emit_state()

        async def _runner() -> None:
            req = TurnRequest(
                message=message or "continue",
                user_id=self.user_id,
                session_id=self.session_id,
                correlation_id=CorrelationId.new(),
                capabilities=self.capabilities,
                confirmed_tools=confirmed_tools,
                max_output_tokens=self.config.max_output_tokens,
                route="voice_live",
            )
            await self._consume_stream(req)

        self._turn_task = asyncio.create_task(_runner())
        try:
            await self._turn_task
        except asyncio.CancelledError:
            await self._emit({"type": "cancelled"})
        finally:
            self._turn_task = None
            if self.state is not SessionState.AWAITING_CONFIRMATION:
                self.state = SessionState.LISTENING
                await self._emit_state()

    async def _consume_stream(self, request: TurnRequest) -> None:
        accumulator = SentenceAccumulator()
        token_buf: list[str] = []
        hold_tts = self.safety_guards_enabled and self.config.hold_tts_until_done

        async for frame in self.turn_streamer.stream(request):
            ftype = frame.get("type")
            if ftype == "token":
                token = str(frame.get("text") or frame.get("token") or "")
                token_buf.append(token)
                await self._emit({"type": "token", "text": token})
                if not hold_tts:
                    for sentence in accumulator.feed(token):
                        await self._speak_sentence(sentence)
            elif ftype == "revision":
                await self._emit(dict(frame))
            elif ftype in ("citations", "step", "actions", "tool_calls"):
                await self._emit(dict(frame))
            elif ftype == "done":
                needs_confirmation = tuple(frame.get("needs_confirmation") or ())
                full_text = "".join(token_buf)
                if needs_confirmation:
                    confirm_line = (
                        "I need your confirmation before continuing: "
                        + ", ".join(needs_confirmation)
                    )
                    await self._speak_sentence(confirm_line)
                    await self._emit(
                        {
                            "type": "confirmation",
                            "tools": list(needs_confirmation),
                            "text": confirm_line,
                        }
                    )
                    await self._emit(dict(frame))
                    await self._emit({"type": "audio_end"})
                    self.state = SessionState.AWAITING_CONFIRMATION
                    await self._emit_state()
                    return
                if hold_tts:
                    held = SentenceAccumulator()
                    for sentence in held.feed(full_text):
                        await self._speak_sentence(sentence)
                    for sentence in held.flush():
                        await self._speak_sentence(sentence)
                else:
                    for sentence in accumulator.flush():
                        await self._speak_sentence(sentence)
                await self._emit(dict(frame))
                await self._emit({"type": "audio_end"})
            elif ftype == "error":
                await self._emit(dict(frame))
            else:
                await self._emit(dict(frame))

    async def _speak_sentence(self, sentence: str) -> None:
        if not sentence.strip():
            return
        self.state = SessionState.SPEAKING
        await self._emit_state()
        try:
            wav = await self.tts.synthesize(sentence, voice=self.voice)
        except Exception as exc:
            await self._emit(
                {"type": "error", "error": "tts_failed", "message": type(exc).__name__}
            )
            return
        try:
            pcm, rate, _channels = wav_to_pcm16(wav)
        except Exception as exc:
            await self._emit(
                {"type": "error", "error": "tts_decode_failed", "message": type(exc).__name__}
            )
            return
        if rate != self._playback_rate:
            self._playback_rate = rate
            await self._emit(
                {
                    "type": "playback",
                    "sample_rate": rate,
                    "channels": 1,
                    "format": "pcm_s16le",
                }
            )
        await self._emit(pcm)

    async def _handle_barge_in(self) -> None:
        await self._cancel_turn()
        self._pcm_buf.clear()
        self._endpoint.reset()
        self.state = SessionState.LISTENING
        await self._emit({"type": "cancelled"})
        await self._emit_state()

    async def _cancel_turn(self) -> None:
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def _emit(self, item: Mapping[str, Any] | bytes) -> None:
        await self._outbound.put(item)

    async def _emit_state(self) -> None:
        await self._emit({"type": "state", "state": self.state.value})
