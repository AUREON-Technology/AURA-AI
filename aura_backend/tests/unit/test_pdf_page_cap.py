"""PDF extraction is bounded and can run off the event loop (audit F40).

``extract_pdf`` parsed every page of whatever it was given, synchronously: an upload within the
byte limit but with tens of thousands of (tiny) pages held the gateway's event loop for as long as
pypdf took to walk them, stalling every other request on that worker. Two bounds now:

* a page cap, checked from the page count before any page is read, refused with a 413 that names
  the limit;
* ``extract_pdf_async``, which runs the same extraction in a worker thread.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time

import pytest

from aura_core.domain.errors import KnowledgeError
from aura_core.knowledge import documents
from aura_core.knowledge.documents import (
    MAX_PDF_PAGES,
    DocumentTooLargeError,
    extract_document,
    extract_pdf,
    extract_pdf_async,
)


def _pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class TestPageCap:
    def test_a_pdf_over_the_cap_is_refused_with_413_naming_the_limit(self) -> None:
        with pytest.raises(DocumentTooLargeError) as caught:
            extract_pdf(_pdf(5), max_pages=3)
        error = caught.value
        assert error.http_status == 413
        assert error.details == {"pages": 5, "limit_pages": 3}
        assert "3" in str(error)

    def test_it_is_still_a_knowledge_error_for_callers_that_record_one_per_document(self) -> None:
        # The connector path (`extract_document`) and the legacy `services/rag.py` catch
        # KnowledgeError per document; a too-long PDF must be recorded there, not crash the run.
        assert issubclass(DocumentTooLargeError, KnowledgeError)

    def test_a_pdf_at_the_cap_is_read(self) -> None:
        extraction = extract_pdf(_pdf(3), max_pages=3)
        assert extraction.pages == 3
        assert extraction.blank_pages == (1, 2, 3)

    def test_the_default_cap_applies_without_an_argument(self, monkeypatch) -> None:
        monkeypatch.setattr(documents, "MAX_PDF_PAGES", 2)
        with pytest.raises(DocumentTooLargeError):
            extract_pdf(_pdf(3))
        with pytest.raises(DocumentTooLargeError):
            extract_document(_pdf(3), format="pdf")

    def test_the_default_cap_is_generous(self) -> None:
        assert MAX_PDF_PAGES >= 500


class TestOffTheLoop:
    async def test_the_async_form_returns_the_same_extraction(self) -> None:
        data = _pdf(2)
        assert await extract_pdf_async(data, title="t") == extract_pdf(data, title="t")

    async def test_the_async_form_does_not_hold_the_event_loop(self, monkeypatch) -> None:
        loop_thread = threading.get_ident()
        seen: dict[str, int] = {}

        def slow_extract(data, **kwargs):
            seen["thread"] = threading.get_ident()
            time.sleep(0.3)
            return "done"

        monkeypatch.setattr(documents, "extract_pdf", slow_extract)
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            assert await extract_pdf_async(b"%PDF", title="t") == "done"
        finally:
            task.cancel()
        assert seen["thread"] != loop_thread
        assert ticks >= 5  # the loop kept running while the extraction slept

    async def test_the_cap_is_enforced_off_the_loop_too(self) -> None:
        with pytest.raises(DocumentTooLargeError):
            await extract_pdf_async(_pdf(4), max_pages=3)
