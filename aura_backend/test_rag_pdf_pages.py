"""The legacy PDF path must keep page numbers, not concatenate them away.

Part of the legacy (unittest) suite because `services/rag.py` is legacy code. The extraction itself
is covered thoroughly in `tests/unit/test_document_extraction.py`; what is tested here is the
*wiring* — that `ingest_pdf` passes blocks through and that each page's modality and page number
reach the Qdrant payload. Wiring is where the previous round of this work went wrong twice, both
times because the pieces were individually correct and connected to the wrong thing.

No Qdrant and no embedding model: the client is a recorder and the embedder is stubbed, because the
question is what payload gets built, not whether Qdrant stores it.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TESTING", "True")


def _pdf(pages):
    """A genuinely valid PDF. Mirrors the builder in tests/unit/test_document_extraction.py."""
    counter = [0]

    def nxt():
        counter[0] += 1
        return counter[0]

    catalog, pages_obj, font = nxt(), nxt(), nxt()
    body = {}
    page_ids = []

    for spec in pages:
        page_id, content_id = nxt(), nxt()
        parts = []
        resources = {}

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
    offsets = {}
    for number in sorted(body):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + body[number] + b"\nendobj\n"

    xref_at = len(out)
    total = max(body) + 1
    out += f"xref\n0 {total}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, total):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {total} /Root {catalog} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


class _Recorder:
    """Stands in for QdrantClient, recording what would have been written."""

    def __init__(self):
        self.points = []

    def upsert(self, collection_name, points):
        self.points.extend(points)


def _service():
    from services.rag import RAGService

    with patch.object(RAGService, "__init__", lambda self: None):
        service = RAGService()
    service.collection_name = "test_knowledge"
    service.client = _Recorder()
    service._is_local = True
    service._embedding_cache = {}
    service._verified_collections = {"test_knowledge"}
    # A fixed-width deterministic vector: this test is about payloads, not similarity.
    service._get_embedding = lambda text: [0.1] * 8
    service._ensure_collection_exists = lambda *a, **k: None
    return service


class TestLegacyPdfIngestion(unittest.TestCase):
    def test_each_page_keeps_its_number_and_modality(self):
        service = _service()
        data = _pdf(
            [
                {"text": ["The first page says something about refunds."]},
                {"text": ["The second page says something about delivery."]},
            ]
        )

        self.assertTrue(service.ingest_pdf("doc-1", data, {"title": "policy.pdf"}))

        payloads = [p.payload for p in service.client.points]
        self.assertTrue(payloads)
        pages = sorted({p["page"] for p in payloads})
        self.assertEqual([1, 2], pages)
        self.assertEqual({"pdf_page"}, {p["modality"] for p in payloads})
        # doc_id and the caller's metadata still survive — this is additive, not a replacement.
        self.assertEqual({"doc-1"}, {p["doc_id"] for p in payloads})
        self.assertEqual({"policy.pdf"}, {p["title"] for p in payloads})

    def test_chunk_index_stays_unique_across_pages(self):
        """Restarting the index per page would collide every page after the first."""
        service = _service()
        data = _pdf([{"text": [f"Page {n} content that is long enough to chunk."]} for n in (1, 2, 3)])

        service.ingest_pdf("doc-2", data, {})

        indexes = [p.payload["chunk_index"] for p in service.client.points]
        self.assertEqual(len(indexes), len(set(indexes)))
        self.assertEqual(list(range(len(indexes))), sorted(indexes))

    def test_a_scanned_page_is_indexed_as_an_honest_placeholder(self):
        service = _service()
        data = _pdf([{"text": ["Readable first page of the contract."]}, {"image": True}])

        self.assertTrue(service.ingest_pdf("doc-3", data, {"title": "contract.pdf"}))

        by_modality = {p.payload["modality"]: p.payload for p in service.client.points}
        self.assertIn("image", by_modality)
        placeholder = by_modality["image"]
        self.assertEqual(2, placeholder["page"])
        self.assertTrue(placeholder["needs_ocr"])
        self.assertIn("Page 2", placeholder["content"])
        self.assertIn("not indexed", placeholder["content"])

    def test_a_pdf_with_nothing_readable_returns_false(self):
        service = _service()

        self.assertFalse(service.ingest_pdf("doc-4", _pdf([{}, {}]), {}))
        self.assertEqual([], service.client.points)

    def test_plain_text_ingestion_is_unchanged(self):
        """`segments` is optional, and omitting it must behave exactly as before."""
        service = _service()

        self.assertTrue(service.ingest_document("doc-5", "Some plain text content.", {"k": "v"}))

        payload = service.client.points[0].payload
        self.assertEqual("v", payload["k"])
        self.assertNotIn("modality", payload)
        self.assertNotIn("page", payload)

    def test_corrupt_bytes_are_reported_as_a_failure_not_an_empty_document(self):
        service = _service()

        self.assertFalse(service.ingest_pdf("doc-6", b"not a pdf", {}))
        self.assertEqual([], service.client.points)


if __name__ == "__main__":
    unittest.main()
