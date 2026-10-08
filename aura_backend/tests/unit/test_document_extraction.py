"""Extraction is tested against real PDFs read by real ``pypdf``.

No mock reader, no fake page objects. The last time I tested a component against a double I had
written myself, all five tests passed while the component was incapable of working -- the double
returned the shape I had assumed rather than the shape the library produces. So the PDFs here are
built byte by byte (``_pdf`` below) and handed to the actual library, which means a change in how
``pypdf`` extracts text fails these tests instead of quietly invalidating them.

``pypdf`` is a declared runtime dependency, so this file does not skip. Pillow is *not* declared,
and image-byte extraction needs it -- which is why the OCR tests construct blocks directly and say
so, rather than pretending the pixels are reachable on a stock deployment.
"""

from __future__ import annotations

import pytest

from aura_core.knowledge.documents import (
    DocumentExtraction,
    ExtractedBlock,
    Modality,
    apply_ocr,
    blocks_for_chunking,
    extract_pdf,
    extract_plain_text,
    image_extraction_available,
    pdf_support_available,
)


# -- a real PDF, assembled by hand ------------------------------------------------------------


def _pdf(pages: list[dict]) -> bytes:
    """Build a minimal but genuinely valid PDF.

    Each entry is ``{"text": [lines]}``, ``{"image": True}``, or ``{}`` for a blank page. Written
    out with a correct cross-reference table so ``pypdf`` parses it the normal way rather than
    falling back to its damaged-file recovery path -- otherwise these tests would be exercising the
    recovery path, which is not what production reads.
    """
    counter = [0]

    def nxt() -> int:
        counter[0] += 1
        return counter[0]

    catalog, pages_obj, font = nxt(), nxt(), nxt()
    body: dict[int, bytes] = {}
    page_ids: list[int] = []

    for spec in pages:
        page_id, content_id = nxt(), nxt()
        parts: list[str] = []
        resources: dict[str, str] = {}

        if spec.get("text"):
            y = 720
            parts.append("BT /F1 12 Tf")
            for line in spec["text"]:
                escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
                parts.append(f"1 0 0 1 72 {y} Tm ({escaped}) Tj")
                y -= 16
            parts.append("ET")
            resources["/Font"] = f"<< /F1 {font} 0 R >>"

        if spec.get("image"):
            image_id = nxt()
            body[image_id] = (
                b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n"
                b"\xff\nendstream"
            )
            parts.append("q 10 0 0 10 100 100 cm /Im0 Do Q")
            resources["/XObject"] = f"<< /Im0 {image_id} 0 R >>"

        stream = "\n".join(parts).encode("latin-1")
        body[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        joined = " ".join(f"{k} {v}" for k, v in resources.items())
        body[page_id] = (
            f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << {joined} >> /Contents {content_id} 0 R >>"
        ).encode()
        page_ids.append(page_id)

    body[catalog] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    body[pages_obj] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    body[font] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(body):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + body[number] + b"\nendobj\n"

    xref_at = len(out)
    total = max(body) + 1
    out += f"xref\n0 {total}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, total):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {total} /Root {catalog} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


PROSE = ["Hello world this is page one", "with two lines of ordinary prose."]
TABLE = [
    "Item      Qty      Price",
    "Widget      2      4.00",
    "Gadget      1      9.50",
    "Doohickey      3      1.25",
]


class TestEnvironment:
    def test_pdf_support_is_declared_and_present(self) -> None:
        # requirements.txt pins pypdf>=4.2.0. If this ever fails, `ingest_pdf` is dead in
        # production and the only symptom is a caught exception logged as a parse failure -- which
        # is exactly how the prometheus-client omission hid.
        assert pdf_support_available() is True

    def test_image_extraction_is_honestly_reported(self) -> None:
        # Pillow is deliberately not declared. This asserts the reporting, not the value: whichever
        # way it goes, `apply_ocr` behaves correspondingly and says which case it is in.
        assert isinstance(image_extraction_available(), bool)


class TestPlainText:
    def test_wraps_content_as_a_single_text_block(self) -> None:
        extraction = extract_plain_text("  some notes  ")
        assert extraction.blocks == (ExtractedBlock(text="some notes", modality=Modality.TEXT),)
        assert extraction.modalities == (Modality.TEXT,)
        assert extraction.is_fully_readable

    def test_empty_content_yields_no_blocks(self) -> None:
        assert extract_plain_text("   \n  ").blocks == ()


class TestPdfExtraction:
    def test_page_numbers_survive(self) -> None:
        """The defect this module exists to fix: pages used to be concatenated."""
        data = _pdf([{"text": PROSE}, {"text": ["A second page of text here."]}])
        extraction = extract_pdf(data)

        assert extraction.pages == 2
        assert [b.page for b in extraction.blocks] == [1, 2]
        assert all(b.modality == Modality.PDF_PAGE for b in extraction.blocks)
        assert "page one" in extraction.blocks[0].text
        assert "second page" in extraction.blocks[1].text

    def test_a_scanned_page_becomes_a_block_that_says_so(self) -> None:
        data = _pdf([{"text": PROSE}, {"image": True}])
        extraction = extract_pdf(data)

        assert extraction.unreadable_pages == (2,)
        assert extraction.is_fully_readable is False

        image_block = extraction.blocks[1]
        assert image_block.modality == Modality.IMAGE
        assert image_block.page == 2
        assert image_block.needs_ocr is True
        # The three things the note must contain for it to be worth indexing at all: the page, the
        # fact that it is an image, and the fixable reason it was not read.
        assert "Page 2" in image_block.text
        assert "image" in image_block.text
        assert "character recognition" in image_block.text.lower()

    def test_a_blank_page_is_not_reported_as_unreadable(self) -> None:
        """A blank page is not a failure, and reporting it as one sends an operator hunting."""
        data = _pdf([{"text": PROSE}, {}])
        extraction = extract_pdf(data)

        assert extraction.blank_pages == (2,)
        assert extraction.unreadable_pages == ()
        assert extraction.is_fully_readable is True
        assert len(extraction.blocks) == 1

    def test_tabular_layout_is_tagged_as_a_table(self) -> None:
        extraction = extract_pdf(_pdf([{"text": TABLE}]))
        assert extraction.blocks[0].modality == Modality.TABLE

    def test_prose_is_not_mistaken_for_a_table(self) -> None:
        """The false positive matters: a page tagged `table` is retrieved by `multimodal`."""
        prose = [
            "This is an ordinary paragraph of text that runs on",
            "for several lines without any columns in it at all,",
            "and it should not be mistaken for tabular content.",
        ]
        extraction = extract_pdf(_pdf([{"text": prose}]))
        assert extraction.blocks[0].modality == Modality.PDF_PAGE

    def test_one_wide_gap_does_not_make_a_table(self) -> None:
        # A single column gap per line is common in prose that was justified or centred. Requiring
        # two is what keeps this from firing on ordinary documents.
        lines = ["Chapter One      1", "Chapter Two      14", "Chapter Three      31"]
        extraction = extract_pdf(_pdf([{"text": lines}]))
        assert extraction.blocks[0].modality == Modality.PDF_PAGE

    def test_mixed_document_reports_every_modality(self) -> None:
        data = _pdf([{"text": PROSE}, {"text": TABLE}, {"image": True}, {}])
        extraction = extract_pdf(data)

        assert extraction.pages == 4
        assert set(extraction.modalities) == {Modality.PDF_PAGE, Modality.TABLE, Modality.IMAGE}
        assert extraction.unreadable_pages == (3,)
        assert extraction.blank_pages == (4,)
        assert [b.page for b in extraction.blocks] == [1, 2, 3]

    def test_text_joins_blocks_without_injecting_page_markers(self) -> None:
        """Markers would be the obvious approach and would corrupt every heading_path."""
        extraction = extract_pdf(_pdf([{"text": ["# Real Heading", "body text here"]}, {"text": PROSE}]))
        text = extraction.text()

        assert "# Real Heading" in text
        assert "## Page" not in text
        assert "Page 1 of" not in text

    def test_a_page_of_stray_glyphs_counts_as_unread(self) -> None:
        """Scanned pages often extract a page number or a header stamp and nothing else."""
        data = _pdf([{"text": ["7"], "image": True}])
        extraction = extract_pdf(data)

        assert extraction.unreadable_pages == (1,)
        assert extraction.blocks[0].needs_ocr is True

    def test_corrupt_bytes_raise_rather_than_returning_empty(self) -> None:
        from aura_core.domain.errors import KnowledgeError

        with pytest.raises(KnowledgeError):
            extract_pdf(b"this is not a PDF at all")

    def test_title_appears_in_the_unreadable_note(self) -> None:
        extraction = extract_pdf(_pdf([{"image": True}]), title="Invoice-2026.pdf")
        assert "Invoice-2026.pdf" in extraction.blocks[0].text


class TestChunkMetadata:
    def test_metadata_carries_modality_and_page(self) -> None:
        extraction = extract_pdf(_pdf([{"text": PROSE}, {"image": True}]))
        pairs = blocks_for_chunking(extraction)

        assert pairs[0][1] == {"modality": Modality.PDF_PAGE, "page": 1}
        assert pairs[1][1] == {"modality": Modality.IMAGE, "page": 2, "needs_ocr": True}

    def test_values_are_payload_safe(self) -> None:
        """`IndexManager._payload_for` forwards only str/int/float/bool. Anything else vanishes."""
        extraction = extract_pdf(_pdf([{"text": TABLE}, {"image": True}]))
        for _text, meta in blocks_for_chunking(extraction):
            for key, value in meta.items():
                assert isinstance(value, (str, int, float, bool)), f"{key}={value!r}"

    def test_modalities_are_a_subset_of_what_retrieval_filters_on(self) -> None:
        """Ingestion and retrieval must agree, or ingested media is unreachable."""
        from aura_core.rag.agentic import MultimodalRAG

        produced = {Modality.PDF_PAGE, Modality.TABLE, Modality.IMAGE}
        assert produced <= set(MultimodalRAG.NON_TEXT_MODALITIES)
        # TEXT must NOT be in the retrieval set, or the multimodal filter matches everything.
        assert Modality.TEXT not in MultimodalRAG.NON_TEXT_MODALITIES


class _Ocr:
    """An OCR engine that recognises whatever it is told to.

    Legitimate as a double in a way ``FakeGraph`` was not: this protocol has one method and is
    defined by `aura_core.ports.documents`, not by a third-party library whose real shape could
    differ from my assumption. There is no real implementation for it to disagree with.
    """

    name = "test-ocr"

    def __init__(self, *, text: str = "", raises: bool = False) -> None:
        self._text = text
        self._raises = raises
        self.calls: list[int] = []

    async def image_text(self, image: bytes, *, page: int) -> str:
        self.calls.append(page)
        if self._raises:
            raise RuntimeError("tesseract not found")
        return self._text


def _image_only_extraction(*, images: tuple[bytes, ...] = (b"\xff",)) -> DocumentExtraction:
    block = ExtractedBlock(
        text="[Page 1 of 1 could not be read as text.]",
        modality=Modality.IMAGE,
        page=1,
        images=images,
        needs_ocr=True,
    )
    return DocumentExtraction(blocks=(block,), pages=1, unreadable_pages=(1,))


class TestApplyOcr:
    def test_port_is_satisfied_by_the_double(self) -> None:
        from aura_core.ports.documents import OcrEngine

        assert isinstance(_Ocr(), OcrEngine)

    async def test_recognised_text_replaces_the_placeholder(self) -> None:
        ocr = _Ocr(text="TOTAL DUE 4,210.00 EUR payable within thirty days")
        result = await apply_ocr(_image_only_extraction(), ocr)

        block = result.blocks[0]
        assert "TOTAL DUE" in block.text
        assert block.needs_ocr is False
        assert block.images == ()          # pixels dropped once they are no longer needed
        assert result.unreadable_pages == ()
        assert ocr.calls == [1]

    async def test_failure_keeps_the_page_marked_unread(self) -> None:
        result = await apply_ocr(_image_only_extraction(), _Ocr(raises=True))

        assert result.unreadable_pages == (1,)
        assert result.blocks[0].needs_ocr is True
        assert "could not be read" in result.blocks[0].text

    async def test_recognising_nothing_is_not_recognising_an_empty_page(self) -> None:
        """An image with no text in it is still an image the reader may ask about."""
        result = await apply_ocr(_image_only_extraction(), _Ocr(text=""))

        assert result.unreadable_pages == (1,)
        assert result.blocks[0].needs_ocr is True

    async def test_too_little_text_does_not_count_as_read(self) -> None:
        result = await apply_ocr(_image_only_extraction(), _Ocr(text="7"))
        assert result.unreadable_pages == (1,)

    async def test_blocks_without_pixels_are_left_alone(self) -> None:
        """The stock deployment case: no Pillow, so extract_pdf kept no image bytes."""
        ocr = _Ocr(text="never called")
        result = await apply_ocr(_image_only_extraction(images=()), ocr)

        assert ocr.calls == []
        assert result.unreadable_pages == (1,)
        assert result.blocks[0].text == _image_only_extraction(images=()).blocks[0].text

    async def test_one_bad_image_does_not_discard_its_siblings(self) -> None:
        class Partial(_Ocr):
            async def image_text(self, image: bytes, *, page: int) -> str:
                self.calls.append(page)
                if image == b"bad":
                    raise RuntimeError("decode failed")
                return "the readable figure caption goes here"

        extraction = _image_only_extraction(images=(b"bad", b"good"))
        result = await apply_ocr(extraction, Partial())

        assert result.blocks[0].needs_ocr is False
        assert "readable figure caption" in result.blocks[0].text

    async def test_a_fully_readable_document_is_returned_untouched(self) -> None:
        extraction = extract_pdf(_pdf([{"text": PROSE}]))
        ocr = _Ocr(text="should not run")

        assert await apply_ocr(extraction, ocr) is extraction
        assert ocr.calls == []


class TestIngestionReachesRetrieval:
    """The claim that actually matters: a PDF ingested here is findable by modality there.

    Everything above tests extraction in isolation. These tests run the real ``KnowledgeEngine``
    against the real in-memory stores and the real ``MultimodalRAG``, because "modality is written
    into chunk metadata" and "``MultimodalRAG`` can filter on it" are two different claims and the
    second one is the one a user experiences.
    """

    PDF = _pdf(
        [
            {"text": ["Refund requests must be filed within thirty days of purchase."]},
            {"text": TABLE},
            {"image": True},
        ]
    )

    async def _ingest(self, knowledge, user):
        extraction = extract_pdf(self.PDF, title="policy.pdf")
        return await knowledge.ingest(
            segments=extraction.blocks,
            title="policy.pdf",
            owner_id=user,
        ), extraction

    async def test_page_and_modality_reach_the_vector_payload(
        self, knowledge, vectors, user
    ) -> None:
        result, extraction = await self._ingest(knowledge, user)
        assert result.chunks == len(extraction.blocks)

        payloads = [
            point.payload
            for index in vectors._indexes.values()
            for point in index.values()
        ]
        assert payloads, "nothing was indexed"

        by_modality: dict[str, list[int]] = {}
        for payload in payloads:
            # Both keys must survive `_payload_for`'s scalar filter, or the filter below is a no-op.
            assert "modality" in payload, payload
            by_modality.setdefault(payload["modality"], []).append(payload["page"])

        assert set(by_modality) == {Modality.PDF_PAGE, Modality.TABLE, Modality.IMAGE}
        assert by_modality[Modality.PDF_PAGE] == [1]
        assert by_modality[Modality.TABLE] == [2]
        assert by_modality[Modality.IMAGE] == [3]

    async def test_multimodal_retrieval_selects_extracted_content_and_nothing_else(
        self, knowledge, user
    ) -> None:
        """It no longer falls back, which is the behaviour change.

        Before extraction wrote a modality, this strategy found nothing on every corpus and took
        its text fallback every time. ``pdf_page`` is intentionally inside
        ``NON_TEXT_MODALITIES``: without it, "what does page 4 say" could not be answered at all,
        which is a more common question than "what does the diagram show".
        """
        from aura_core.rag import MultimodalRAG, RetrievalRequest, SimpleRAG

        # A plain-text upload alongside the PDF, so the filter has something to exclude.
        await knowledge.ingest(
            content="A plain text note about refunds that is not from any PDF.",
            title="note.txt",
            owner_id=user,
        )
        await self._ingest(knowledge, user)

        strategy = MultimodalRAG(SimpleRAG(knowledge))
        outcome = await strategy.retrieve(
            RetrievalRequest(query="the price table", owner_id=user, score_threshold=0.0)
        )

        modalities = {c.chunk.metadata.get("modality") for c in outcome.result.chunks}
        assert modalities, "multimodal retrieval returned nothing"
        assert modalities <= set(MultimodalRAG.NON_TEXT_MODALITIES)
        # The plain-text upload has no modality key at all, so it is filtered out. That is the
        # exclusion that makes this strategy different from `simple`.
        assert None not in modalities
        assert "note.txt" not in {c.chunk.metadata.get("title") for c in outcome.result.chunks}
        assert "fell back to text retrieval" not in outcome.trace.notes

    async def test_the_unreadable_page_is_retrievable_and_says_it_is_unreadable(
        self, knowledge, user
    ) -> None:
        """A question about the scanned page must reach an honest account of the gap."""
        from aura_core.rag import MultimodalRAG, RetrievalRequest, SimpleRAG

        await self._ingest(knowledge, user)
        strategy = MultimodalRAG(SimpleRAG(knowledge))

        outcome = await strategy.retrieve(
            RetrievalRequest(
                query="what is on the scanned image page", owner_id=user, score_threshold=0.0
            )
        )

        notes = " ".join(
            c.content for c in outcome.result.chunks if c.chunk.metadata.get("modality") == Modality.IMAGE
        )
        assert "Page 3" in notes
        assert "not indexed" in notes

    async def test_plain_text_ingestion_is_unchanged(self, knowledge, user) -> None:
        """The segments path is additive. Without it, nothing about ingestion moves."""
        result = await knowledge.ingest(
            content="A plain text upload with no page structure whatsoever.",
            title="notes.txt",
            owner_id=user,
        )
        assert result.created is True

        chunks = await knowledge._documents.chunks_for(result.document.id, result.document.version)
        assert chunks
        for chunk in chunks:
            assert "modality" not in chunk.metadata
            assert "page" not in chunk.metadata
