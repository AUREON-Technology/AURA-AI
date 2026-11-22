"""Shared, safe file reading for the ``open`` tools.

Every tool in this folder that takes a *path* goes through :func:`read_source_file`, so they all
behave the same way on the same input:

* A **PDF** is read in full -- every page, none skipped, none truncated -- through the platform's own
  extractor (``knowledge.documents.extract_pdf``, which is ``pypdf`` page by page). Pages that are
  scans (images, no text layer) cannot be read without OCR; they are *named* in the result instead
  of being silently dropped, so a caller can never mistake "nothing found" for "nothing there".
* A text file is decoded as UTF-8 (falling back to Latin-1, which cannot fail).
* A binary file that is not a PDF is refused rather than fed to a parser that would crash on it
  (``ast.parse`` raises ``ValueError`` on NUL bytes before Python 3.12).
* An oversized file is refused with a clear error. It is never *partially* read: a scanner that
  looked at the first 5 MB and said "secure" would be worse than one that says "too large".
* With ``base_dir`` set, a path that resolves outside it (``..``, absolute paths, symlinks) is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

#: A source file larger than this is refused. Generous: no human-written module is near it.
MAX_TEXT_BYTES = 10 * 1024 * 1024
#: A PDF larger than this is refused. A 100 MB PDF held in memory twice (bytes + parsed) is already
#: heavy for a request-scoped tool.
MAX_PDF_BYTES = 100 * 1024 * 1024

_PDF_MAGIC = b"%PDF-"


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
        return info

    def warnings(self) -> list[str]:
        if not self.unreadable_pages:
            return []
        pages = ", ".join(str(p) for p in self.unreadable_pages)
        return [
            f"PDF page(s) {pages} are scanned images with no text layer and were NOT read "
            f"(OCR is required); results cover only the other pages."
        ]


def is_pdf_bytes(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(_PDF_MAGIC) or data.startswith(_PDF_MAGIC)


def read_source_bytes(data: bytes, *, name: str = "<bytes>") -> SourceRead:
    """Decode ``data`` as a PDF (all pages) or as text. Raises :class:`SourceReadError`."""
    if is_pdf_bytes(data):
        return _read_pdf(data, name)

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

    if not resolved.exists():
        raise SourceReadError(f"File not found: {file_path}")
    if not resolved.is_file():
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
    return read_source_bytes(data, name=str(file_path))


def _read_pdf(data: bytes, name: str) -> SourceRead:
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

    try:
        extraction = extract_pdf(data, title=Path(name).name or "the document")
    except AuraError as exc:
        raise SourceReadError(f"Could not parse {name} as a PDF ({exc}).") from exc

    # Unreadable-page placeholders are the extractor's note *about* a page, not the page's content.
    text = "\n\n".join(block.text for block in extraction.blocks if block.text and not block.needs_ocr)
    return SourceRead(
        text=text,
        kind="pdf",
        pages=extraction.pages,
        unreadable_pages=tuple(extraction.unreadable_pages),
        blank_pages=tuple(extraction.blank_pages),
    )
