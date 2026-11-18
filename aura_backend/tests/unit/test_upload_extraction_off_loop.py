"""F40 at the route: ``POST /api/v3/knowledge/documents/upload`` parses the PDF off the event loop
and answers a PDF over the page cap with 413.

``extract_pdf`` was called synchronously from the async handler, so a long PDF held the worker's
event loop -- every other request on it waited. The route now awaits ``extract_pdf_async``
(stream B5), and the page cap's ``DocumentTooLargeError`` surfaces as its own 413.
"""

from __future__ import annotations

import asyncio
import io

from aura_core.domain.identifiers import UserId
from aura_core.knowledge import documents


def _client(container):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("uploader"), email="u@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


def _blank_pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _upload(client, data: bytes):
    return client.post(
        "/api/v3/knowledge/documents/upload",
        files={"file": ("doc.pdf", data, "application/pdf")},
        data={"title": "doc.pdf"},
    )


async def test_the_pdf_is_parsed_off_the_event_loop(container, monkeypatch) -> None:
    from tests.unit.test_document_extraction import _pdf

    seen: list[bool] = []
    original = documents.extract_pdf

    def recording(data, **kwargs):
        try:
            asyncio.get_running_loop()
            seen.append(True)   # called on the loop's own thread
        except RuntimeError:
            seen.append(False)  # a worker thread: no loop running here
        return original(data, **kwargs)

    monkeypatch.setattr(documents, "extract_pdf", recording)
    await container.startup()
    with _client(container) as client:
        response = _upload(client, _pdf([{"text": ["Refunds take ten days."]}]))
    assert response.status_code == 200, response.text
    assert seen == [False], "extract_pdf ran on the event loop"


async def test_a_pdf_over_the_page_cap_is_413_naming_the_limit(container) -> None:
    await container.startup()
    with _client(container) as client:
        response = _upload(client, _blank_pdf(documents.MAX_PDF_PAGES + 1))
    assert response.status_code == 413, response.text
    body = response.json()
    assert body["error"] == "document_too_large"
    assert body["details"]["limit_pages"] == documents.MAX_PDF_PAGES
