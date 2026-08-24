"""Tesseract implementation of the ``OcrEngine`` port (Phase F1).

``aura_core/ports/documents.py``'s own docstring names this exact adapter as the missing piece:
"Adding Tesseract is a class with one method." Runs a real, self-hosted OCR engine -- no vendor
API, consistent with the platform's one governing constraint -- via ``pytesseract``'s subprocess
wrapper around the ``tesseract`` binary.
"""

from __future__ import annotations

import asyncio
import io
import logging

from ..domain.errors import DependencyUnavailableError

logger = logging.getLogger(__name__)

__all__ = ["TesseractOcrEngine", "tesseract_available"]


def tesseract_available() -> bool:
    """Whether the ``tesseract`` binary and its Python bindings are both present.

    Checked once at container build time so a missing binary is a clear "OCR not configured" at
    startup rather than a per-page exception during a user's first upload.
    """
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


class TesseractOcrEngine:
    def __init__(self, *, lang: str = "eng") -> None:
        try:
            import pytesseract
        except ImportError as exc:
            raise DependencyUnavailableError(
                "TesseractOcrEngine requires pytesseract and Pillow",
                dependency="pytesseract",
            ) from exc
        self._pytesseract = pytesseract
        self._lang = lang
        try:
            version = str(pytesseract.get_tesseract_version())
        except Exception as exc:
            raise DependencyUnavailableError(
                "the tesseract binary is not on PATH", dependency="tesseract",
            ) from exc
        self._name = f"tesseract-{version}"

    @property
    def name(self) -> str:
        return self._name

    async def image_text(self, image: bytes, *, page: int) -> str:
        """Recognise text in ``image``.

        ``pytesseract`` shells out to the ``tesseract`` binary synchronously; ``to_thread`` keeps
        that off the event loop, the same reasoning ``OcrEngine``'s docstring gives for why the
        port is async at all.
        """
        return await asyncio.to_thread(self._recognise, image, page)

    def _recognise(self, image: bytes, page: int) -> str:
        from PIL import Image

        try:
            with Image.open(io.BytesIO(image)) as pil_image:
                pil_image.load()
                return self._pytesseract.image_to_string(pil_image, lang=self._lang)
        except Exception:
            logger.warning("tesseract recognition failed", extra={"page": page})
            raise
