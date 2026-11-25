"""``faster-whisper`` implementation of the ``SpeechToText`` port (Phase F2).

Self-hosted, CPU-friendly speech recognition -- no vendor API, the platform's one governing
constraint. Runs entirely on CPU via CTranslate2; the "tiny.en" model is ~75MB, small enough to
ship alongside a deployment that already runs a 7B text generator.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
from typing import Any

from ..domain.errors import DependencyUnavailableError
from ..ports.speech import Transcript, TranscriptSegment, TranscriptWord

logger = logging.getLogger(__name__)

__all__ = ["FasterWhisperEngine", "faster_whisper_available", "map_transcript"]


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _segment_confidence(avg_logprob: float | None, no_speech_prob: float | None) -> float | None:
    """0-1: ``exp(avg_logprob)`` (the geometric-mean token probability) scaled by
    ``1 - no_speech_prob``. ``None`` when the engine reported neither figure."""
    if avg_logprob is None and no_speech_prob is None:
        return None
    value = math.exp(min(avg_logprob, 0.0)) if avg_logprob is not None else 1.0
    if no_speech_prob is not None:
        value *= 1.0 - min(max(no_speech_prob, 0.0), 1.0)
    return round(min(max(value, 0.0), 1.0), 4)


def map_transcript(segments: Any, info: Any, *, language: str | None) -> Transcript:
    """Map faster-whisper's ``(segments, info)`` into a :class:`Transcript`.

    ``segments`` is the engine's lazy iterable: it is consumed here, which is where decoding
    happens. Speaker labels are never produced (``speaker=None``): faster-whisper does no
    diarization, so who-spoke-when would need a separate model and is not guessed at.
    """
    mapped: list[TranscriptSegment] = []
    for seg in segments:
        start = _num(getattr(seg, "start", None)) or 0.0
        end = max(_num(getattr(seg, "end", None)) or start, start)
        avg_logprob = _num(getattr(seg, "avg_logprob", None))
        no_speech_prob = _num(getattr(seg, "no_speech_prob", None))
        words: list[TranscriptWord] = []
        for word in getattr(seg, "words", None) or ():
            w_start = _num(getattr(word, "start", None)) or 0.0
            words.append(
                TranscriptWord(
                    start=w_start,
                    end=max(_num(getattr(word, "end", None)) or w_start, w_start),
                    text=str(word.word).strip(),
                    confidence=_num(getattr(word, "probability", None)),
                )
            )
        mapped.append(
            TranscriptSegment(
                start=start,
                end=end,
                text=str(seg.text).strip(),
                confidence=_segment_confidence(avg_logprob, no_speech_prob),
                avg_logprob=avg_logprob,
                no_speech_prob=no_speech_prob,
                words=tuple(words),
                speaker=None,
            )
        )
    duration = _num(getattr(info, "duration", None))
    if duration is None and mapped:
        duration = mapped[-1].end
    return Transcript(
        text=" ".join(s.text for s in mapped if s.text).strip(),
        segments=tuple(mapped),
        language=language or getattr(info, "language", None) or None,
        language_probability=None if language else _num(getattr(info, "language_probability", None)),
        duration=duration,
    )


class FasterWhisperEngine:
    """Loads its model lazily, on first :meth:`transcribe` call -- construction stays a cheap,
    network-free operation (ADR-001's "every connection is lazy"), matching every other adapter
    in this codebase that defers real work past ``__init__``. The first real call pays the
    one-time cost of pulling the model from the Hugging Face Hub if it is not already cached.
    """

    def __init__(
        self,
        *,
        model_size: str = "tiny.en",
        device: str = "cpu",
        compute_type: str = "int8",
        revision: str = "",
    ) -> None:
        if not faster_whisper_available():
            raise DependencyUnavailableError(
                "FasterWhisperEngine requires the faster-whisper package", dependency="faster-whisper",
            )
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        # Hub commit of the weights (audit FSA-110); shown in `name`, so /health/ready reports it.
        self._revision = revision
        self._model = None
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        base = f"faster-whisper-{self._model_size}"
        return f"{base}@{self._revision}" if self._revision else base

    async def warm(self) -> None:
        """Load the model now (audit FSA-110): the container calls this at startup in production
        so the first live-voice turn of a fresh container does not pay the Hub download."""
        await self._ensure_model()

    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_model)
        return self._model

    def _load_model(self):
        from faster_whisper import WhisperModel

        try:
            return WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                revision=self._revision or None,
            )
        except Exception as exc:
            raise DependencyUnavailableError(
                f"could not load the faster-whisper model {self._model_size!r}",
                dependency="faster-whisper",
            ) from exc

    @property
    def multilingual(self) -> bool:
        """Whisper's ``*.en`` sizes recognise English only; every other size is multilingual."""
        return not self._model_size.endswith(".en")

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        text, _language = await self.transcribe_detect(audio, language=language)
        return text

    async def transcribe_detect(
        self, audio: bytes, *, language: str | None = None
    ) -> tuple[str, str | None]:
        model = await self._ensure_model()
        return await asyncio.to_thread(self._transcribe, model, audio, language)

    def _transcribe(self, model, audio: bytes, language: str | None) -> tuple[str, str | None]:
        try:
            segments, info = model.transcribe(io.BytesIO(audio), language=language)
            # `segments` is lazy: the decode happens while it is consumed, so join first.
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception:
            logger.warning("faster-whisper transcription failed")
            raise
        return text, (language or getattr(info, "language", None) or None)

    async def transcribe_segments(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        word_timestamps: bool = True,
    ) -> Transcript:
        """Like :meth:`transcribe`, but keeps segment (and, by default, word) timestamps, a
        confidence per segment, the language and the clip duration.

        Same lazy model load as every other call. No speaker labels: faster-whisper does not
        diarize, so every ``speaker`` is ``None`` (see :class:`TranscriptSegment`).
        """
        model = await self._ensure_model()
        return await asyncio.to_thread(self._transcribe_segments, model, audio, language, word_timestamps)

    def _transcribe_segments(
        self, model, audio: bytes, language: str | None, word_timestamps: bool
    ) -> Transcript:
        try:
            segments, info = model.transcribe(
                io.BytesIO(audio), language=language, word_timestamps=word_timestamps
            )
            return map_transcript(segments, info, language=language)
        except Exception:
            logger.warning("faster-whisper segment transcription failed")
            raise
