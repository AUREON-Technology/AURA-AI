"""``TesseractOcrEngine``, the real ``OcrEngine`` adapter (Phase F1).

``aura_core/ports/documents.py``'s own docstring names this adapter as the missing piece and
``tests/unit/test_document_extraction.py``'s OCR tests already prove ``apply_ocr``'s logic against
a fake engine (deliberately, since Pillow is not a declared dependency there). This file is the
other half: a real image, a real ``tesseract`` subprocess, a real recognised string -- skipped
cleanly wherever the binary genuinely is not on PATH, exactly like ``image_extraction_available()``
already does for Pillow.
"""

from __future__ import annotations

import io

import pytest

from aura_core.adapters.tesseract_ocr import TesseractOcrEngine, tesseract_available
from aura_core.domain.errors import DependencyUnavailableError
from aura_core.ports.documents import OcrEngine

pytestmark = pytest.mark.skipif(
    not tesseract_available(), reason="tesseract binary not on PATH in this environment"
)


def _text_png(text: str) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (600, 100), color="white")
    ImageDraw.Draw(image).text((10, 35), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class TestTesseractOcrEngine:
    def test_satisfies_the_ocr_engine_port(self) -> None:
        assert isinstance(TesseractOcrEngine(), OcrEngine)

    async def test_recognises_real_text_in_a_real_image(self) -> None:
        engine = TesseractOcrEngine()
        text = await engine.image_text(_text_png("The quick brown fox"), page=1)
        assert "quick brown fox" in text.lower()

    async def test_a_blank_image_recognises_to_empty_or_near_empty(self) -> None:
        from PIL import Image

        blank = Image.new("RGB", (200, 100), color="white")
        buf = io.BytesIO()
        blank.save(buf, format="PNG")

        engine = TesseractOcrEngine()
        text = await engine.image_text(buf.getvalue(), page=1)
        assert text.strip() == ""

    async def test_garbage_bytes_raise_rather_than_return_empty(self) -> None:
        """Recognition failure and 'no text' are different findings -- apply_ocr's own tests
        depend on that distinction, and it starts here: a genuinely undecodable image must raise,
        not silently report an empty page."""
        engine = TesseractOcrEngine()
        with pytest.raises(Exception):
            await engine.image_text(b"not an image at all", page=1)

    def test_name_identifies_the_real_engine_and_version(self) -> None:
        engine = TesseractOcrEngine()
        assert engine.name.startswith("tesseract-")

    async def test_lang_is_configurable(self) -> None:
        engine = TesseractOcrEngine(lang="eng")
        text = await engine.image_text(_text_png("hello"), page=1)
        assert "hello" in text.lower()


class TestTesseractUnavailable:
    def test_missing_binary_raises_dependency_unavailable(self, monkeypatch) -> None:
        import pytesseract

        def _boom():
            raise pytesseract.TesseractNotFoundError()

        monkeypatch.setattr(pytesseract, "get_tesseract_version", _boom)
        with pytest.raises(DependencyUnavailableError):
            TesseractOcrEngine()


class TestRealPdfOcrRoundTrip:
    """The claim that matters end to end: a scanned-page PDF, extracted and OCR'd with the real
    engine, produces real readable text -- not just that the two pieces work in isolation."""

    async def test_a_pillow_rendered_scanned_pdf_is_recognised(self) -> None:
        from PIL import Image, ImageDraw

        from aura_core.knowledge.documents import apply_ocr, extract_pdf, image_extraction_available

        image = Image.new("RGB", (600, 200), color="white")
        ImageDraw.Draw(image).text((20, 80), "Invoice total forty two dollars", fill="black")
        buf = io.BytesIO()
        image.save(buf, format="PDF")

        assert image_extraction_available()
        extraction = extract_pdf(buf.getvalue(), title="scan.pdf", keep_images=True)
        assert extraction.unreadable_pages == (1,)

        result = await apply_ocr(extraction, TesseractOcrEngine())
        assert result.unreadable_pages == ()
        assert "forty" in result.blocks[0].text.lower()
        assert result.blocks[0].needs_ocr is False
