"""Turn uploaded bytes into blocks that remember where they came from.

The defect this exists to fix is small to describe and impossible to work around downstream: the
legacy PDF path concatenated every page into one string. Page numbers were destroyed at ingestion,
so no retrieval strategy could ever answer "what does page 4 say" -- not because retrieval was
weak, but because the information had already been thrown away. Equally, a page that is a *scan*
extracted to nothing and was silently dropped, so a document could be indexed as five pages of
text when it was really twenty pages of images.

What this module produces instead is a sequence of :class:`ExtractedBlock`, each carrying:

* the text,
* a **modality** -- what kind of thing the text came from,
* the **page** it came from,

which survive into chunk metadata and from there into the vector payload
(``IndexManager._payload_for`` spreads scalar chunk metadata), which is what makes
:class:`~aura_core.rag.agentic.MultimodalRAG`'s ``modality`` filter select real content rather than
an empty set.

**What is deliberately not here.** No OCR and no vision model. An image-only page becomes a block
that *says so*, naming the page and the number of images on it. That is worth indexing: asked about
page 7 of a scanned contract, the system answers "page 7 is an image and OCR is not configured"
instead of answering confidently from page 6. See :mod:`aura_core.ports.documents` for the port an
OCR adapter would implement and :func:`apply_ocr` for where it would plug in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from ..domain.errors import KnowledgeError

__all__ = [
    "Modality",
    "ExtractedBlock",
    "DocumentExtraction",
    "extract_plain_text",
    "extract_html",
    "extract_pdf",
    "extract_document",
    "apply_ocr",
    "blocks_for_chunking",
    "pdf_support_available",
    "image_extraction_available",
]


class Modality:
    """What kind of source a block's text came from.

    Plain string constants rather than an enum: these values travel into a Qdrant payload, and
    ``_payload_for`` only forwards ``str | int | float | bool``. A ``str`` enum would survive that
    filter, but the constant is one fewer thing to think about at the boundary.

    ``MultimodalRAG.NON_TEXT_MODALITIES`` is the retrieval-side counterpart and must stay a superset
    of the values produced here -- asserted by ``test_multimodal_ingestion.py``.
    """

    TEXT = "text"          # a plain-text upload, no page structure
    PDF_PAGE = "pdf_page"  # a page whose text layer we read
    TABLE = "table"        # a page whose layout is tabular (heuristic, see _looks_tabular)
    IMAGE = "image"        # a page with images and no readable text layer


@dataclass(frozen=True, slots=True)
class ExtractedBlock:
    """One contiguous piece of a document, with its provenance intact."""

    text: str
    modality: str = Modality.TEXT
    page: int | None = None
    #: Raw bytes of the images on this page, populated only when ``extract_pdf(keep_images=True)``.
    #: Held separately from ``text`` so that a caller who does not intend to run OCR never pays the
    #: memory cost of carrying a scanned document's pixels around.
    images: tuple[bytes, ...] = field(default=(), repr=False)
    #: True when the page has content we could not read. This is the flag an operator greps for to
    #: find out how much of a corpus is invisible to search.
    needs_ocr: bool = False

    @property
    def chunk_metadata(self) -> dict[str, Any]:
        """The scalar metadata that should follow this block onto every chunk derived from it."""
        meta: dict[str, Any] = {"modality": self.modality}
        if self.page is not None:
            meta["page"] = self.page
        if self.needs_ocr:
            meta["needs_ocr"] = True
        return meta


@dataclass(frozen=True, slots=True)
class DocumentExtraction:
    """The result of reading a document, plus what could not be read."""

    blocks: tuple[ExtractedBlock, ...]
    pages: int = 0
    #: Pages with images but no readable text layer. Non-empty means part of this document is not
    #: searchable, and the caller should be able to say so.
    unreadable_pages: tuple[int, ...] = ()
    #: Pages with neither text nor images. Separated from ``unreadable_pages`` because a blank page
    #: is not a failure and should not be reported as one.
    blank_pages: tuple[int, ...] = ()

    @property
    def modalities(self) -> tuple[str, ...]:
        """Distinct modalities present, in first-seen order."""
        seen: dict[str, None] = {}
        for block in self.blocks:
            seen.setdefault(block.modality, None)
        return tuple(seen)

    @property
    def is_fully_readable(self) -> bool:
        return not self.unreadable_pages

    def text(self, *, separator: str = "\n\n") -> str:
        """The whole document as one string, for hashing, graph extraction and display.

        No page markers are injected. Markers would be the obvious way to preserve page numbers in
        the text, and they are wrong here: ``chunk_with_headings`` splits on headings, so a
        synthetic ``## Page 3`` would reparent the document's own headings underneath it and corrupt
        every ``heading_path``. Page provenance travels in block metadata instead, where nothing has
        to parse it back out.
        """
        return separator.join(block.text for block in self.blocks if block.text)


def pdf_support_available() -> bool:
    """Whether :func:`extract_pdf` can run in this process. ``pypdf`` is a declared dependency."""
    try:  # pragma: no cover - trivially environment-dependent
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def image_extraction_available() -> bool:
    """Whether embedded image *bytes* can be pulled out of a PDF.

    Separate from :func:`pdf_support_available` because the two have different requirements and,
    right now, different answers. ``pypdf`` counts image XObjects on a page with no help, which is
    all :func:`extract_pdf` needs to tell a scanned page from a blank one. Decoding those images to
    bytes calls into Pillow, which this platform does **not** declare -- so on a stock deployment
    this returns ``False``, ``keep_images=True`` yields no bytes, and :func:`apply_ocr` has nothing
    to work with.

    That is the honest state of the OCR path and it is checkable rather than buried: an operator who
    wants OCR needs Pillow *and* an :class:`~aura_core.ports.documents.OcrEngine` adapter, and this
    function tells them about the first half.
    """
    try:  # pragma: no cover - trivially environment-dependent
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def extract_plain_text(content: str, *, modality: str = Modality.TEXT) -> DocumentExtraction:
    """Wrap an already-textual upload so both ingestion paths look the same to callers."""
    text = content.strip()
    if not text:
        return DocumentExtraction(blocks=())
    return DocumentExtraction(blocks=(ExtractedBlock(text=text, modality=modality),))


#: Elements whose content is never prose: their text is dropped, not indexed.
_HTML_SKIPPED = frozenset({"script", "style", "template", "noscript", "head", "svg", "iframe", "object"})
#: Elements that end a line of text, so adjacent blocks do not run together into one word.
_HTML_BLOCKS = frozenset({
    "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "section", "article", "header",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dd", "dt", "hr", "main", "nav",
})


def extract_html(content: str) -> DocumentExtraction:
    """The visible text of an HTML document, one block, with scripts and styles removed.

    Stdlib ``html.parser`` only: nothing is fetched (no external entities, images or stylesheets)
    and nothing executes. Markup is a formatting hint here, never content -- an attribute such as
    ``title`` or ``alt`` is not text a reader sees, so it is not indexed either.
    """
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.skipping = 0

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag in _HTML_SKIPPED:
                self.skipping += 1
            elif tag in _HTML_BLOCKS:
                self.parts.append("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in _HTML_SKIPPED:
                self.skipping = max(0, self.skipping - 1)
            elif tag in _HTML_BLOCKS:
                self.parts.append("\n")

        def handle_data(self, data: str) -> None:
            if not self.skipping:
                self.parts.append(data)

    parser = _Text()
    parser.feed(content)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    text = "\n".join(line for line in lines if line)
    return extract_plain_text(text)


#: A page whose text layer yields fewer characters than this is treated as unread rather than as
#: nearly-empty. Scanned pages commonly extract to a handful of stray glyphs from a header stamp or
#: a page number, and treating those few characters as "the content of page 7" is worse than
#: admitting the page was not read: it produces a chunk that retrieval can match on and that says
#: nothing.
MIN_PAGE_CHARS = 16


def extract_pdf(
    data: bytes,
    *,
    title: str = "the document",
    keep_images: bool = False,
    min_page_chars: int = MIN_PAGE_CHARS,
) -> DocumentExtraction:
    """Read a PDF page by page, preserving page numbers and flagging what could not be read.

    ``keep_images`` carries each unreadable page's image bytes on its block so :func:`apply_ocr` can
    run later. Default false: without an OCR engine configured the bytes are pure memory cost, and a
    200-page scan is not a small amount of it. Note that it yields nothing unless
    :func:`image_extraction_available` -- counting a page's images needs only ``pypdf``, decoding
    them needs Pillow.

    Raises :class:`KnowledgeError` when ``pypdf`` is unavailable or the file cannot be parsed. Both
    are worth raising rather than returning empty: the legacy path caught every exception and
    returned ``False``, which made a missing dependency and a corrupt upload indistinguishable from
    a PDF that genuinely had no text in it.
    """
    try:
        import io

        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - requirements.txt declares pypdf
        raise KnowledgeError(
            "PDF extraction requires pypdf; install it or upload plain text",
            details={"dependency": "pypdf"},
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = list(reader.pages)
    except Exception as exc:
        raise KnowledgeError(
            "could not parse the uploaded PDF",
            details={"reason": type(exc).__name__},
        ) from exc

    blocks: list[ExtractedBlock] = []
    unreadable: list[int] = []
    blank: list[int] = []

    for number, page in enumerate(pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            # One malformed page must not lose the other 199. It is recorded as unreadable, which
            # is exactly what it is from the reader's point of view.
            raw = ""

        text = raw.strip()
        if len(text) >= min_page_chars:
            modality = Modality.TABLE if _looks_tabular(text) else Modality.PDF_PAGE
            blocks.append(ExtractedBlock(text=text, modality=modality, page=number))
            continue

        images = _page_images(page) if keep_images else ()
        image_count = len(images) if keep_images else _page_image_count(page)
        if image_count == 0:
            blank.append(number)
            continue

        unreadable.append(number)
        blocks.append(
            ExtractedBlock(
                text=_unreadable_page_note(number, len(pages), title, image_count),
                modality=Modality.IMAGE,
                page=number,
                images=images,
                needs_ocr=True,
            )
        )

    return DocumentExtraction(
        blocks=tuple(blocks),
        pages=len(pages),
        unreadable_pages=tuple(unreadable),
        blank_pages=tuple(blank),
    )


def extract_document(data: bytes, *, format: str, title: str = "the document") -> DocumentExtraction:
    """A connected system's document bytes as blocks, by declared format (AP10 project knowledge).

    ``format`` is one of ``domain.discovery.DOCUMENT_FORMATS``. Text formats must be UTF-8.
    Raises :class:`KnowledgeError` for anything unreadable, so the caller records it per document.
    """
    if format == "pdf":
        return extract_pdf(data, title=title)
    if format not in ("markdown", "text", "html"):
        raise KnowledgeError("unsupported document format", details={"format": format})
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KnowledgeError("the document is not UTF-8 text", details={"format": format}) from exc
    return extract_html(text) if format == "html" else extract_plain_text(text)


async def apply_ocr(
    extraction: DocumentExtraction,
    ocr: Any,
    *,
    min_chars: int = MIN_PAGE_CHARS,
) -> DocumentExtraction:
    """Replace unreadable-page blocks with their recognised text, where recognition succeeds.

    Pass an :class:`~aura_core.ports.documents.OcrEngine`. Requires the extraction to have been
    produced with ``keep_images=True`` **and** Pillow present (see
    :func:`image_extraction_available`). Blocks without image bytes are left exactly as they are
    rather than reported as recognition failures, because "we never had the pixels" is a different
    finding from "OCR could not read them", and an operator debugging an empty corpus needs to be
    able to tell which one happened.

    Per-page failure is contained: a page whose recognition raises keeps its unreadable note and
    stays in ``unreadable_pages``, so a partially-working OCR install degrades page by page instead
    of failing the upload. A page that recognises to nothing also keeps its note -- an image with no
    text in it is still an image the reader may ask about.
    """
    if not extraction.unreadable_pages:
        return extraction

    blocks: list[ExtractedBlock] = []
    still_unreadable: list[int] = []

    for block in extraction.blocks:
        if not block.needs_ocr:
            blocks.append(block)
            continue

        if not block.images:
            # No pixels: either ``keep_images`` was off, or decoding needed Pillow and it is
            # absent. Either way there is nothing to recognise, and the page stays unread.
            blocks.append(block)
            if block.page is not None:
                still_unreadable.append(block.page)
            continue

        recognised: list[str] = []
        for image in block.images:
            try:
                text = await ocr.image_text(image, page=block.page or 0)
            except Exception:
                # Per-image containment: one failed decode on a page with three figures should not
                # discard the two that worked.
                continue
            if text.strip():
                recognised.append(text.strip())

        joined = "\n".join(recognised).strip()
        if len(joined) >= min_chars:
            blocks.append(replace(block, text=joined, images=(), needs_ocr=False))
            continue

        # Nothing usable came back. Keep the honest note, drop the pixels -- a second attempt on the
        # same bytes fails the same way -- and leave the page on the unreadable list.
        blocks.append(replace(block, images=()))
        if block.page is not None:
            still_unreadable.append(block.page)

    return DocumentExtraction(
        blocks=tuple(blocks),
        pages=extraction.pages,
        unreadable_pages=tuple(sorted(set(still_unreadable))),
        blank_pages=extraction.blank_pages,
    )


# -- heuristics -------------------------------------------------------------------------------

#: Two or more consecutive spaces between non-space characters, which is how a PDF text layer
#: renders a column gap. The trailing character is a *lookahead* deliberately: matching it would
#: consume it, so ``"Widget  2  4.00"`` would report one gap instead of two -- the second gap has no
#: preceding non-space left to match against. The first version of this counted exactly that way and
#: no table was ever detected, while every test that only checked prose still passed.
_COLUMN_GAP = re.compile(r"\S {2,}(?=\S)")


def _looks_tabular(text: str) -> bool:
    """Whether a page's extracted text reads as a table rather than as prose.

    Deliberately conservative. The cost of a false positive is real: a page tagged ``table`` is
    still retrieved by ``simple`` and still tagged non-text for ``multimodal``, so mislabelling
    prose as a table makes a media-flavoured query surface an ordinary paragraph. Requiring a
    majority of lines to show at least two column gaps means a paragraph with one wide space in it
    does not qualify.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    gapped = sum(1 for line in lines if len(_COLUMN_GAP.findall(line)) >= 2)
    return gapped / len(lines) >= 0.6


def _page_image_count(page: Any) -> int:
    try:
        return len(page.images)
    except Exception:
        # `page.images` decodes embedded streams and can raise on damaged files. Reporting zero
        # sends the page to `blank_pages`, which is the safer wrong answer: it does not claim the
        # document contains unread content that a reader might go looking for.
        return 0


def _page_images(page: Any) -> tuple[bytes, ...]:
    try:
        return tuple(image.data for image in page.images)
    except Exception:
        return ()


def _unreadable_page_note(page: int, total: int, title: str, images: int) -> str:
    """The text indexed in place of a page we could not read.

    Written to be *findable and truthful* rather than to look like content. It names the page, so
    "what is on page 7" retrieves it; it says the page is an image, so the model grounds a refusal
    rather than an answer; and it says OCR is not configured, so the reader learns the fixable
    reason instead of concluding the document is empty.
    """
    plural = "image" if images == 1 else "images"
    return (
        f"[Page {page} of {total} in {title} could not be read as text. "
        f"It contains {images} {plural} and no text layer. "
        f"Optical character recognition is not configured on this deployment, "
        f"so the contents of this page are not indexed and cannot be quoted.]"
    )


def blocks_for_chunking(
    extraction: DocumentExtraction,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """``(text, chunk_metadata)`` pairs, for callers that chunk each block separately.

    Chunking each block on its own is what keeps a chunk from straddling two pages and then
    reporting one page number for both halves.
    """
    return tuple(
        (block.text, block.chunk_metadata) for block in extraction.blocks if block.text
    )
