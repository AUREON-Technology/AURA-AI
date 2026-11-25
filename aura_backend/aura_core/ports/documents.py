"""Ports for turning uploaded bytes into indexable text.

One port, deliberately: :class:`OcrEngine`. PDF text extraction is not a port, because it needs no
choice of implementation -- ``pypdf`` reads the text layer and there is nothing to swap. Optical
character recognition *is* a choice: Tesseract, PaddleOCR, a self-hosted vision model, or nothing at
all, and the sensible default on a box already running a 7B generator is nothing.

Stdlib-only, like every other port (ADR-001). The engine that uses this lives in
``knowledge/documents.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "OcrBox",
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "OcrWord",
    "StructuredOcrEngine",
]


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


@dataclass(frozen=True, slots=True)
class OcrBox:
    """An axis-aligned pixel rectangle: ``left``/``top`` is the upper-left corner, measured from
    the upper-left of the image, in the pixel grid of the bytes that were recognised."""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class OcrWord:
    """One recognised word. ``confidence`` is 0-100 as the engine reports it, ``None`` when the
    engine gave no usable figure (never a sentinel such as ``-1``)."""

    text: str
    confidence: float | None
    box: OcrBox


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One text line: its words in the engine's reading order, which for a right-to-left script
    (Arabic) is *not* left-to-right pixel order and is deliberately not re-sorted. ``text`` is
    the words joined by single spaces, ``box`` the union of the word boxes, ``confidence`` the
    mean of the words' known confidences."""

    text: str
    words: tuple[OcrWord, ...]
    confidence: float | None
    box: OcrBox


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Structured recognition result for one image.

    ``mean_confidence`` is the mean over every word that has a confidence (0-100), or ``None``
    when the image has none -- an empty page has *no* confidence, which is not the same as a
    confidence of 0. ``image_width``/``image_height`` are the decoded pixel size, so a consumer
    can normalise the boxes.
    """

    text: str
    lines: tuple[OcrLine, ...]
    mean_confidence: float | None
    image_width: int = 0
    image_height: int = 0

    @property
    def words(self) -> tuple[OcrWord, ...]:
        """Every word of every line, in reading order."""
        return tuple(word for line in self.lines for word in line.words)


@runtime_checkable
class StructuredOcrEngine(OcrEngine, Protocol):
    """An :class:`OcrEngine` that can also report *where* and *how sure*.

    A second port rather than a change to :class:`OcrEngine`, so every existing implementer and
    test fake keeps satisfying the plain port; callers that want boxes or confidences check
    ``isinstance(engine, StructuredOcrEngine)``.
    """

    async def image_data(self, image: bytes, *, page: int) -> OcrResult:
        """Recognise ``image`` and return words, lines, confidences and bounding boxes.

        Same contract as :meth:`OcrEngine.image_text`: an image with no text yields a result with
        no lines (and ``mean_confidence=None``); a recognition failure raises.
        """
        ...
