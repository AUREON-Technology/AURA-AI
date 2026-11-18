"""Ports for turning speech into text and text into speech (Phase F2).

Two ports, not one: recognition and synthesis are different capabilities with different
adapters -- a synthesizer needs no speech model, a transcription engine does -- and a deployment
may reasonably want one without the other (e.g. TTS for reading answers aloud on a device with no
microphone).

Stdlib-only, like every other port (ADR-001).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["LanguageAwareSpeechToText", "SpeechToText", "TextToSpeech"]


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
