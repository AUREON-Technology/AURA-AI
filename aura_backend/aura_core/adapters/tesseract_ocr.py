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

from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.errors import DependencyUnavailableError
from ..ports.documents import OcrBox, OcrLine, OcrResult, OcrWord

logger = logging.getLogger(__name__)

__all__ = ["TesseractOcrEngine", "parse_tesseract_data", "tesseract_available"]


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


_WORD_LEVEL = 5  # tesseract's TSV hierarchy: 1 page, 2 block, 3 paragraph, 4 line, 5 word


def _confidence(raw: Any) -> float | None:
    """Tesseract's 0-100 word confidence, or ``None`` for its ``-1`` "no figure" sentinel."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return min(value, 100.0)


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _union(boxes: Sequence[OcrBox]) -> OcrBox:
    left = min(b.left for b in boxes)
    top = min(b.top for b in boxes)
    right = max(b.left + b.width for b in boxes)
    bottom = max(b.top + b.height for b in boxes)
    return OcrBox(left, top, right - left, bottom - top)


def parse_tesseract_data(
    data: Mapping[str, Sequence[Any]], *, image_width: int = 0, image_height: int = 0
) -> OcrResult:
    """Turn ``pytesseract.image_to_data(..., output_type=Output.DICT)`` into an :class:`OcrResult`.

    Only word-level rows (``level == 5``) with non-blank text are kept: the page/block/paragraph/
    line rows tesseract interleaves carry ``conf == -1`` and no text, and blank "words" are
    whitespace artefacts. A kept word whose own confidence is ``-1`` keeps its text with
    ``confidence=None`` rather than a fake number. Rows are consumed in the order tesseract emits
    them -- its reading order, which for Arabic runs right to left -- and consecutive words that
    share a (block, paragraph, line) triple form one line. Nothing is re-sorted by coordinates.
    """
    texts = data.get("text", ())
    lines: list[OcrLine] = []
    current_key: tuple[Any, Any, Any] | None = None
    current: list[OcrWord] = []

    def flush() -> None:
        if not current:
            return
        known = [w.confidence for w in current if w.confidence is not None]
        lines.append(
            OcrLine(
                text=" ".join(w.text for w in current),
                words=tuple(current),
                confidence=_mean(known),
                box=_union([w.box for w in current]),
            )
        )
        current.clear()

    for i, raw_text in enumerate(texts):
        if int(data["level"][i]) != _WORD_LEVEL:
            continue
        text = str(raw_text).strip()
        if not text:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key != current_key:
            flush()
            current_key = key
        current.append(
            OcrWord(
                text=text,
                confidence=_confidence(data["conf"][i]),
                box=OcrBox(
                    int(data["left"][i]),
                    int(data["top"][i]),
                    int(data["width"][i]),
                    int(data["height"][i]),
                ),
            )
        )
    flush()

    all_known = [
        w.confidence for line in lines for w in line.words if w.confidence is not None
    ]
    return OcrResult(
        text="\n".join(line.text for line in lines),
        lines=tuple(lines),
        mean_confidence=_mean(all_known),
        image_width=image_width,
        image_height=image_height,
    )


class TesseractOcrEngine:
    """``OcrEngine`` (plain text) and ``StructuredOcrEngine`` (words, confidences, boxes)."""

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
        self._lang = self._installed_subset(pytesseract, lang)

    @staticmethod
    def _installed_subset(pytesseract: object, lang: str) -> str:
        """``lang`` minus any language whose traineddata is not installed.

        The default is ``ara+eng`` (docs/plans/arabic-language_plan.md, decision 16), and the
        image installs ``tesseract-ocr-ara``; a host without the Arabic pack would otherwise fail
        *every* OCR call, English included, because Tesseract refuses the whole request. Missing
        languages are logged and dropped; if the installed list cannot be read, or none of the
        requested languages is installed, ``lang`` is kept as configured so the error surfaces
        where it belongs.
        """
        try:
            installed = set(pytesseract.get_languages(config=""))  # type: ignore[attr-defined]
        except Exception:
            return lang
        wanted = [code for code in lang.split("+") if code]
        usable = [code for code in wanted if code in installed]
        missing = [code for code in wanted if code not in installed]
        if missing and usable:
            logger.warning(
                "tesseract language data missing; OCR continues without it",
                extra={"missing": missing, "using": usable},
            )
            return "+".join(usable)
        return lang

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

    @property
    def lang(self) -> str:
        """The language string this engine recognises with by default (installed languages only)."""
        return self._lang

    def installed_languages(self) -> tuple[str, ...]:
        """Tesseract language codes whose data is installed (empty when that cannot be read)."""
        try:
            return tuple(sorted(set(self._pytesseract.get_languages(config=""))))
        except Exception:
            return ()

    def resolve_language(self, lang: str | None) -> str:
        """``lang`` (``"eng"``, ``"ara+eng"``) checked against what is installed, or the default.

        Raises :class:`ValueError` naming the missing codes and the installed ones, so a caller can
        tell the user what to ask for instead of surfacing tesseract's own failure.
        """
        if not lang:
            return self._lang
        wanted = [code for code in lang.split("+") if code]
        installed = self.installed_languages()
        missing = [code for code in wanted if installed and code not in installed]
        if not wanted or missing:
            raise ValueError(
                f"tesseract language data for {missing or [lang]} is not installed "
                f"(installed: {', '.join(installed) or 'unknown'})"
            )
        return "+".join(wanted)

    async def image_data(self, image: bytes, *, page: int, lang: str | None = None) -> OcrResult:
        """Recognise ``image`` and return per-word/per-line text, confidence (0-100, ``None``
        when unknown) and pixel bounding boxes, plus the page's mean confidence.

        Same off-the-event-loop treatment and the same failure behaviour as :meth:`image_text`:
        an undecodable image or a tesseract failure raises; an image with no text returns an
        empty result. ``lang`` (optional, beyond the port) overrides the engine's languages for this
        call; an uninstalled one raises :class:`ValueError`.
        """
        return await asyncio.to_thread(self._recognise_data, image, page, self.resolve_language(lang))

    def _recognise_data(self, image: bytes, page: int, lang: str | None = None) -> OcrResult:
        from PIL import Image

        try:
            with Image.open(io.BytesIO(image)) as pil_image:
                pil_image.load()
                data = self._pytesseract.image_to_data(
                    pil_image, lang=lang or self._lang, output_type=self._pytesseract.Output.DICT
                )
                width, height = pil_image.size
            return parse_tesseract_data(data, image_width=width, image_height=height)
        except Exception:
            logger.warning("tesseract structured recognition failed", extra={"page": page})
            raise
