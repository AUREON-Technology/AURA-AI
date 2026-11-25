"""DOCX / PPTX / XLSX / TSV readers, tested against real OOXML bytes read by the real parsers.

Nothing here mocks the code under test: each file is a minimal but genuine Open XML package
assembled with ``zipfile`` (``[Content_Types].xml``, ``_rels/.rels``, the main part and its
relationships), then handed to ``extract_document`` / ``FilesConnector``'s table loader exactly as
an upload or a connected directory would be.

The hostile-input cases matter as much as the happy paths: an Office file is attacker-controlled
bytes, so the decompression, DOCTYPE/entity, nesting and corruption refusals are asserted to be
*refusals* (a :class:`KnowledgeError`), never a partial read.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from aura_core.adapters.connectors import files as files_module
from aura_core.adapters.connectors.files import load_documents, load_tables
from aura_core.domain.discovery import DOCUMENT_FORMATS
from aura_core.domain.errors import KnowledgeError
from aura_core.knowledge.documents import (
    DocumentTooLargeError,
    Modality,
    blocks_for_chunking,
    extract_document,
)
from aura_core.knowledge.office import extract_docx, extract_pptx
from aura_core.knowledge.ooxml import OoxmlError, OoxmlLimitError, OoxmlLimits, read_xlsx
from aura_core.rag.agentic import MultimodalRAG

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

ARABIC_HEADING = "الفصل الأول: الأحكام العامة"
ARABIC_PARA = "العقد رقم 1234 بين الطرفين ABC Ltd. ساري المفعول"


# -- package builders --------------------------------------------------------------------------


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8") if isinstance(content, str) else content)
    return buffer.getvalue()


def _rels(*entries: tuple[str, str, str]) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{OD}/{t}" Target="{target}"/>' for i, t, target in entries)
    return f'{XML}<Relationships xmlns="{REL}">{body}</Relationships>'


def _p(text: str, style: str | None = None, *, numbered: bool = False) -> str:
    ppr = ""
    if style or numbered:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + (
            '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "") + "</w:pPr>"
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _table(rows: list[list[str]]) -> str:
    body = "".join(
        "<w:tr>" + "".join(f"<w:tc><w:tcPr><w:tcW w:w='100'/></w:tcPr>{_p(cell)}</w:tc>" for cell in row) + "</w:tr>"
        for row in rows)
    return f"<w:tbl><w:tblPr><w:tblW w:w='0'/></w:tblPr>{body}</w:tbl>"


def _docx(body: str, *, styles: str | None = None, document_xml: str | None = None) -> bytes:
    document = document_xml or f'{XML}<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    files: dict[str, str | bytes] = {
        "[Content_Types].xml": f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": _rels(("rId1", "officeDocument", "word/document.xml")),
        "word/document.xml": document,
    }
    if styles is not None:
        files["word/_rels/document.xml.rels"] = _rels(("rId1", "styles", "styles.xml"))
        files["word/styles.xml"] = styles
    return _zip(files)


def _shape(text: list[str], ph: str | None = None) -> str:
    placeholder = f'<p:nvPr><p:ph type="{ph}"/></p:nvPr>' if ph else "<p:nvPr/>"
    paragraphs = "".join(f"<a:p><a:r><a:rPr lang='en'/><a:t>{t}</a:t></a:r></a:p>" for t in text)
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="s"/><p:cNvSpPr/>{placeholder}</p:nvSpPr>'
            f"<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>{paragraphs}</p:txBody></p:sp>")


def _slide(*shapes: str) -> str:
    return (f'{XML}<p:sld xmlns:a="{A}" xmlns:p="{P}" xmlns:r="{R}"><p:cSld><p:spTree>'
            f'<p:nvGrpSpPr/><p:grpSpPr/>{"".join(shapes)}</p:spTree></p:cSld></p:sld>')


def _notes(text: list[str]) -> str:
    return (f'{XML}<p:notes xmlns:a="{A}" xmlns:p="{P}"><p:cSld><p:spTree>'
            f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="img"/><p:cNvSpPr/><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            f'{_shape(text, "body")}'
            f'{_shape(["7"], "sldNum")}'
            f"</p:spTree></p:cSld></p:notes>")


def _pptx(slides: list[tuple[str, str | None]], order: list[int] | None = None) -> bytes:
    """``slides`` is ``[(slide xml, notes xml or None)]`` stored as slide1..N; ``order`` is the
    1-based file numbers in presentation order (default: as stored)."""
    order = order or list(range(1, len(slides) + 1))
    files: dict[str, str | bytes] = {
        "[Content_Types].xml": f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": _rels(("rId1", "officeDocument", "ppt/presentation.xml")),
    }
    ids = "".join(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>' for n in order)
    files["ppt/presentation.xml"] = (f'{XML}<p:presentation xmlns:p="{P}" xmlns:r="{R}">'
                                     f"<p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
    files["ppt/_rels/presentation.xml.rels"] = _rels(
        *[(f"rId{n}", "slide", f"slides/slide{n}.xml") for n in range(1, len(slides) + 1)])
    for n, (slide_xml, notes_xml) in enumerate(slides, start=1):
        files[f"ppt/slides/slide{n}.xml"] = slide_xml
        if notes_xml:
            files[f"ppt/slides/_rels/slide{n}.xml.rels"] = _rels(("rId1", "notesSlide", f"../notesSlides/notesSlide{n}.xml"))
            files[f"ppt/notesSlides/notesSlide{n}.xml"] = notes_xml
    return _zip(files)


def _c(ref: str, value: str | None = None, t: str | None = None, s: int | None = None, *, inline: str | None = None) -> str:
    style = f' s="{s}"' if s is not None else ""
    if inline is not None:
        return f'<c r="{ref}"{style} t="inlineStr"><is><t>{inline}</t></is></c>'
    kind = f' t="{t}"' if t else ""
    return f'<c r="{ref}"{kind}{style}><v>{value}</v></c>' if value is not None else f'<c r="{ref}"{style}/>'


def _row(n: int, *cells: str) -> str:
    return f'<row r="{n}">{"".join(cells)}</row>'


def _sheet_xml(*rows: str) -> str:
    return f'{XML}<worksheet xmlns="{S}"><sheetData>{"".join(rows)}</sheetData></worksheet>'


STYLES = (f'{XML}<styleSheet xmlns="{S}"><numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd\\ hh:mm"/></numFmts>'
          '<cellStyleXfs count="1"><xf numFmtId="14"/></cellStyleXfs>'
          '<cellXfs count="4"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="10"/></cellXfs></styleSheet>')


def _xlsx(sheets: list[tuple[str, str]], shared: list[str] | None = None, *, date1904: bool = False,
          styles: str | None = STYLES) -> bytes:
    files: dict[str, str | bytes] = {
        "[Content_Types].xml": f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": _rels(("rId1", "officeDocument", "xl/workbook.xml")),
    }
    listing = "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
    props = '<workbookPr date1904="1"/>' if date1904 else "<workbookPr/>"
    files["xl/workbook.xml"] = f'{XML}<workbook xmlns="{S}" xmlns:r="{R}">{props}<sheets>{listing}</sheets></workbook>'
    relations = [(f"rId{i}", "worksheet", f"worksheets/sheet{i}.xml") for i in range(1, len(sheets) + 1)]
    for i, (_, xml) in enumerate(sheets, 1):
        files[f"xl/worksheets/sheet{i}.xml"] = xml
    if shared is not None:
        relations.append(("rId90", "sharedStrings", "sharedStrings.xml"))
        files["xl/sharedStrings.xml"] = f'{XML}<sst xmlns="{S}">' + "".join(shared) + "</sst>"
    if styles is not None:
        relations.append(("rId91", "styles", "styles.xml"))
        files["xl/styles.xml"] = styles
    files["xl/_rels/workbook.xml.rels"] = _rels(*relations)
    return _zip(files)


def _si(text: str) -> str:
    return f"<si><t>{text}</t></si>"


# -- DOCX --------------------------------------------------------------------------------------


class TestDocx:
    def test_headings_paragraphs_and_tables_come_back_in_reading_order(self) -> None:
        body = (
            _p("Introduction", "Heading1") + _p("Welcome to the handbook.") + _p("More intro text.")
            + _p("Scope", "Heading2") + _p("Applies to everyone.", numbered=True)
            + _table([["Region", "Revenue"], ["EMEA", "100"], ["APAC", "200"]])
            + _p("Figures are in thousands.")
            + _p("Next steps", "Heading1") + _p("Ship it.")
        )
        extraction = extract_docx(_docx(body))

        got = [(b.role, b.modality, b.section, b.heading_level) for b in extraction.blocks]
        assert got == [
            ("section", Modality.TEXT, "Introduction", 1),
            ("section", Modality.TEXT, "Introduction > Scope", 2),
            ("table", Modality.TABLE, "Introduction > Scope", None),
            ("body", Modality.TEXT, "Introduction > Scope", None),
            ("section", Modality.TEXT, "Next steps", 1),
        ]
        intro, scope, table, after, nxt = extraction.blocks
        assert intro.text == "# Introduction\n\nWelcome to the handbook.\n\nMore intro text."
        assert scope.text == "## Scope\n\n- Applies to everyone."
        assert table.rows == (("Region", "Revenue"), ("EMEA", "100"), ("APAC", "200"))
        assert table.text == "Region | Revenue\nEMEA | 100\nAPAC | 200"
        assert after.text == "Figures are in thousands."
        assert nxt.text == "# Next steps\n\nShip it."
        assert all(b.page is None and b.slide is None for b in extraction.blocks)

    def test_a_custom_style_is_a_heading_when_styles_xml_says_so(self) -> None:
        styles = (f'{XML}<w:styles xmlns:w="{W}">'
                  '<w:style w:type="paragraph" w:styleId="Kop1"><w:name w:val="heading 1"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Kop2"><w:name w:val="heading 2"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Sub"><w:name w:val="Subtle"/><w:basedOn w:val="Kop2"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>')
        body = _p("Top", "Kop1") + _p("under top") + _p("Derived", "Sub") + _p("deep text")
        extraction = extract_docx(_docx(body, styles=styles))
        assert [(b.section, b.heading_level) for b in extraction.blocks] == [("Top", 1), ("Top > Derived", 2)]

    def test_arabic_round_trips_unmangled_and_in_logical_order(self) -> None:
        body = _p(ARABIC_HEADING, "Heading1") + _p(ARABIC_PARA) + _table([["الاسم", "المبلغ"], ["ليلى", "٣٠٠"]])
        extraction = extract_docx(_docx(body))

        assert extraction.blocks[0].text == f"# {ARABIC_HEADING}\n\n{ARABIC_PARA}"
        assert extraction.blocks[0].section == ARABIC_HEADING
        assert extraction.blocks[1].rows == (("الاسم", "المبلغ"), ("ليلى", "٣٠٠"))
        # No bidi control characters were injected and nothing was reordered.
        joined = extraction.text()
        assert not {"‪", "‫", "‬", "‭", "‮", "‎", "‏"} & set(joined)
        assert ARABIC_PARA in joined

    def test_tracked_deletions_and_field_codes_are_not_text(self) -> None:
        body = ('<w:p><w:r><w:t>Kept </w:t></w:r><w:del w:id="1"><w:r><w:delText>gone</w:delText></w:r></w:del>'
                '<w:ins w:id="2"><w:r><w:t>added</w:t></w:r></w:ins>'
                '<w:r><w:instrText> PAGE </w:instrText></w:r><w:r><w:tab/><w:t>tail</w:t></w:r></w:p>')
        (block,) = extract_docx(_docx(body)).blocks
        assert block.text == "Kept added\ttail"

    def test_content_controls_and_nested_table_text_are_read(self) -> None:
        body = ("<w:sdt><w:sdtContent>" + _p("Inside a content control") + "</w:sdtContent></w:sdt>"
                + "<w:tbl><w:tr><w:tc>" + _p("outer") + _table([["inner"]]) + "</w:tc></w:tr></w:tbl>")
        blocks = extract_docx(_docx(body)).blocks
        assert blocks[0].text == "Inside a content control"
        assert blocks[1].rows == (("outer inner",),)

    def test_chunk_metadata_is_payload_safe_scalars(self) -> None:
        extraction = extract_docx(_docx(_p("Heading", "Heading1") + _p("text") + _table([["a", "b"]])))
        for _text, meta in blocks_for_chunking(extraction):
            assert all(isinstance(v, (str, int, float, bool)) for v in meta.values()), meta
        assert blocks_for_chunking(extraction)[0][1] == {
            "modality": "text", "section": "Heading", "heading_level": 1, "role": "section"}

    def test_an_empty_document_is_an_empty_extraction_not_an_error(self) -> None:
        extraction = extract_docx(_docx(""))
        assert extraction.blocks == ()
        assert extraction.text() == ""
        assert extract_docx(_docx(_p("   ") + _table([[""]]))).blocks == ()

    def test_produced_modalities_stay_within_what_retrieval_filters_on(self) -> None:
        docx = extract_docx(_docx(_p("H", "Heading1") + _p("x") + _table([["a"]])))
        pptx = extract_pptx(_pptx([(_slide(_shape(["T"], "title")), None),
                                   (_slide("<p:pic><p:nvPicPr><p:cNvPr id='3' name='p'/></p:nvPicPr></p:pic>"), None)]))
        produced = set(docx.modalities) | set(pptx.modalities)
        assert produced == {Modality.TEXT, Modality.TABLE, Modality.IMAGE}
        # TEXT is deliberately outside the non-text filter; everything else must be inside it.
        assert produced - {Modality.TEXT} <= set(MultimodalRAG.NON_TEXT_MODALITIES)
        assert Modality.TEXT not in MultimodalRAG.NON_TEXT_MODALITIES

    def test_extract_document_dispatches_on_the_declared_format(self) -> None:
        assert {"docx", "pptx"} <= DOCUMENT_FORMATS
        via_dispatch = extract_document(_docx(_p("Hi", "Heading1") + _p("there")), format="docx", title="t.docx")
        assert via_dispatch.blocks[0].text == "# Hi\n\nthere"
        via_dispatch = extract_document(_pptx([(_slide(_shape(["Hello"], "title")), None)]), format="pptx")
        assert via_dispatch.blocks[0].slide == 1
        with pytest.raises(KnowledgeError):
            extract_document(b"x", format="xlsx")


# -- PPTX --------------------------------------------------------------------------------------


class TestPptx:
    def test_slides_carry_their_number_text_tables_and_speaker_notes(self) -> None:
        table = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
                 '<a:tr><a:tc><a:txBody><a:p><a:r><a:t>Q</a:t></a:r></a:p></a:txBody></a:tc>'
                 '<a:tc><a:txBody><a:p><a:r><a:t>Total</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
                 '<a:tr><a:tc><a:txBody><a:p><a:r><a:t>Q1</a:t></a:r></a:p></a:txBody></a:tc>'
                 '<a:tc><a:txBody><a:p><a:r><a:t>42</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
                 '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
        deck = _pptx([
            (_slide(_shape(["Roadmap"], "title"), _shape(["Ship v2", "Fix bugs"]), _shape(["3"], "sldNum")),
             _notes(["Remember to mention the migration.", "Ask for questions."])),
            (_slide(_shape(["Numbers"], "title"), table), None),
        ])
        extraction = extract_pptx(deck)

        assert extraction.pages == 2
        assert [(b.slide, b.page, b.role, b.modality) for b in extraction.blocks] == [
            (1, 1, "slide", Modality.TEXT), (1, 1, "notes", Modality.TEXT),
            (2, 2, "slide", Modality.TEXT), (2, 2, "table", Modality.TABLE)]
        slide1, notes1, slide2, table2 = extraction.blocks
        assert slide1.text == "# Roadmap\n\nShip v2\nFix bugs"  # the slide-number placeholder is not content
        assert slide1.section == "Roadmap"
        assert notes1.text == "Speaker notes:\nRemember to mention the migration.\nAsk for questions."
        assert table2.rows == (("Q", "Total"), ("Q1", "42"))
        assert blocks_for_chunking(extraction)[1][1]["slide"] == 1

    def test_slide_order_is_the_presentations_not_the_file_names(self) -> None:
        deck = _pptx([(_slide(_shape(["stored first"], "title")), None),
                      (_slide(_shape(["stored second"], "title")), None)], order=[2, 1])
        blocks = extract_pptx(deck).blocks
        assert [(b.slide, b.section) for b in blocks] == [(1, "stored second"), (2, "stored first")]

    def test_picture_only_slide_is_flagged_unreadable_and_an_empty_one_is_blank(self) -> None:
        pic = "<p:pic><p:nvPicPr><p:cNvPr id='3' name='photo'/></p:nvPicPr></p:pic>"
        deck = _pptx([(_slide(_shape(["Intro"], "title")), None), (_slide(pic), None), (_slide(), None)])
        extraction = extract_pptx(deck, title="deck.pptx")
        assert extraction.unreadable_pages == (2,)
        assert extraction.blank_pages == (3,)
        note = extraction.blocks[1]
        assert (note.modality, note.needs_ocr, note.slide) == (Modality.IMAGE, True, 2)
        assert "Slide 2 of 3 in deck.pptx" in note.text

    def test_arabic_slide_and_notes(self) -> None:
        deck = _pptx([(_slide(_shape([ARABIC_HEADING], "title"), _shape([ARABIC_PARA])), _notes(["ملاحظات المتحدث"]))])
        title_block, notes = extract_pptx(deck).blocks
        assert title_block.text == f"# {ARABIC_HEADING}\n\n{ARABIC_PARA}"
        assert notes.text == "Speaker notes:\nملاحظات المتحدث"

    def test_a_deck_with_no_slides_is_empty(self) -> None:
        extraction = extract_pptx(_pptx([]))
        assert extraction.blocks == () and extraction.pages == 0

    def test_too_many_slides_is_refused_before_any_is_parsed(self) -> None:
        deck = _pptx([(_slide(_shape([f"s{i}"], "title")), None) for i in range(5)])
        with pytest.raises(DocumentTooLargeError) as caught:
            extract_pptx(deck, max_slides=3)
        assert caught.value.details["slides"] == 5


# -- XLSX / TSV through the files connector ----------------------------------------------------


def _tables(directory: Path):
    tables, issues = load_tables(directory)
    return {t.name: t for t in tables}, {(i["code"], i["locator"]) for i in issues}


class TestXlsxTables:
    def _book(self) -> bytes:
        shared = [
            _si("Staff directory 2024"), _si("id"), _si("name"), _si("hired"), _si("active"), _si("score"),
            _si("ليلى"), "<si><r><t>Om</t></r><r><t>ar</t></r><rPh><t>ignored</t></rPh></si>",
            _si("when"), _si("n"),
        ]
        people = _sheet_xml(
            _row(1, _c("A1", "0", "s")),                                    # a title line above the table
            _row(3, _c("A3", "1", "s"), _c("B3", "2", "s"), _c("C3", "3", "s"), _c("D3", "4", "s"), _c("E3", "5", "s")),
            _row(4, _c("A4", "1"), _c("B4", "6", "s"), _c("C4", "45292", s=1), _c("D4", "1", "b"), _c("E4", "3.5")),
            _row(5, _c("A5", "2"), _c("B5", "7", "s"), _c("C5", "45293", s=1), _c("D5", "0", "b"), _c("E5", "4")),
            _row(6, _c("A6", "3"), _c("B6", inline="Noor"), _c("C6", "45294", s=1), _c("D6", "1", "b"), _c("E6", "7.25")),
        )
        events = _sheet_xml(
            _row(1, _c("A1", "8", "s"), _c("B1", "9", "s")),
            _row(2, _c("A2", "45292.5", s=2), _c("B2", "10")),
            _row(3, _c("A3", "45293", s=2), _c("B3", "#DIV/0!", "e")),
            _row(4, _c("A4", "45294.25", s=2), _c("B4", "12")),
        )
        bare = _sheet_xml(_row(1, _c("A1", "1"), _c("B1", "2")), _row(2, _c("A2", "3"), _c("B2", "4")))
        return _xlsx([("People", people), ("Events", events), ("Bare", bare)], shared)

    def test_one_table_per_sheet_with_typed_cells_and_header_detection(self, tmp_path: Path) -> None:
        (tmp_path / "book.xlsx").write_bytes(self._book())
        tables, issues = _tables(tmp_path)

        assert set(tables) == {"book__People", "book__Events", "book__Bare"}
        assert issues == set()

        people = tables["book__People"]
        assert people.columns == ("id", "name", "hired", "active", "score")  # the title row was skipped
        assert people.types == ("integer", "text", "date", "boolean", "numeric")
        assert [dict(r) for r in people.rows] == [
            {"id": 1, "name": "ليلى", "hired": "2024-01-01", "active": True, "score": 3.5},
            {"id": 2, "name": "Omar", "hired": "2024-01-02", "active": False, "score": 4},
            {"id": 3, "name": "Noor", "hired": "2024-01-03", "active": True, "score": 7.25},
        ]

        events = tables["book__Events"]
        assert events.columns == ("when", "n")
        assert events.types == ("timestamp without time zone", "integer")  # the #DIV/0! cell is empty
        assert [r["when"] for r in events.rows] == ["2024-01-01T12:00:00", "2024-01-02T00:00:00", "2024-01-03T06:00:00"]
        assert [r["n"] for r in events.rows] == [10, None, 12]

        bare = tables["book__Bare"]  # a numeric first row is data, not a header
        assert bare.columns == ("column_1", "column_2")
        assert [dict(r) for r in bare.rows] == [{"column_1": 1, "column_2": 2}, {"column_1": 3, "column_2": 4}]

    def test_a_single_sheet_workbook_is_named_after_its_file(self, tmp_path: Path) -> None:
        sheet = _sheet_xml(_row(1, _c("A1", "0", "s"), _c("B1", "1", "s")), _row(2, _c("A2", "5"), _c("B2", "6")))
        (tmp_path / "customers.xlsx").write_bytes(_xlsx([("Sheet1", sheet)], [_si("id"), _si("amount")]))
        tables, _ = _tables(tmp_path)
        assert list(tables) == ["customers"]
        assert tables["customers"].columns == ("id", "amount")

    def test_the_1904_date_system_is_honoured(self) -> None:
        sheet = _sheet_xml(_row(1, _c("A1", "0", "s")), _row(2, _c("A2", "0", s=1)))
        (only,) = read_xlsx(_xlsx([("S", sheet)], [_si("d")], date1904=True))
        assert str(only.rows[1][0].date()) == "1904-01-01"

    def test_a_workbook_with_no_styles_or_shared_strings_still_reads(self) -> None:
        sheet = _sheet_xml(_row(1, _c("A1", inline="a"), _c("B1", inline="b")), _row(2, _c("A2", "1"), _c("C2", "3")))
        (only,) = read_xlsx(_xlsx([("S", sheet)], None, styles=None))
        assert only.rows == (("a", "b"), (1, None, 3))

    def test_mixed_date_and_time_cells_make_a_timestamp_column_and_whole_days_a_date_column(self, tmp_path: Path) -> None:
        sheet = _sheet_xml(_row(1, _c("A1", "0", "s")), _row(2, _c("A2", "45292", s=1)), _row(3, _c("A3", "45293.5", s=2)))
        (tmp_path / "m.xlsx").write_bytes(_xlsx([("S", sheet)], [_si("d")]))
        tables, _ = _tables(tmp_path)
        assert tables["m"].types == ("timestamp without time zone",)
        assert [r["d"] for r in tables["m"].rows] == ["2024-01-01T00:00:00", "2024-01-02T12:00:00"]

    def test_the_row_cap_is_reported_not_silent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(files_module, "MAX_ROWS", 5)
        rows = [_row(1, _c("A1", "0", "s"))] + [_row(n, _c(f"A{n}", str(n))) for n in range(2, 22)]
        (tmp_path / "big.xlsx").write_bytes(_xlsx([("S", _sheet_xml(*rows))], [_si("n")]))
        tables, issues = _tables(tmp_path)
        assert len(tables["big"].rows) == 5 and tables["big"].truncated
        assert ("rows_truncated", "table:files.big") in issues

    def test_the_readers_own_cap_stops_early_and_says_so(self) -> None:
        rows = [_row(n, _c(f"A{n}", str(n))) for n in range(1, 11)]
        (sheet,) = read_xlsx(_xlsx([("S", _sheet_xml(*rows))], None), max_rows_per_sheet=3)
        assert sheet.truncated and len(sheet.rows) == 3
        (whole,) = read_xlsx(_xlsx([("S", _sheet_xml(*rows))], None), max_rows_per_sheet=10)
        assert not whole.truncated and len(whole.rows) == 10

    def test_tsv_is_a_table_like_csv(self, tmp_path: Path) -> None:
        (tmp_path / "staff.tsv").write_text(
            "id\tname\tactive\tjoined\n1\tليلى\ttrue\t2024-01-01\n2\t\"Omar\tSr\"\tfalse\t2024-02-02\n", encoding="utf-8")
        (tmp_path / "staff2.csv").write_text("id,name,active,joined\n1,ليلى,true,2024-01-01\n", encoding="utf-8")
        tables, issues = _tables(tmp_path)
        assert issues == set()
        staff = tables["staff"]
        assert staff.columns == ("id", "name", "active", "joined")
        assert staff.types == ("integer", "text", "boolean", "date")
        assert [r["name"] for r in staff.rows] == ["ليلى", "Omar\tSr"]  # a quoted tab stays inside its cell
        # Same inference as the CSV path for the same data.
        assert tables["staff2"].types == staff.types

    def test_profiling_treats_a_sheet_like_any_other_table(self, tmp_path: Path) -> None:
        from aura_core.adapters.connectors.files import catalog_snapshot
        from aura_core.domain.discovery import DiscoveryBudget

        (tmp_path / "book.xlsx").write_bytes(self._book())
        tables, issues = load_tables(tmp_path)
        snapshot = catalog_snapshot(tables, issues, DiscoveryBudget())
        assert {r["name"] for r in snapshot["relations"]} == {"book__People", "book__Events", "book__Bare"}
        assert {p["column"] for p in snapshot["profiles"] if p["table"] == "book__People"} == {
            "id", "name", "hired", "active", "score"}

    def test_documents_and_unsupported_legacy_formats_are_classified(self, tmp_path: Path) -> None:
        (tmp_path / "plan.docx").write_bytes(_docx(_p("x")))
        (tmp_path / "deck.pptx").write_bytes(_pptx([(_slide(_shape(["t"], "title")), None)]))
        (tmp_path / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0")
        (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0")
        (tmp_path / "old.ppt").write_bytes(b"\xd0\xcf\x11\xe0")
        (tmp_path / "grid.xlsx").write_bytes(self._book())
        documents, issues = load_documents(tmp_path)
        assert {(d.name, d.format) for d in documents} == {("plan.docx", "docx"), ("deck.pptx", "pptx")}
        assert {(i["code"], i["locator"]) for i in issues} == {
            ("unsupported_format", "document:old.xls"), ("unsupported_format", "document:old.doc"),
            ("unsupported_format", "document:old.ppt")}  # a workbook is a table source, not a document


# -- hostile and broken input --------------------------------------------------------------------

_DOC_HEAD = f'{XML}<w:document xmlns:w="{W}"><w:body>'
_DOC_TAIL = "</w:body></w:document>"


class TestHostileInput:
    def test_a_decompression_bomb_is_refused_not_truncated(self) -> None:
        # ~70 MiB of spaces deflates to roughly 70 KiB: tiny upload, member over the 64 MiB cap.
        bomb = (_DOC_HEAD + " " * (70 * 1024 * 1024) + _p("payload") + _DOC_TAIL)
        data = _docx("", document_xml=bomb)
        assert len(data) < 1024 * 1024
        with pytest.raises(OoxmlLimitError) as caught:
            extract_docx(data)
        assert isinstance(caught.value, KnowledgeError) and caught.value.http_status == 413

    def test_the_budgets_are_member_count_member_size_and_total(self) -> None:
        many = _zip({f"f{i}.txt": "x" for i in range(30)} | {"word/document.xml": _DOC_HEAD + _DOC_TAIL,
                                                             "_rels/.rels": _rels(("rId1", "officeDocument", "word/document.xml"))})
        with pytest.raises(OoxmlLimitError, match="entries"):
            extract_docx(many, limits=OoxmlLimits(max_members=10))
        big = _docx(_p("x" * 5000))
        with pytest.raises(OoxmlLimitError):
            extract_docx(big, limits=OoxmlLimits(max_member_bytes=1000))
        with pytest.raises(OoxmlLimitError, match="expands to more than"):
            extract_docx(big, limits=OoxmlLimits(max_total_bytes=2000))
        assert extract_docx(big).blocks  # the same file is fine under the defaults

    def test_a_bomb_workbook_is_an_unparseable_table_never_a_partial_one(self, tmp_path: Path) -> None:
        sheet = f'{XML}<worksheet xmlns="{S}"><sheetData>' + " " * (70 * 1024 * 1024) + "</sheetData></worksheet>"
        (tmp_path / "bomb.xlsx").write_bytes(_xlsx([("S", sheet)], None))
        tables, issues = _tables(tmp_path)
        assert tables == {}
        assert issues == {("unparseable", "table:files.bomb")}

    @pytest.mark.parametrize("prolog", [
        '<!DOCTYPE foo [<!ENTITY a "aaaaaaaaaa">]>',
        '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>',
        '<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>',
        '<!DOCTYPE x SYSTEM "http://127.0.0.1:9/evil.dtd">',
    ])
    def test_doctype_and_entity_declarations_are_refused(self, prolog: str) -> None:
        document = f'{XML}{prolog}<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&a;&xxe;&lol2;</w:t></w:r></w:p></w:body></w:document>'
        with pytest.raises(OoxmlError, match="DOCTYPE or ENTITY"):
            extract_docx(_docx("", document_xml=document))
        sheet_book = _xlsx([("S", f'{XML}{prolog}<worksheet xmlns="{S}"><sheetData/></worksheet>')], None)
        with pytest.raises(OoxmlError, match="DOCTYPE or ENTITY"):
            read_xlsx(sheet_book)

    def test_a_declaration_hidden_in_an_exotic_encoding_is_still_refused(self) -> None:
        # EBCDIC bytes slip past a byte scan for "<!DOCTYPE"; expat's own declaration handler must not.
        text = (f'<?xml version="1.0" encoding="cp037"?><!DOCTYPE d [<!ENTITY a "b">]>'
                f'<w:document xmlns:w="{W}"><w:body/></w:document>')
        with pytest.raises(OoxmlError):
            extract_docx(_docx("", document_xml=text.encode("cp037")))  # type: ignore[arg-type]

    def test_utf16_xml_is_refused_because_it_would_hide_a_doctype_from_the_scan(self) -> None:
        text = f'{XML}<w:document xmlns:w="{W}"><w:body/></w:document>'
        with pytest.raises(OoxmlError, match="UTF-8"):
            extract_docx(_docx("", document_xml=text.encode("utf-16")))  # type: ignore[arg-type]

    def test_pathological_nesting_is_refused(self) -> None:
        nested = "<w:sdt><w:sdtContent>" * 300 + _p("deep") + "</w:sdtContent></w:sdt>" * 300
        with pytest.raises(OoxmlLimitError, match="deep"):
            extract_docx(_docx(nested))

    def test_too_many_elements_is_refused(self) -> None:
        with pytest.raises(OoxmlLimitError, match="elements"):
            extract_docx(_docx(_p("a") * 50), limits=OoxmlLimits(max_tree_elements=40))

    @pytest.mark.parametrize("data", [
        b"", b"not a zip at all", b"PK\x03\x04 this is not a real archive", b"PK\x05\x06" + b"\x00" * 18,
    ])
    def test_corrupt_input_is_a_clear_error(self, data: bytes) -> None:
        for reader in (extract_docx, extract_pptx):
            with pytest.raises(OoxmlError):
                reader(data)
        with pytest.raises(OoxmlError):
            read_xlsx(data)

    def test_a_truncated_package_is_a_clear_error(self) -> None:
        good = _docx(_p("some text") * 20)
        with pytest.raises(OoxmlError):
            extract_docx(good[: len(good) // 2])

    def test_a_flipped_byte_inside_a_part_fails_the_crc_instead_of_returning_garbage(self) -> None:
        raw = bytearray(_zip({"_rels/.rels": _rels(("rId1", "officeDocument", "word/document.xml")),
                              "word/document.xml": _DOC_HEAD + _p("A" * 4000) + _DOC_TAIL}))
        # Corrupt the compressed stream of the last local entry (deflate data sits after its header).
        at = raw.rfind(b"PK\x03\x04") + 60
        raw[at] ^= 0xFF
        with pytest.raises(OoxmlError):
            extract_docx(bytes(raw))

    def test_password_protected_files_are_named_as_such(self) -> None:
        cfb = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600
        with pytest.raises(OoxmlError, match="password-protected or legacy"):
            extract_docx(cfb)

    def test_an_encrypted_zip_member_is_refused(self) -> None:
        raw = bytearray(_docx(_p("secret")))
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            at = raw.find(signature)
            while at != -1:
                raw[at + offset] |= 0x01  # general-purpose flag bit 0: encrypted
                at = raw.find(signature, at + 4)
        with pytest.raises(OoxmlError, match="encrypted"):
            extract_docx(bytes(raw))

    def test_the_wrong_kind_of_office_file_is_named(self) -> None:
        workbook = _xlsx([("S", _sheet_xml(_row(1, _c("A1", "1"))))], None)
        with pytest.raises(OoxmlError, match="not a Word document"):
            extract_docx(workbook)
        with pytest.raises(OoxmlError, match="not a PowerPoint"):
            extract_pptx(_docx(_p("x")))
        with pytest.raises(OoxmlError, match="not an Excel workbook"):
            read_xlsx(_docx(_p("x")))

    def test_a_relationship_cannot_point_outside_the_package(self) -> None:
        escape = _zip({"_rels/.rels": _rels(("rId1", "officeDocument", "../../etc/passwd"))})
        with pytest.raises(OoxmlError, match="outside the package"):
            extract_docx(escape)

    def test_malformed_xml_is_a_clear_error(self) -> None:
        with pytest.raises(OoxmlError, match="well-formed"):
            extract_docx(_docx("", document_xml=_DOC_HEAD + "<w:p><w:r></w:p>" + _DOC_TAIL))
        with pytest.raises(OoxmlError, match="well-formed"):
            extract_docx(_docx("", document_xml=_DOC_HEAD + "<w:p>&undefined;</w:p>" + _DOC_TAIL))
