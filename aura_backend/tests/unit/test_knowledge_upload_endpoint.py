"""``POST /api/v3/knowledge/documents/upload`` (Phase F1): the HTTP entry point that was missing
to reach ``extract_pdf``/``apply_ocr`` -- both existed and were tested in isolation
(``test_document_extraction.py``), but nothing in the v3 router ever called them. These tests
drive the real route with real PDF bytes, the same ``container``-backed ``build_app`` pattern
``test_schema_readiness.py`` uses for endpoint tests.
"""

from __future__ import annotations

import io

import pytest

from aura_core.adapters.tesseract_ocr import tesseract_available
from aura_core.domain.identifiers import UserId


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("uploader"), email="u@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


def _text_pdf(text: str) -> bytes:
    """A PDF with a real text layer -- Pillow can only rasterise, so this one is built with the
    same hand-rolled minimal-PDF technique as test_document_extraction.py's ``_pdf`` helper."""
    from tests.unit.test_document_extraction import _pdf

    return _pdf([{"text": [text]}])


def _scanned_pdf(text: str) -> bytes:
    """A PDF with no text layer at all -- a raster image of ``text``, exactly what a real scanner
    produces. Built with Pillow's own PDF writer, which JPEG-encodes the page image."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (600, 200), color="white")
    ImageDraw.Draw(image).text((20, 80), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PDF")
    return buf.getvalue()


class TestUploadTextPdf:
    async def test_a_text_pdf_ingests_without_needing_ocr(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            response = client.post(
                "/api/v3/knowledge/documents/upload",
                files={"file": ("doc.pdf", _text_pdf("Refunds are processed within ten days."), "application/pdf")},
                data={"title": "refund-policy.pdf"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["created"] is True
        assert body["chunks"] >= 1
        assert body["unreadable_pages"] == []
        assert body["pages"] == 1

    async def test_ingested_text_is_actually_searchable(self, container) -> None:
        await container.startup()
        with _client(container) as client:
            client.post(
                "/api/v3/knowledge/documents/upload",
                files={"file": ("doc.pdf", _text_pdf("The quarterly report ships every March."), "application/pdf")},
                data={"title": "report.pdf"},
            )
            search = client.post(
                "/api/v3/knowledge/search", json={"query": "when does the quarterly report ship"},
            )
        assert search.status_code == 200
        results = search.json()["results"]
        assert any("March" in r["content"] for r in results)


class TestUploadOversized:
    async def test_a_file_over_the_configured_limit_is_rejected(self, container) -> None:
        import dataclasses

        await container.startup()
        container.settings = dataclasses.replace(
            container.settings,
            knowledge=dataclasses.replace(container.settings.knowledge, max_document_bytes=100),
        )
        with _client(container) as client:
            response = client.post(
                "/api/v3/knowledge/documents/upload",
                files={"file": ("doc.pdf", _text_pdf("x" * 500), "application/pdf")},
                data={"title": "too-big.pdf"},
            )
        assert response.status_code == 422
        assert response.json()["error"] == "validation_error"


@pytest.mark.skipif(
    not tesseract_available(), reason="tesseract binary not on PATH in this environment"
)
class TestUploadScannedPdf:
    async def test_a_scanned_pdf_is_ocrd_and_indexed(self, container) -> None:
        await container.startup()
        assert container.ocr_engine is not None
        with _client(container) as client:
            response = client.post(
                "/api/v3/knowledge/documents/upload",
                files={"file": ("scan.pdf", _scanned_pdf("Invoice total forty two dollars"), "application/pdf")},
                data={"title": "scan.pdf"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["unreadable_pages"] == []
        assert body["ocr_used"] is not None and body["ocr_used"].startswith("tesseract-")

        with _client(container) as client:
            search = client.post(
                "/api/v3/knowledge/search", json={"query": "invoice total forty two dollars"},
            )
        results = search.json()["results"]
        assert any("forty" in r["content"].lower() for r in results)


class TestUploadWithoutOcrConfigured:
    async def test_a_scanned_pdf_without_ocr_stays_honestly_unreadable(self, container) -> None:
        await container.startup()
        container.ocr_engine = None
        with _client(container) as client:
            response = client.post(
                "/api/v3/knowledge/documents/upload",
                files={"file": ("scan.pdf", _scanned_pdf("Anything at all"), "application/pdf")},
                data={"title": "scan.pdf"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ocr_used"] is None
        assert body["pages"] == 1
        assert body["unreadable_pages"] == [1]
        assert body["created"] is True  # the honest "page is an image" note still indexes
