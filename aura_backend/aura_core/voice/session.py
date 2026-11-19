"""Live voice session orchestrator (Phase H engine).

Depends on a ``TurnStreamer`` protocol rather than the concrete ``AIKernel`` so tests can inject
an echo-backed stream without an adapter import.
"""

from __future__ import annotations

import re

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence

from ..domain.identifiers import CorrelationId, SessionId, UserId
from ..kernel.kernel import TurnRequest
from ..nlp import NlpEngine, normalize_for_turn
from .sentences import SentenceAccumulator
from .turn_taking import EndpointConfig, EndpointDetector, EndpointEvent
from .vad import EnergyVad, VadConfig, VoiceActivityDetector
from .wav_pcm import pcm16_mono_to_wav, wav_to_pcm16

__all__ = [
    "TurnStreamer",
    "LiveVoiceConfig",
    "LiveVoiceSession",
    "SessionState",
    "TurnRefused",
]


class TurnStreamer(Protocol):
    """Narrow protocol over ``AIKernel.stream`` for the voice engine."""

    def stream(self, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]: ...


class TurnRefused(Exception):
    """An admission hook declined a turn (rate limit, plan quota). ``frame`` is the ``error``
    frame to send instead of running it -- the same body the HTTP routes return (F7)."""

    def __init__(self, frame: Mapping[str, Any]) -> None:
        super().__init__(str(frame.get("error") or "refused"))
        self.frame: dict[str, Any] = {**frame, "type": "error"}


#: Admits one turn before it runs: returns the reservation to give back if it produces nothing
#: (``None`` when unmetered) and the turn to run, which it may narrow (a plan's capabilities).
#: Raises :class:`TurnRefused` to decline.
TurnAdmission = Callable[[TurnRequest], Awaitable[tuple[Any, TurnRequest]]]


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
    #: Largest single binary frame accepted on the socket (audit FSA-007). One second of
    #: 16 kHz s16le mono is 32 000 bytes; a well-behaved client sends 20-100 ms frames. The
    #: endpointer counts every frame as `frame_ms` regardless of its size, so without this a
    #: client could buffer ~750 arbitrarily large frames per utterance before MAX_UTTERANCE.
    max_frame_bytes: int = 32_000
    max_output_tokens: int = 400
    playback_sample_rate_hint: int = 22_050
    hold_tts_until_done: bool = True
    english_only_stt: bool = True


#: The language codes Whisper accepts (ISO-639-1 plus ``yue``, ``haw``, ``jw``): the only values a
#: start frame's ``language`` may take (audit FSA-059). Anything else used to reach the model as
#: typed.
WHISPER_LANGUAGES = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw "
    "he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my "
    "ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt "
    "uk ur uz vi yi yo yue zh".split()
)
_LANGUAGE_NAMES = {"english": "en", "arabic": "ar"}


def normalise_language(value: str | None) -> str | None:
    """The Whisper code for a client's language value, or ``None`` when it is not one.

    ``EN-US`` and ``en_GB`` fold to ``en``; ``english`` is accepted for the clients that sent it
    before codes were required. Anything not in :data:`WHISPER_LANGUAGES` is refused.
    """
    raw = (value or "en").strip().lower()
    if not raw:
        raw = "en"
    raw = _LANGUAGE_NAMES.get(raw, raw)
    code = re.split(r"[-_]", raw, maxsplit=1)[0]
    return code if code in WHISPER_LANGUAGES else None


class PcmFrameError(ValueError):
    """A binary frame the session refuses to process (audit FSA-007).

    Raised by :meth:`LiveVoiceSession.push_pcm`; the WebSocket endpoint closes the socket with
    4400 rather than let an oversized frame reach the VAD or the utterance buffer.
    """


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
    #: This session's own detector (one per session: detectors are stateful). ``None`` uses
    #: :class:`EnergyVad`; the container passes a neural one when ``AURA_VOICE_LIVE_VAD`` asks.
    vad: VoiceActivityDetector | None = None
    #: The turn's scope, as ``to_turn`` sets it for a typed turn (ADR-034): the team's corpus
    #: inside a team workspace (``None`` means ``user_id``), no personal memory there, and the
    #: signed token's origin (WP8).
    knowledge_owner: UserId | None = None
    use_memory: bool = True
    origin: str | None = None
    #: Run before every turn -- a committed utterance or a confirmation -- to rate-limit it and
    #: reserve it against the workspace's plan. ``release`` gives the reservation back when the
    #: turn fails, is cancelled or is refused before its first token (F7, the /chat/stream rule).
    admit: TurnAdmission | None = None
    release: Callable[[Any], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        vad_cfg = VadConfig(
            sample_rate=self.config.sample_rate,
            frame_ms=self.config.frame_ms,
        )
        self._vad: VoiceActivityDetector = self.vad or EnergyVad(vad_cfg)
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
        #: F-04: the approval ids of the calls this session's last turn held for confirmation.
        #: Confirming resumes exactly those (server-side state); a client cannot name tools.
        self._pending_approval_ids: tuple[str, ...] = ()

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

    @property
    def max_utterance_bytes(self) -> int:
        """Byte ceiling for one buffered utterance: `max_utterance_ms` of s16le mono audio.

        The endpointer bounds an utterance in *frames* (`frame_ms` each); this bounds it in
        bytes, so a client whose frames carry more than `frame_ms` of audio still cannot grow
        the buffer past what `max_utterance_ms` was meant to allow.
        """
        return int(self.config.sample_rate * 2 * self.config.max_utterance_ms / 1000)

    async def push_pcm(self, frame: bytes) -> None:
        if len(frame) > self.config.max_frame_bytes:
            raise PcmFrameError(
                f"pcm frame of {len(frame)} bytes exceeds the {self.config.max_frame_bytes}-byte "
                "limit"
            )
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
        if len(self._pcm_buf) >= self.max_utterance_bytes and event not in (
            EndpointEvent.SPEECH_ENDED,
            EndpointEvent.MAX_UTTERANCE,
        ):
            # Byte-level MAX_UTTERANCE: finish the turn on what is buffered instead of letting
            # the buffer outgrow the time ceiling the endpointer thinks it is enforcing.
            del self._pcm_buf[self.max_utterance_bytes:]
            self._endpoint.reset()
            event = EndpointEvent.MAX_UTTERANCE
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

    async def confirm(self, approval_ids: Sequence[str] | None = None) -> None:
        """Approve the calls this session's last turn held -- by approval id, never by name.

        ``approval_ids`` narrows the approval to some of them; an id this session was not
        given is ignored. ``None`` approves every call the held turn asked about (what the
        spoken prompt listed). The kernel then resumes that exact turn (F-04).
        """
        if self.state is not SessionState.AWAITING_CONFIRMATION:
            return
        pending = held = self._pending_approval_ids
        if approval_ids is not None:
            wanted = {str(i) for i in approval_ids}
            held = tuple(i for i in held if i in wanted)
        self._pending_approval_ids = ()
        if not held:
            self.state = SessionState.LISTENING
            await self._emit(
                {"type": "error", "error": "confirmation_invalid",
                 "message": "There is nothing pending to confirm."}
            )
            await self._emit_state()
            return
        if not await self._run_kernel_turn(message="continue", approval_ids=held):
            # Refused before it ran (rate limit, plan): the calls stay held, to confirm later.
            self._pending_approval_ids = pending

    async def reject(self) -> None:
        if self.state is not SessionState.AWAITING_CONFIRMATION:
            return
        self._pending_approval_ids = ()
        self.state = SessionState.LISTENING
        await self._emit_state()

    @property
    def stt_language(self) -> str:
        """The validated, normalised code handed to the STT engine (``en`` when unset)."""
        return normalise_language(self.language) or "en"

    def check_language(self) -> str | None:
        code = normalise_language(self.language)
        if code is None:
            return (
                f"{str(self.language)[:40]!r} is not a supported language code; send an "
                "ISO-639-1 code such as en or ar"
            )
        if self.config.english_only_stt and code != "en":
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
            transcript = await self.stt.transcribe(wav, language=self.stt_language)
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
        approval_ids: tuple[str, ...] = (),
    ) -> bool:
        """Run one turn. ``False`` when admission refused it (its error frame already sent)."""
        req = TurnRequest(
            message=message or "continue",
            user_id=self.user_id,
            knowledge_owner=self.knowledge_owner,
            session_id=self.session_id,
            correlation_id=CorrelationId.new(),
            use_memory=self.use_memory,
            capabilities=self.capabilities,
            approval_ids=approval_ids,
            max_output_tokens=self.config.max_output_tokens,
            route="voice_live",
            origin=self.origin,
        )
        reservation: Any = None
        if self.admit is not None:
            try:
                reservation, req = await self.admit(req)
            except TurnRefused as refused:
                await self._emit(refused.frame)
                return False
        await self._cancel_turn()
        self.state = SessionState.THINKING
        await self._emit_state()

        async def _runner() -> None:
            answered = False

            def on_answer() -> None:
                nonlocal answered
                answered = True

            try:
                await self._consume_stream(req, on_answer=on_answer)
            finally:
                # Counted once the first token reached the session (or the turn finished): a
                # turn that fails, is cancelled or barged in on before that gives it back.
                if not answered and reservation is not None and self.release is not None:
                    await self.release(reservation)

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
        return True

    async def _consume_stream(
        self, request: TurnRequest, *, on_answer: Callable[[], None] | None = None
    ) -> None:
        accumulator = SentenceAccumulator()
        token_buf: list[str] = []
        hold_tts = self.safety_guards_enabled and self.config.hold_tts_until_done

        async for frame in self.turn_streamer.stream(request):
            ftype = frame.get("type")
            if on_answer is not None and ftype in ("token", "done"):
                on_answer()
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
                pending_calls = [
                    p for p in (frame.get("pending_tool_calls") or ()) if isinstance(p, Mapping)
                ]
                self._pending_approval_ids = tuple(
                    str(p["approval_id"]) for p in pending_calls if p.get("approval_id")
                )
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
                            # The held calls with their arguments and approval ids, so the
                            # client can show what is being approved and approve by id.
                            "calls": [
                                {
                                    "approval_id": p.get("approval_id"),
                                    "call_id": p.get("call_id"),
                                    "tool": p.get("tool"),
                                    "arguments": dict(p.get("arguments") or {}),
                                }
                                for p in pending_calls
                            ],
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
