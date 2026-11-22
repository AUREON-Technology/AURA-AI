"""Ports for turning speech into text and text into speech (Phase F2).

Two ports, not one: recognition and synthesis are different capabilities with different
adapters -- a synthesizer needs no speech model, a transcription engine does -- and a deployment
may reasonably want one without the other (e.g. TTS for reading answers aloud on a device with no
microphone).

Stdlib-only, like every other port (ADR-001).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "LanguageAwareSpeechToText",
    "SpeechToText",
    "TextToSpeech",
    "TimestampedSpeechToText",
    "Transcript",
    "TranscriptSegment",
    "TranscriptWord",
]


@runtime_checkable
class SpeechToText(Protocol):
    """Recognise speech in an audio clip."""

    @property
    def name(self) -> str:
        """Identifier for logs and traces -- e.g. ``"faster-whisper-tiny.en"``."""
        ...

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        """Return the recognised transcript, or ``""`` when the clip contains no speech.

        ``language`` is an ISO 639-1 hint (e.g. ``"en"``); ``None`` lets the engine detect it.

        Returning ``""`` and raising mean different things, the same distinction
        :meth:`~aura_core.ports.documents.OcrEngine.image_text` makes: empty means "no speech
        here", an exception means "recognition failed" -- conflating them would let a broken
        install silently turn every voice note into nothing.
        """
        ...


@runtime_checkable
class LanguageAwareSpeechToText(SpeechToText, Protocol):
    """A :class:`SpeechToText` that says which languages it can hear and which one it heard.

    Optional on top of the base port, so an engine that cannot report either still serves
    ``transcribe``. ``POST /api/v3/voice/transcribe`` uses it to refuse a language the
    configured model cannot recognise (an English-only Whisper given ``ar`` would otherwise
    return an English guess) and to tell the client which language a clip was in.
    """

    @property
    def multilingual(self) -> bool:
        """``False`` when the model recognises English only (Whisper's ``*.en`` sizes)."""
        ...

    async def transcribe_detect(
        self, audio: bytes, *, language: str | None = None
    ) -> tuple[str, str | None]:
        """Like :meth:`transcribe`, plus the language code used: ``language`` when given, else
        the one the engine detected, or ``None`` when it cannot say."""
        ...


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """One word with its time span in seconds from the start of the clip. ``confidence`` is the
    engine's word probability in 0-1, or ``None`` when not reported."""

    start: float
    end: float
    text: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One recognised stretch of speech.

    ``start``/``end`` are seconds from the start of the clip. ``confidence`` (0-1) is derived
    from the engine's own figures -- ``exp(avg_logprob)``, scaled by ``1 - no_speech_prob`` --
    and is ``None`` when the engine reports neither; the raw ``avg_logprob`` and
    ``no_speech_prob`` are kept so a caller can apply its own rule. ``words`` is empty unless
    word timestamps were requested *and* the engine reported them.

    ``speaker`` is always ``None`` today: faster-whisper does no speaker diarization (it cannot
    say who is talking), and labels are never invented. Filling it needs a separate diarization
    model (e.g. pyannote) run over the audio and merged by timestamp.
    """

    start: float
    end: float
    text: str
    confidence: float | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    words: tuple[TranscriptWord, ...] = ()
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    """A timestamped transcript. ``text`` is the segments' text joined by single spaces (what
    :meth:`SpeechToText.transcribe` returns); ``language`` is the code used (the hint, else the
    detected one, else ``None``); ``language_probability`` is the detector's confidence (0-1)
    when it ran; ``duration`` is the clip length in seconds when known."""

    text: str
    segments: tuple[TranscriptSegment, ...]
    language: str | None = None
    language_probability: float | None = None
    duration: float | None = None


@runtime_checkable
class TimestampedSpeechToText(SpeechToText, Protocol):
    """A :class:`SpeechToText` that also returns segment (and optionally word) timestamps.

    A second port so engines and fakes that only implement ``transcribe`` stay valid.
    """

    async def transcribe_segments(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        word_timestamps: bool = True,
    ) -> Transcript:
        """Like :meth:`transcribe`, but keep the time structure. A clip with no speech yields a
        transcript with ``text=""`` and no segments; a failure raises."""
        ...


@runtime_checkable
class TextToSpeech(Protocol):
    """Render text as speech."""

    @property
    def name(self) -> str:
        """Identifier for logs and traces -- e.g. ``"espeak-ng"``."""
        ...

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """Return WAV-encoded audio for ``text``. ``voice`` is engine-specific (e.g. an
        espeak-ng voice code like ``"en-us"``); ``None`` uses the engine's default."""
        ...
