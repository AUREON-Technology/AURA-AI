"""Word and PowerPoint documents as blocks that remember where in the document they came from.

The same idea as :mod:`aura_core.knowledge.documents` applies to Office files as to PDFs: flatten a
document to one string and the structure is gone before retrieval ever sees it. Here a DOCX becomes

* one **section** block per heading -- the heading line (as a Markdown ``#`` line, so the chunker's
  ``heading_path`` works) followed by the paragraphs under it, with the whole heading path
  (``"Chapter > Section"``) in ``section`` and the heading's level in ``heading_level``;
* one **table** block per table, with ``rows`` holding the cells and ``text`` the same rows one per
  line; a table sits between the text blocks around it, so reading order is block order.

and a PPTX becomes, per slide, a **slide** block (title plus body text), a **table** block per table
and a **notes** block for the speaker notes, every one carrying ``slide`` (and the same number as
``page``, so citations and page filters need no second code path for decks).

Modalities: text is :attr:`Modality.TEXT`, tables are :attr:`Modality.TABLE`, and a slide that is
only pictures is :attr:`Modality.IMAGE` with ``needs_ocr`` -- all values that already exist, so
``MultimodalRAG.NON_TEXT_MODALITIES`` is untouched. Text is returned exactly as stored in logical
order: Arabic and other right-to-left runs are never reordered, shaped or normalised.

Everything hostile-input related (zip bombs, entities, depth) is :mod:`aura_core.knowledge.ooxml`'s
job; this module only reads what it lets through.
"""

from __future__ import annotations

import re
from typing import Iterable

from .documents import DocumentExtraction, DocumentTooLargeError, ExtractedBlock, Modality
from .ooxml import DEFAULT_LIMITS, Node, OoxmlError, OoxmlLimits, Package

__all__ = ["extract_docx", "extract_pptx", "office_images", "MAX_PPTX_SLIDES"]

#: The most slides ``extract_pptx`` reads, the counterpart of ``MAX_PDF_PAGES``. The count comes
#: from the presentation part before any slide is parsed.
MAX_PPTX_SLIDES = 1000

#: Run, paragraph and table property subtrees: most of a Word file by element count, none of it text.
_DOCX_LEAF_TAGS = frozenset({"rPr", "tblPr", "tblGrid", "sectPr", "trPr", "tcPr", "rPrChange", "pPrChange",
                             "tblPrChange", "sectPrChange"})
#: Containers whose content is shown, so their children are walked as if they were in place.
_DOCX_TRANSPARENT = frozenset({"sdtContent", "customXml", "ins", "moveTo", "smartTag", "hyperlink", "fldSimple",
                               "r", "Choice", "AlternateContent", "txbxContent", "drawing", "pict", "object",
                               "sdt", "bdo", "dir", "p"})
#: Content that is deleted (tracked changes) or an alternate rendering of content shown elsewhere.
_DOCX_SKIPPED = frozenset({"del", "moveFrom", "delText", "instrText", "Fallback", "pPr", "rPr"})

_HEADING_STYLE = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)


# -- DOCX --------------------------------------------------------------------------------------


def _style_levels(package: Package, document_part: str) -> dict[str, int]:
    """Style id -> heading level (1 = top) for every style Word treats as a heading or title."""
    part = next((r.target for r in package.relationships(document_part) if r.type == "styles"), "word/styles.xml")
    levels: dict[str, int] = {}
    if not package.has(part):
        return levels
    root = package.tree(part, leaf_tags=frozenset({"rPr", "tblPr", "tcPr", "trPr"}))
    based: dict[str, str] = {}
    own: dict[str, int] = {}
    for style in root.children:
        if style.tag != "style" or style.attr("type") != "paragraph":
            continue
        sid = style.attr("styleId")
        if not sid:
            continue
        name_node = style.child("name")
        name = (name_node.attr("val") if name_node else "") or ""
        parent = style.child("basedOn")
        if parent and parent.attr("val"):
            based[sid] = parent.attr("val") or ""
        match = _HEADING_STYLE.match(name.strip())
        if match:
            own[sid] = int(match.group(1))
        elif name.strip().lower() == "title":
            own[sid] = 1
        else:
            ppr = style.child("pPr")
            outline = ppr.child("outlineLvl") if ppr else None
            if outline is not None and (outline.attr("val") or "").isdigit() and int(outline.attr("val") or 9) < 9:
                own[sid] = int(outline.attr("val") or 0) + 1
    for sid in set(based) | set(own):
        current, hops = sid, 0
        while current and hops < 12:
            if current in own:
                levels[sid] = own[current]
                break
            current, hops = based.get(current, ""), hops + 1
    return levels


def _paragraph_text(paragraph: Node) -> str:
    """A paragraph's visible text: runs in order, tabs and breaks kept, deletions and field codes out."""
    parts: list[str] = []

    def walk(node: Node) -> None:
        for child in node.children:
            tag = child.tag
            if tag == "t":
                parts.append(child.text)
            elif tag == "tab":
                parts.append("\t")
            elif tag in ("br", "cr"):
                parts.append("\n")
            elif tag == "noBreakHyphen":
                parts.append("-")
            elif tag in _DOCX_SKIPPED:
                continue
            elif tag == "p":  # a text box's own paragraph inside this one
                parts.append("\n")
                walk(child)
            elif tag in _DOCX_TRANSPARENT:
                walk(child)

    walk(paragraph)
    return "".join(parts).strip()


def _heading_level(paragraph: Node, levels: dict[str, int]) -> int | None:
    ppr = paragraph.child("pPr")
    if ppr is None:
        return None
    outline = ppr.child("outlineLvl")
    if outline is not None:
        value = outline.attr("val") or ""
        if value.isdigit():
            return int(value) + 1 if int(value) < 9 else None
    style = ppr.child("pStyle")
    sid = style.attr("val") if style else None
    if not sid:
        return None
    if sid in levels:
        return levels[sid]
    match = _HEADING_STYLE.match(sid)  # "Heading1": the id Word gives its own style, when styles.xml is absent
    return int(match.group(1)) if match else None


def _is_list_item(paragraph: Node) -> bool:
    ppr = paragraph.child("pPr")
    return ppr is not None and ppr.child("numPr") is not None


def _table_rows(table: Node) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for row in table.children:
        if row.tag != "tr":
            continue
        cells = [_cell_text(cell) for cell in row.children if cell.tag == "tc"]
        if any(cells):
            rows.append(tuple(cells))
    return tuple(rows)


def _cell_text(cell: Node) -> str:
    pieces: list[str] = []

    def walk(node: Node) -> None:
        for child in node.children:
            if child.tag == "p":
                text = " ".join(_paragraph_text(child).split())
                if text:
                    pieces.append(text)
            elif child.tag in ("tbl", "tr", "tc", "sdt", "sdtContent"):
                walk(child)

    walk(cell)
    return " ".join(pieces)


def _render_rows(rows: Iterable[tuple[str, ...]]) -> str:
    return "\n".join(" | ".join(row) for row in rows)


def extract_docx(
    data: bytes, *, title: str = "the document", limits: OoxmlLimits | None = None
) -> DocumentExtraction:
    """Read a ``.docx`` into section and table blocks, preserving heading structure and order.

    A document with no text returns an empty extraction (like an empty plain-text upload). Anything
    that is not a readable Word package -- corrupt, encrypted, an Excel file renamed, over a size
    budget, carrying an entity declaration -- raises :class:`~aura_core.knowledge.ooxml.OoxmlError`,
    a :class:`KnowledgeError`.
    """
    package = Package(data, limits=limits or DEFAULT_LIMITS)
    part = package.main_part("officeDocument", "word/document.xml")
    root = package.tree(part, leaf_tags=_DOCX_LEAF_TAGS)
    if root.tag != "document":
        raise OoxmlError("the file is not a Word document")
    body = root.child("body")
    if body is None:
        return DocumentExtraction(blocks=())
    levels = _style_levels(package, part)

    blocks: list[ExtractedBlock] = []
    stack: list[tuple[int, str]] = []  # the open headings, outermost first
    lines: list[str] = []
    opening: tuple[str, int] | None = None  # (path, level) of the heading the pending lines start with

    def path() -> str | None:
        return " > ".join(text for _, text in stack) or None

    def flush() -> None:
        nonlocal opening
        text = "\n\n".join(lines).strip()
        lines.clear()
        if text:
            blocks.append(
                ExtractedBlock(
                    text=text,
                    modality=Modality.TEXT,
                    section=path(),
                    heading_level=opening[1] if opening else None,
                    role="section" if opening else "body",
                )
            )
        opening = None

    def visit(container: Node) -> None:
        nonlocal opening
        for node in container.children:
            tag = node.tag
            if tag == "p":
                text = _paragraph_text(node)
                if not text:
                    continue
                level = _heading_level(node, levels)
                if level is not None:
                    flush()
                    heading = " ".join(text.split())
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, heading))
                    opening = (path() or heading, level)
                    lines.append("#" * min(level, 6) + " " + heading)
                else:
                    lines.append(("- " + text) if _is_list_item(node) else text)
            elif tag == "tbl":
                rows = _table_rows(node)
                if not rows:
                    continue
                flush()
                blocks.append(
                    ExtractedBlock(
                        text=_render_rows(rows),
                        modality=Modality.TABLE,
                        section=path(),
                        role="table",
                        rows=rows,
                    )
                )
            elif tag in ("sdt", "sdtContent", "customXml", "ins", "moveTo", "smartTag"):
                visit(node)

    visit(body)
    flush()
    return DocumentExtraction(blocks=tuple(blocks))


# -- PPTX --------------------------------------------------------------------------------------

_SLIDE_NOISE = frozenset({"sldNum", "dt", "ftr", "hdr"})
_TITLE_TYPES = frozenset({"title", "ctrTitle"})
_PPTX_LEAF_TAGS = frozenset({"spPr", "grpSpPr", "xfrm", "rPr", "bodyPr", "lstStyle", "tblPr", "tcPr", "blipFill",
                             "extLst"})


def _drawing_paragraph(paragraph: Node) -> str:
    parts: list[str] = []
    for child in paragraph.children:
        if child.tag in ("r", "fld"):
            for t in child.children:
                if t.tag == "t":
                    parts.append(t.text)
        elif child.tag == "br":
            parts.append("\n")
    return "".join(parts).strip()


def _text_body(node: Node) -> list[str]:
    body = node.child("txBody")
    if body is None:
        return []
    return [text for text in (_drawing_paragraph(p) for p in body.children if p.tag == "p") if text]


class _Slide:
    """What one slide or notes page contains, gathered in shape order."""

    __slots__ = ("title", "body", "tables", "pictures", "other_graphics")

    def __init__(self) -> None:
        self.title: list[str] = []
        self.body: list[str] = []
        self.tables: list[tuple[tuple[str, ...], ...]] = []
        self.pictures = 0
        self.other_graphics = 0


def _gather(shapes: Node, slide: _Slide, *, notes: bool) -> None:
    for node in shapes.children:
        tag = node.tag
        if tag == "sp":
            ph = None
            for found in node.iter_all("ph"):
                ph = found.attr("type") or "body"
                break
            if ph in _SLIDE_NOISE or ph in ("sldImg",):
                continue
            paragraphs = _text_body(node)
            if not paragraphs:
                continue
            if ph in _TITLE_TYPES and not notes:
                slide.title.append(" ".join(" ".join(paragraphs).split()))
            else:
                slide.body.extend(paragraphs)
        elif tag == "graphicFrame":
            table = next(node.iter_all("tbl"), None)
            if table is not None:
                rows = []
                for tr in table.children:
                    if tr.tag != "tr":
                        continue
                    cells = [" ".join(" ".join(_text_body(tc)).split()) for tc in tr.children if tc.tag == "tc"]
                    if any(cells):
                        rows.append(tuple(cells))
                if rows:
                    slide.tables.append(tuple(rows))
            else:
                slide.other_graphics += 1
        elif tag == "pic":
            slide.pictures += 1
        elif tag in ("grpSp", "AlternateContent", "Choice"):
            _gather(node, slide, notes=notes)


def _shape_tree(root: Node) -> Node | None:
    common = root.child("cSld")
    return common.child("spTree") if common is not None else None


def _read_slide(package: Package, part: str, *, notes: bool) -> _Slide:
    root = package.tree(part, leaf_tags=_PPTX_LEAF_TAGS)
    slide = _Slide()
    tree = _shape_tree(root)
    if tree is not None:
        _gather(tree, slide, notes=notes)
    return slide


def _slide_parts(package: Package, presentation: str) -> list[str]:
    root = package.tree(presentation)
    if root.tag != "presentation":
        raise OoxmlError("the file is not a PowerPoint presentation")
    relations = {r.id: r for r in package.relationships(presentation)}
    listing = root.child("sldIdLst")
    parts: list[str] = []
    if listing is not None:
        for entry in listing.children:
            relation = relations.get(entry.rel_id() or "")
            if entry.tag == "sldId" and relation is not None and relation.type == "slide":
                parts.append(relation.target)
    if parts:
        return parts
    # No usable slide list: fall back to the numeric order of the slide files themselves.
    found = [n for n in package.names() if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", n, re.IGNORECASE)]
    return sorted(found, key=lambda n: int(re.findall(r"[0-9]+", n.rpartition("/")[2])[0]))


def _unreadable_slide_note(number: int, total: int, title: str, pictures: int) -> str:
    plural = "picture" if pictures == 1 else "pictures"
    return (
        f"[Slide {number} of {total} in {title} could not be read as text. "
        f"It contains {pictures} {plural} or graphics and no text. "
        f"Optical character recognition is not configured on this deployment, "
        f"so the contents of this slide are not indexed and cannot be quoted.]"
    )


def extract_pptx(
    data: bytes,
    *,
    title: str = "the document",
    limits: OoxmlLimits | None = None,
    max_slides: int | None = None,
) -> DocumentExtraction:
    """Read a ``.pptx`` slide by slide: text, tables and speaker notes, each tagged with its slide.

    ``pages`` is the slide count. A slide holding pictures or charts and no text at all becomes an
    :attr:`Modality.IMAGE` note (``needs_ocr``, listed in ``unreadable_pages``) rather than vanishing,
    for the same reason an unreadable PDF page does; a slide with nothing on it is a blank page. More
    than ``max_slides`` (default :data:`MAX_PPTX_SLIDES`) raises :class:`DocumentTooLargeError`
    before any slide is parsed.
    """
    package = Package(data, limits=limits or DEFAULT_LIMITS)
    presentation = package.main_part("officeDocument", "ppt/presentation.xml")
    parts = _slide_parts(package, presentation)
    limit = MAX_PPTX_SLIDES if max_slides is None else max_slides
    if len(parts) > limit:
        raise DocumentTooLargeError(
            f"the presentation has {len(parts)} slides; at most {limit} can be read",
            details={"slides": len(parts), "limit_slides": limit},
        )

    blocks: list[ExtractedBlock] = []
    unreadable: list[int] = []
    blank: list[int] = []
    total = len(parts)
    for number, part in enumerate(parts, start=1):
        if not package.has(part):
            raise OoxmlError("the presentation lists a slide whose part is missing", details={"part": part})
        slide = _read_slide(package, part, notes=False)
        notes_part = next((r.target for r in package.relationships(part) if r.type == "notesSlide"), None)
        notes = _read_slide(package, notes_part, notes=True) if notes_part and package.has(notes_part) else None
        produced = False

        heading = " ".join(slide.title).strip()
        if heading or slide.body:
            lines = []
            if heading:
                lines.append("# " + heading)
            if slide.body:
                lines.append("\n".join(slide.body))
            blocks.append(
                ExtractedBlock(
                    text="\n\n".join(lines),
                    modality=Modality.TEXT,
                    page=number,
                    slide=number,
                    section=heading or None,
                    heading_level=1 if heading else None,
                    role="slide",
                )
            )
            produced = True
        for rows in slide.tables:
            blocks.append(
                ExtractedBlock(
                    text=_render_rows(rows),
                    modality=Modality.TABLE,
                    page=number,
                    slide=number,
                    section=heading or None,
                    role="table",
                    rows=rows,
                )
            )
            produced = True
        if notes is not None and notes.body:
            blocks.append(
                ExtractedBlock(
                    text="Speaker notes:\n" + "\n".join(notes.body),
                    modality=Modality.TEXT,
                    page=number,
                    slide=number,
                    section=heading or None,
                    role="notes",
                )
            )
            produced = True

        if produced:
            continue
        visuals = slide.pictures + slide.other_graphics
        if visuals == 0:
            blank.append(number)
            continue
        unreadable.append(number)
        blocks.append(
            ExtractedBlock(
                text=_unreadable_slide_note(number, total, title, visuals),
                modality=Modality.IMAGE,
                page=number,
                slide=number,
                role="slide",
                needs_ocr=True,
            )
        )

    return DocumentExtraction(
        blocks=tuple(blocks), pages=total, unreadable_pages=tuple(unreadable), blank_pages=tuple(blank)
    )


def office_images(data: bytes, kind: str, *, limits: OoxmlLimits | None = None) -> list[dict[str, object]]:
    """The pictures a ``.docx`` or ``.pptx`` embeds: ``{"name", "slide"}`` per picture (``slide`` is
    ``None`` for Word, which does not record page numbers in the file -- pagination is decided when
    the document is rendered). Read from the relationships, so nothing is decoded; a picture used on
    two slides is listed on each. Raises :class:`OoxmlError` like the extractors."""
    package = Package(data, limits=limits or DEFAULT_LIMITS)
    found: list[dict[str, object]] = []
    if kind == "docx":
        part = package.main_part("officeDocument", "word/document.xml")
        seen: set[str] = set()
        for rel in package.relationships(part):
            if rel.type == "image" and rel.target not in seen:
                seen.add(rel.target)
                found.append({"name": rel.target.rpartition("/")[2], "slide": None})
    elif kind == "pptx":
        presentation = package.main_part("officeDocument", "ppt/presentation.xml")
        for number, part in enumerate(_slide_parts(package, presentation), start=1):
            for rel in package.relationships(part):
                if rel.type == "image":
                    found.append({"name": rel.target.rpartition("/")[2], "slide": number})
    else:
        raise OoxmlError("only Word and PowerPoint packages carry a picture list here")
    return found
