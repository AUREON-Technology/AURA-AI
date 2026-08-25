"""``faster-whisper`` implementation of the ``SpeechToText`` port (Phase F2).

Self-hosted, CPU-friendly speech recognition -- no vendor API, the platform's one governing
constraint. Runs entirely on CPU via CTranslate2; the "tiny.en" model is ~75MB, small enough to
ship alongside a deployment that already runs a 7B text generator.
"""

from __future__ import annotations

import asyncio
import io
import logging

from ..domain.errors import DependencyUnavailableError

logger = logging.getLogger(__name__)

__all__ = ["FasterWhisperEngine", "faster_whisper_available"]


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


class FasterWhisperEngine:
    """Loads its model lazily, on first :meth:`transcribe` call -- construction stays a cheap,
    network-free operation (ADR-001's "every connection is lazy"), matching every other adapter
    in this codebase that defers real work past ``__init__``. The first real call pays the
    one-time cost of pulling the model from the Hugging Face Hub if it is not already cached.
    """

    def __init__(self, *, model_size: str = "tiny.en", device: str = "cpu", compute_type: str = "int8") -> None:
        if not faster_whisper_available():
            raise DependencyUnavailableError(
                "FasterWhisperEngine requires the faster-whisper package", dependency="faster-whisper",
            )
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return f"faster-whisper-{self._model_size}"

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
            return WhisperModel(self._model_size, device=self._device, compute_type=self._compute_type)
        except Exception as exc:
            raise DependencyUnavailableError(
                f"could not load the faster-whisper model {self._model_size!r}",
                dependency="faster-whisper",
            ) from exc

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        model = await self._ensure_model()
        return await asyncio.to_thread(self._transcribe, model, audio, language)

    def _transcribe(self, model, audio: bytes, language: str | None) -> str:
        try:
            segments, _info = model.transcribe(io.BytesIO(audio), language=language)
            return " ".join(segment.text.strip() for segment in segments).strip()
        except Exception:
            logger.warning("faster-whisper transcription failed")
            raise
