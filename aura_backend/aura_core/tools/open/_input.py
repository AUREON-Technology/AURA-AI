"""Shared, safe file reading for the ``open`` tools.

Every tool in this folder that takes a *path* goes through :func:`read_source_file`, so they all
behave the same way on the same input:

* A **PDF** is read in full -- every page, none skipped, none truncated -- through the platform's own
  extractor (``knowledge.documents.extract_pdf``, which is ``pypdf`` page by page). Pages that are
  scans (images, no text layer) cannot be read without OCR; they are *named* in the result instead
  of being silently dropped, so a caller can never mistake "nothing found" for "nothing there".
* **Scanned pages are OCR'd when an engine is available.** The engine is the platform's
  ``OcrEngine`` *port* (``ports/documents.py``); this layer never imports an adapter. The container
  hands its engine over once with :func:`configure_ocr_engine` (or a caller passes ``ocr_engine=``),
  and only pages with no text layer are recognised. OCR text is approximate, so the result says which
  pages came from OCR; a page OCR could not read stays in ``unreadable_pages``.
* A text file is decoded as UTF-8 (falling back to Latin-1, which cannot fail).
* A binary file that is not a PDF is refused rather than fed to a parser that would crash on it
  (``ast.parse`` raises ``ValueError`` on NUL bytes before Python 3.12).
* An oversized file is refused with a clear error. It is never *partially* read: a scanner that
  looked at the first 5 MB and said "secure" would be worse than one that says "too large".
* With ``base_dir`` set, a path that resolves outside it (``..``, absolute paths, symlinks) is refused.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

#: A source file larger than this is refused. Generous: no human-written module is near it.
MAX_TEXT_BYTES = 10 * 1024 * 1024
#: A PDF larger than this is refused. A 100 MB PDF held in memory twice (bytes + parsed) is already
#: heavy for a request-scoped tool.
MAX_PDF_BYTES = 100 * 1024 * 1024
#: Pages read per PDF. The platform's own ingestion limit is 500; these tools are asked to read a
#: whole document, so the ceiling here is ten times that. Beyond it the file is refused with a clear
#: error rather than cut short.
MAX_PDF_PAGES = 5000

_PDF_MAGIC = b"%PDF-"

#: Wall-clock allowance for OCR of one document. OCR is the slowest thing these tools do (seconds
#: per page on CPU), so it gets a generous but bounded budget; it is never allowed to run unbounded.
DEFAULT_OCR_BUDGET_S = 300.0
MAX_OCR_BUDGET_S = 600.0

#: The ``OcrEngine`` the container configured, if any. ``None`` means "no OCR": scanned pages are
#: named as unread, exactly as before.
_OCR_ENGINE: Any = None


def configure_ocr_engine(engine: Any) -> None:
    """Install the platform's ``OcrEngine`` (the port, an object with ``async image_text``).

    Called once at start-up by the container with ``container.ocr_engine``. Passing ``None`` turns
    OCR off. A second call replaces the first.
    """
    global _OCR_ENGINE
    _OCR_ENGINE = engine


def get_ocr_engine() -> Any:
    return _OCR_ENGINE


class SourceReadError(Exception):
    """The input could not be read. ``str(exc)`` is safe to show the user."""


@dataclass(frozen=True)
class SourceRead:
    """The text of one input file, plus how much of it was actually read."""

    text: str
    kind: str  # "text" | "pdf"
    pages: Optional[int] = None
    unreadable_pages: Tuple[int, ...] = ()
    blank_pages: Tuple[int, ...] = ()
    #: Pages whose text came from OCR rather than from a text layer: approximate, so a scanner must
    #: not treat a clean OCR page as proof of absence.
    ocr_pages: Tuple[int, ...] = ()
    #: Set when OCR could not run (no engine, no Pillow, timed out) -- explains ``unreadable_pages``.
    ocr_note: Optional[str] = None

    @property
    def complete(self) -> bool:
        """True when nothing in the input was left unread."""
        return not self.unreadable_pages

    def describe(self) -> Dict[str, Any]:
        """JSON-safe coverage report for a tool result."""
        info: Dict[str, Any] = {"kind": self.kind, "complete": self.complete, "chars": len(self.text)}
        if self.kind == "pdf":
            info["pages"] = self.pages
            info["pages_read"] = (self.pages or 0) - len(self.unreadable_pages)
            info["unreadable_pages"] = list(self.unreadable_pages)
            info["blank_pages"] = list(self.blank_pages)
            info["ocr_pages"] = list(self.ocr_pages)
        return info

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.ocr_pages:
            pages = ", ".join(str(p) for p in self.ocr_pages)
            out.append(
                f"PDF page(s) {pages} are scanned images read by OCR; the text is approximate and may "
                f"contain recognition errors."
            )
        if self.unreadable_pages:
            pages = ", ".join(str(p) for p in self.unreadable_pages)
            why = self.ocr_note or "OCR is required and is not available"
            out.append(
                f"PDF page(s) {pages} are scanned images with no text layer and were NOT read "
                f"({why}); results cover only the other pages."
            )
        return out


def is_pdf_bytes(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(_PDF_MAGIC) or data.startswith(_PDF_MAGIC)


def read_source_bytes(
    data: bytes,
    *,
    name: str = "<bytes>",
    ocr_engine: Any = None,
    ocr_time_budget_s: float = DEFAULT_OCR_BUDGET_S,
) -> SourceRead:
    """Decode ``data`` as a PDF (all pages) or as text. Raises :class:`SourceReadError`.

    ``ocr_engine`` overrides the configured engine for this call; with neither, scanned pages are
    reported as unread. ``ocr_time_budget_s`` (clamped to 600) bounds the OCR step.
    """
    if is_pdf_bytes(data):
        return _read_pdf(data, name, ocr_engine, ocr_time_budget_s)

    if b"\x00" in data[:8192]:
        raise SourceReadError(
            f"{name} is a binary file (not text and not a PDF); refusing to parse it as source code."
        )
    try:
        return SourceRead(text=data.decode("utf-8-sig"), kind="text")
    except UnicodeDecodeError:
        return SourceRead(text=data.decode("latin-1"), kind="text")


def read_source_file(
    file_path: Union[str, Path],
    *,
    base_dir: Union[str, Path, None] = None,
    ocr_engine: Any = None,
    ocr_time_budget_s: float = DEFAULT_OCR_BUDGET_S,
) -> SourceRead:
    """Read ``file_path`` completely. Raises :class:`SourceReadError` with a user-safe message."""
    try:
        path = Path(file_path).expanduser()
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SourceReadError(f"Invalid path: {file_path}") from exc

    if base_dir is not None:
        root = Path(base_dir).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise SourceReadError(f"Path is outside the allowed directory: {file_path}") from None

    try:
        exists = resolved.exists()
        is_file = exists and resolved.is_file()
    except OSError as exc:  # e.g. ENAMETOOLONG, which Path.exists() raises instead of returning False
        raise SourceReadError(f"Invalid path: {exc.strerror or type(exc).__name__}") from exc
    if not exists:
        raise SourceReadError(f"File not found: {file_path}")
    if not is_file:
        raise SourceReadError(f"Path is not a file: {file_path}")

    try:
        size = resolved.stat().st_size
        with resolved.open("rb") as handle:
            head = handle.read(len(_PDF_MAGIC) + 1024)
            looks_pdf = is_pdf_bytes(head) or resolved.suffix.lower() == ".pdf"
            limit = MAX_PDF_BYTES if looks_pdf else MAX_TEXT_BYTES
            if size > limit:
                raise SourceReadError(
                    f"File is too large to read safely ({size} bytes; limit {limit}): {file_path}"
                )
            handle.seek(0)
            data = handle.read()
    except SourceReadError:
        raise
    except OSError as exc:
        raise SourceReadError(f"Failed to read file: {exc.strerror or type(exc).__name__}") from exc

    if resolved.suffix.lower() == ".pdf" and not is_pdf_bytes(data):
        raise SourceReadError(f"{file_path} has a .pdf extension but is not a valid PDF file.")
    return read_source_bytes(data, name=str(file_path), ocr_engine=ocr_engine, ocr_time_budget_s=ocr_time_budget_s)


def _read_pdf(data: bytes, name: str, ocr_engine: Any = None, ocr_time_budget_s: float = DEFAULT_OCR_BUDGET_S) -> SourceRead:
    try:
        from pypdf import PdfReader  # noqa: F401  (also what extract_pdf needs)

        from ...knowledge.documents import extract_pdf
        from ...domain.errors import AuraError
    except ImportError as exc:  # pragma: no cover - pypdf is a declared dependency
        raise SourceReadError("PDF support requires the 'pypdf' package, which is not installed.") from exc

    # An encrypted PDF yields empty pages from pypdf rather than an error, which would then be
    # reported as "blank". Say so up front instead.
    try:
        import io

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise SourceReadError(f"{name} is password-protected; it cannot be read without the password.")
    except SourceReadError:
        raise
    except Exception:
        # Not parseable at all: let extract_pdf produce the canonical error below.
        pass

    title = Path(name).name or "the document"
    try:
        # Pass 1, the platform's default, classifies each page: readable / scanned image / blank.
        strict = extract_pdf(data, title=title, max_pages=MAX_PDF_PAGES)
        # Pass 2 keeps short pages. The default treats a page with fewer than 16 characters and no
        # image as blank, so a last page that says only "PASSED" or "OK" would vanish from the text,
        # which is exactly the page a reader most wants.
        lenient = extract_pdf(data, title=title, max_pages=MAX_PDF_PAGES, min_page_chars=1)
    except AuraError as exc:
        raise SourceReadError(f"Could not read {name} as a PDF ({exc}).") from exc

    # Unreadable-page placeholders are the extractor's note *about* a page, not the page's content.
    page_text: Dict[int, str] = {
        block.page: block.text for block in lenient.blocks if block.text and not block.needs_ocr and block.page
    }
    unreadable = tuple(strict.unreadable_pages)
    ocr_pages: Tuple[int, ...] = ()
    ocr_note: Optional[str] = None

    engine = ocr_engine if ocr_engine is not None else _OCR_ENGINE
    if unreadable:
        if engine is None:
            ocr_note = "OCR is not configured"
        else:
            recovered, ocr_note = _ocr_pages(data, title, engine, set(unreadable), ocr_time_budget_s)
            for page, text in recovered.items():
                # The OCR text replaces any stray glyphs the text layer held (a page number, a stamp).
                page_text[page] = text
            ocr_pages = tuple(sorted(recovered))
            unreadable = tuple(p for p in unreadable if p not in recovered)

    # A scanned page that carries a stray glyph stays "unreadable" unless OCR recovered it: it has text
    # the second pass picked up, but its content is an image nobody has recognised.
    text = "\n\n".join(page_text[p] for p in sorted(page_text))
    blank = tuple(p for p in strict.blank_pages if p not in page_text)
    return SourceRead(
        text=text,
        kind="pdf",
        pages=strict.pages,
        unreadable_pages=unreadable,
        blank_pages=blank,
        ocr_pages=ocr_pages,
        ocr_note=ocr_note,
    )


def _ocr_pages(
    data: bytes, title: str, engine: Any, wanted: "set[int]", budget_s: float
) -> Tuple[Dict[int, str], Optional[str]]:
    """OCR the ``wanted`` scanned pages. Returns ``({page: text}, note)``; never raises.

    ``note`` explains why pages could not be recognised (no Pillow, timeout, engine failure); it is
    ``None`` when OCR ran for every wanted page that has image data. The OCR runs in its own thread
    with its own event loop, so this works the same from a plain call and from inside a running loop.
    """
    try:
        from ...knowledge.documents import apply_ocr, extract_pdf, image_extraction_available
    except ImportError:  # pragma: no cover
        return {}, "OCR support is not installed"
    if not image_extraction_available():
        return {}, "OCR needs Pillow to decode page images, and it is not installed"

    try:
        budget = float(budget_s)
    except (TypeError, ValueError):
        budget = DEFAULT_OCR_BUDGET_S
    budget = max(1.0, min(budget, MAX_OCR_BUDGET_S)) if budget == budget else DEFAULT_OCR_BUDGET_S

    async def run() -> Dict[int, str]:
        extraction = extract_pdf(data, title=title, keep_images=True, max_pages=MAX_PDF_PAGES)
        # min_chars=1: a scanned page that says only "PASSED" is still a page that was read.
        done = await apply_ocr(extraction, engine, min_chars=1)
        return {
            block.page: block.text
            for block in done.blocks
            if block.page in wanted and not block.needs_ocr and block.text
        }

    def in_thread() -> Dict[int, str]:
        return asyncio.run(asyncio.wait_for(run(), timeout=budget))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="open-tools-ocr")
    try:
        recovered = pool.submit(in_thread).result()
    except (asyncio.TimeoutError, concurrent.futures.TimeoutError):
        return {}, f"OCR did not finish within {int(budget)}s"
    except Exception as exc:
        return {}, f"OCR failed ({type(exc).__name__})"
    finally:
        pool.shutdown(wait=False)

    missed = sorted(wanted - set(recovered))
    return recovered, (f"OCR recognised no text on page(s) {', '.join(map(str, missed))}" if missed else None)
