"""Ports for turning uploaded bytes into indexable text.

One port, deliberately: :class:`OcrEngine`. PDF text extraction is not a port, because it needs no
choice of implementation -- ``pypdf`` reads the text layer and there is nothing to swap. Optical
character recognition *is* a choice: Tesseract, PaddleOCR, a self-hosted vision model, or nothing at
all, and the sensible default on a box already running a 7B generator is nothing.

Stdlib-only, like every other port (ADR-001). The engine that uses this lives in
``knowledge/documents.py``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["OcrEngine"]


@runtime_checkable
class OcrEngine(Protocol):
    """Recognise text in an image.

    Async because every real implementation is either a subprocess or a model forward pass, and a
    synchronous one parks the event loop for the whole document
    (``test_ports_are_async`` enforces this for every port).

    **No production adapter ships with this platform.** That is a stated gap rather than an
    oversight -- see ``AURA_AI_FINAL_IMPLEMENTATION_REPORT.md`` section 11. The port exists so that
    the ingestion side is finished by *configuration* rather than by a rewrite: the extraction
    pipeline already separates image pages from text pages, already carries their page numbers, and
    already routes them through :func:`aura_core.knowledge.documents.apply_ocr`. Adding Tesseract is
    a class with one method.
    """

    @property
    def name(self) -> str:
        """Identifier for logs and traces -- e.g. ``"tesseract-5.3"``."""
        ...

    async def image_text(self, image: bytes, *, page: int) -> str:
        """Return the text recognised in ``image``, or ``""`` when there is none.

        ``page`` is passed for logging and error messages only; an implementation must not need it
        to do its work.

        Returning ``""`` and raising mean different things, and both are handled: empty means "this
        image contains no text", which is a legitimate answer about a photograph, while an exception
        means "recognition failed", which leaves the page marked as unread rather than as empty.
        Conflating them would let a broken OCR install silently convert every scanned page into a
        page with nothing on it.
        """
        ...
