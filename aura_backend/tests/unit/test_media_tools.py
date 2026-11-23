"""ADR-040: ``parse_document``, ``inspect_spreadsheet``, ``ocr_image``, ``analyze_image_requirements``
and ``transcribe_audio``.

Everything runs on real substrate: real PDFs / Word / PowerPoint / Excel files built byte by byte,
real Pillow-rendered images read by the real tesseract binary (skipped, never faked, when tesseract or
Pillow is absent), and the real ``VisionEngine`` / router with the scripted echo provider standing in
for the vision *model*. Two seams are test doubles, and each says so where it is used: a fake
faster-whisper model shape for ``transcribe_audio`` (the package is not installable here), and a
synthetic ``OcrResult`` for the confidence rules that tesseract cannot be forced to produce on demand.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.ports.documents import OcrBox, OcrLine, OcrResult, OcrWord
from aura_core.ports.runtime import ToolContext
from aura_core.tools import media
from aura_core.tools.builtin import default_tools
from aura_core.tools.media import MEDIA_TOOL_NAMES, build_media_tools
from tests.unit.test_docx_pptx_xlsx_readers import (
    XML,
    _c,
    _docx,
    _notes,
    _p,
    _pptx,
    _row,
    _shape,
    _sheet_xml,
    _slide,
    _table,
    _xlsx,
    _zip,
)
from tests.unit.test_document_extraction import PROSE, TABLE, _pdf

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

try:
    from aura_core.adapters.tesseract_ocr import TesseractOcrEngine, tesseract_available
except Exception:  # pragma: no cover
    tesseract_available = lambda: False  # noqa: E731

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None or not tesseract_available(), reason="tesseract is not installed")


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-media", deadline_s=120.0)


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path / "files"


def by_name(tools) -> dict:
    return {t.spec.name: t for t in tools}


def make(root: Path, **engines) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    return by_name(build_media_tools(root, **engines))


def put(root: Path, name: str, data: bytes | str) -> str:
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return name


async def call(tools: dict, name: str, ctx, **arguments) -> dict:
    return json.loads(await tools[name].execute(arguments, ctx))


def render(lines: list[tuple[tuple[int, int], str]], size=(900, 500), font_size: int = 36) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_size)
    for xy, text in lines:
        draw.text(xy, text, font=font, fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


MOCKUP = render([((40, 30), "Sign in to AURA"), ((40, 130), "Email:"), ((40, 200), "Password:"),
                 ((40, 300), "Forgot password?"), ((40, 380), "LOGIN")])


# ======================================================================================
# registration and grants
# ======================================================================================


class TestRegistration:
    def test_spreadsheet_and_document_readers_need_only_a_root(self, root) -> None:
        assert set(make(root)) == {"inspect_spreadsheet", "parse_document"}

    def test_ocr_and_speech_tools_exist_only_with_their_engine(self, root) -> None:
        class PlainOcr:
            name = "plain"

            async def image_text(self, image: bytes, *, page: int) -> str:
                return ""

        class Stt:
            name = "stt"

            async def transcribe(self, audio, *, language=None):
                return ""

            async def transcribe_segments(self, audio, *, language=None, word_timestamps=True):
                raise NotImplementedError

        assert "ocr_image" in make(root, ocr_engine=PlainOcr())
        assert "analyze_image_requirements" not in make(root, ocr_engine=PlainOcr())  # needs *structured* OCR
        assert "transcribe_audio" in make(root, stt_engine=Stt())
        assert "transcribe_audio" not in make(root)

    def test_default_tools_registers_them_only_with_a_files_root(self, root) -> None:
        assert not MEDIA_TOOL_NAMES & {t.spec.name for t in default_tools()}
        names = {t.spec.name for t in default_tools(workspace=root)}
        assert {"inspect_spreadsheet", "parse_document"} <= names

    def test_every_tool_is_untrusted_content_and_read_only(self, root) -> None:
        class Stt:
            name = "stt"

            async def transcribe(self, audio, *, language=None):
                return ""

            async def transcribe_segments(self, audio, *, language=None, word_timestamps=True):
                raise NotImplementedError

        class Ocr:
            name = "ocr"

            async def image_text(self, image, *, page):
                return ""

            async def image_data(self, image, *, page):
                raise NotImplementedError

        tools = make(root, ocr_engine=Ocr(), stt_engine=Stt())
        assert set(tools) == MEDIA_TOOL_NAMES
        for tool in tools.values():
            assert "untrusted_content" in tool.spec.tags
            assert tool.spec.side_effecting is False and tool.spec.required_capability is None

    def test_persona_grants(self) -> None:
        from aura_core.agents.registry import build_default_agent_registry

        agents = build_default_agent_registry()
        engineer = set(agents.get("engineer").allowed_tools)
        assert MEDIA_TOOL_NAMES <= engineer
        assert {"lint_code", "check_format", "web_search", "fetch_url"} <= engineer
        research = set(agents.get("research_agent").allowed_tools)
        assert {"parse_document", "inspect_spreadsheet", "ocr_image", "transcribe_audio"} <= research
        for name in ("decision_agent", "finance_advisor", "health_companion", "task_planner"):
            assert not MEDIA_TOOL_NAMES & set(agents.get(name).allowed_tools)


class TestConfinement:
    @pytest.mark.parametrize("name", ["parse_document", "inspect_spreadsheet"])
    async def test_a_path_outside_the_root_is_refused(self, root, ctx, name, tmp_path) -> None:
        (tmp_path / "secret.csv").write_text("a,b\n1,2\n")
        tools = make(root)
        for bad in ("../secret.csv", "sub/../../secret.csv", "a\x00b"):
            with pytest.raises(ToolExecutionError):
                await tools[name].execute({"path": bad}, ctx)

    async def test_a_symlink_out_of_the_root_is_refused(self, root, ctx, tmp_path) -> None:
        outside = tmp_path / "outside.csv"
        outside.write_text("a,b\n1,2\n")
        root.mkdir()
        (root / "link.csv").symlink_to(outside)
        with pytest.raises(ToolExecutionError, match="escapes"):
            await make(root)["inspect_spreadsheet"].execute({"path": "link.csv"}, ctx)

    async def test_a_directory_and_a_missing_file(self, root, ctx) -> None:
        tools = make(root)
        (root / "dir").mkdir()
        with pytest.raises(ToolExecutionError, match="not a file"):
            await tools["parse_document"].execute({"path": "dir"}, ctx)
        with pytest.raises(ToolExecutionError, match="not a file"):
            await tools["parse_document"].execute({"path": "nope.pdf"}, ctx)


# ======================================================================================
# inspect_spreadsheet
# ======================================================================================

CSV = (
    "id,amount,status,created,active,zip,note\n"
    "1,10,open,2024-01-01,true,00123,a\n"
    "2,20,closed,2024-02-01,false,00456,b\n"
    "3,30,open,2024-03-05,true,00789,c\n"
    "4,,open,2024-03-09,true,01234,\n"
)


class TestInspectSpreadsheet:
    async def test_csv_schema_and_statistics(self, root, ctx) -> None:
        put(root, "orders.csv", CSV)
        out = await call(make(root), "inspect_spreadsheet", ctx, path="orders.csv")
        assert out["format"] == "csv" and out["delimiter"] == ","
        (sheet,) = out["sheets"]
        assert (sheet["rows"], sheet["columns_total"], sheet["truncated"], sheet["header_detected"]) == (4, 7, False, True)
        cols = {c["name"]: c for c in sheet["columns"]}

        amount = cols["amount"]
        assert amount["type"] == "number" or amount["type"] == "integer"
        assert (amount["non_null"], amount["null"], amount["distinct"]) == (3, 1, 3)
        assert (amount["min"], amount["max"], amount["mean"], amount["median"], amount["stddev"]) == (10, 30, 20, 20, 10)

        assert cols["id"]["type"] == "integer" and cols["id"]["max"] == 4
        status = cols["status"]
        assert status["type"] == "text"
        assert status["top_values"] == [{"value": "open", "count": 3}, {"value": "closed", "count": 1}]
        created = cols["created"]
        assert created["type"] == "date" and (created["min"], created["max"]) == ("2024-01-01", "2024-03-09")
        assert cols["active"]["type"] == "boolean" and (cols["active"]["true"], cols["active"]["false"]) == (3, 1)
        # A leading zero makes it a code, not a quantity -- the connector's rule, shared.
        assert cols["zip"]["type"] == "text"
        assert cols["note"]["null"] == 1
        assert len(sheet["sample_rows"]) == 4 and sheet["sample_rows"][0]["status"] == "open"

    async def test_sample_rows_are_bounded_by_the_argument(self, root, ctx) -> None:
        put(root, "orders.csv", CSV)
        tools = make(root)
        assert (await call(tools, "inspect_spreadsheet", ctx, path="orders.csv", sample_rows=2))["sheets"][0]["sample_rows"][1]["id"] == 2
        assert (await call(tools, "inspect_spreadsheet", ctx, path="orders.csv", sample_rows=0))["sheets"][0]["sample_rows"] == []

    async def test_types_agree_with_the_files_connector(self, root, ctx) -> None:
        """The tool and the connector share one inference (knowledge/tabular.py): same file, same types."""
        from aura_core.adapters.connectors.files import load_tables
        from aura_core.knowledge.tabular import friendly_type

        put(root, "orders.csv", CSV)
        (table,) = load_tables(root)[0]
        out = await call(make(root), "inspect_spreadsheet", ctx, path="orders.csv")
        tool_types = {c["name"]: c["type"] for c in out["sheets"][0]["columns"]}
        assert tool_types == {name: friendly_type(t) for name, t in zip(table.columns, table.types)}

    async def test_semicolon_and_tab_delimiters(self, root, ctx) -> None:
        put(root, "eu.csv", "name;price\nwidget;1,5\ngadget;2\n")
        put(root, "t.tsv", "a\tb\n1\t2\n3\t4\n")
        tools = make(root)
        eu = await call(tools, "inspect_spreadsheet", ctx, path="eu.csv")
        assert eu["delimiter"] == ";" and eu["sheets"][0]["columns_total"] == 2
        tsv = await call(tools, "inspect_spreadsheet", ctx, path="t.tsv")
        assert tsv["delimiter"] == "tab" and tsv["sheets"][0]["columns"][1]["mean"] == 3

    async def test_a_latin1_file_is_read_with_a_warning(self, root, ctx) -> None:
        put(root, "l1.csv", "city,n\ncaf\xe9,1\n".encode("latin-1"))
        out = await call(make(root), "inspect_spreadsheet", ctx, path="l1.csv")
        assert out["sheets"][0]["sample_rows"][0]["city"] == "café"
        assert any("Latin-1" in w for w in out["warnings"])

    async def test_a_mostly_numeric_text_column_says_why(self, root, ctx) -> None:
        put(root, "m.csv", "v\n" + "\n".join(["1", "2", "3", "4", "5", "6", "7", "8", "9", "n/a"]) + "\n")
        col = (await call(make(root), "inspect_spreadsheet", ctx, path="m.csv"))["sheets"][0]["columns"][0]
        assert col["type"] == "text" and "n/a" in col["note"] and "9 of 10" in col["note"]

    async def test_row_cap_is_reported_not_silent(self, root, ctx, monkeypatch) -> None:
        monkeypatch.setattr(media, "MAX_TABLE_ROWS", 5)
        put(root, "big.csv", "n\n" + "\n".join(str(i) for i in range(20)) + "\n")
        sheet = (await call(make(root), "inspect_spreadsheet", ctx, path="big.csv"))["sheets"][0]
        assert sheet["truncated"] is True and sheet["rows"] == 5 and "cut at 5" in sheet["note"]
        assert sheet["columns"][0]["max"] == 4  # statistics cover the rows that were read, and say so

    async def test_wide_file_profiles_a_bounded_number_of_columns(self, root, ctx) -> None:
        header = ",".join(f"c{i}" for i in range(130))
        rows = "\n".join(",".join(str(r * j) for j in range(130)) for r in range(5))
        put(root, "wide.csv", header + "\n" + rows + "\n")
        sheet = (await call(make(root), "inspect_spreadsheet", ctx, path="wide.csv"))["sheets"][0]
        assert sheet["columns_total"] == 130 and len(sheet["columns"]) == 100
        assert "only the first 100 of 130" in sheet["columns_truncated"]

    async def test_a_huge_profile_degrades_in_stated_steps_and_stays_valid_json(self, root, ctx) -> None:
        rows = "\n".join(",".join(f"value-{r}-{j}-" + "x" * 60 for j in range(100)) for r in range(300))
        put(root, "heavy.csv", ",".join(f"col{j}" for j in range(100)) + "\n" + rows + "\n")
        raw = await make(root)["inspect_spreadsheet"].execute({"path": "heavy.csv", "sample_rows": 20}, ctx)
        out = json.loads(raw)
        assert len(raw) <= 40_000
        assert out["trimmed"], "something had to be dropped, and it must say what"

    async def test_excel_sheets_header_detection_and_dates(self, root, ctx) -> None:
        shared = None
        sheet1 = _sheet_xml(
            _row(1, _c("A1", inline="Quarterly report")),                      # a title line above the header
            _row(2, _c("A2", inline="day"), _c("B2", inline="units"), _c("C2", inline="region")),
            _row(3, _c("A3", "45292", s=1), _c("B3", "5"), _c("C3", inline="EMEA")),   # 2024-01-01
            _row(4, _c("A4", "45293", s=1), _c("B4", "7"), _c("C4", inline="EMEA")),
            _row(5, _c("A5", "45300", s=1), _c("B5", "9"), _c("C5", inline="APAC")),
        )
        sheet2 = _sheet_xml(_row(1, _c("A1", "3"), _c("B1", "4")), _row(2, _c("A2", "5"), _c("B2", "6")))
        put(root, "book.xlsx", _xlsx([("Sales", sheet1), ("Raw", sheet2)], shared=shared))
        tools = make(root)

        out = await call(tools, "inspect_spreadsheet", ctx, path="book.xlsx")
        assert out["format"] == "xlsx" and out["sheets_available"] == ["Sales", "Raw"]
        sales, raw = out["sheets"]
        cols = {c["name"]: c for c in sales["columns"]}
        assert sales["header_detected"] is True and sales["rows"] == 3
        assert cols["day"]["type"] == "date" and (cols["day"]["min"], cols["day"]["max"]) == ("2024-01-01", "2024-01-09")
        assert (cols["units"]["min"], cols["units"]["max"], cols["units"]["mean"]) == (5, 9, 7)
        assert cols["region"]["top_values"][0] == {"value": "EMEA", "count": 2}
        # No text header on the second sheet: its columns are named, every row is data.
        assert raw["header_detected"] is False and [c["name"] for c in raw["columns"]] == ["column_1", "column_2"]
        assert raw["rows"] == 2

        only = await call(tools, "inspect_spreadsheet", ctx, path="book.xlsx", sheet="raw")  # case-insensitive
        assert [s["name"] for s in only["sheets"]] == ["Raw"]
        with pytest.raises(ToolExecutionError, match="no sheet named 'Nope'.*Sales"):
            await tools["inspect_spreadsheet"].execute({"path": "book.xlsx", "sheet": "Nope"}, ctx)

    async def test_refusals(self, root, ctx, monkeypatch) -> None:
        tools = make(root)
        put(root, "old.xls", b"\xd0\xcf\x11\xe0 legacy")
        with pytest.raises(ToolExecutionError, match="unsupported file type '.xls'"):
            await tools["inspect_spreadsheet"].execute({"path": "old.xls"}, ctx)
        put(root, "empty.csv", "")
        with pytest.raises(ToolExecutionError, match="empty"):
            await tools["inspect_spreadsheet"].execute({"path": "empty.csv"}, ctx)
        put(root, "broken.xlsx", b"PK\x03\x04 not really a zip")
        with pytest.raises(ToolExecutionError, match="Excel workbook"):
            await tools["inspect_spreadsheet"].execute({"path": "broken.xlsx"}, ctx)
        monkeypatch.setattr(media, "MAX_TABLE_BYTES", 10)
        put(root, "toobig.csv", CSV)
        with pytest.raises(ToolExecutionError, match="limit"):
            await tools["inspect_spreadsheet"].execute({"path": "toobig.csv"}, ctx)


# ======================================================================================
# parse_document
# ======================================================================================


def scanned_pdf(text_lines: list[tuple[tuple[int, int], str]]) -> bytes:
    """A PDF with no text layer: a raster image of the text, as a scanner produces it."""
    image = Image.open(io.BytesIO(render(text_lines, size=(900, 300))))
    out = io.BytesIO()
    image.save(out, format="PDF")
    return out.getvalue()


class TestParseDocumentPdf:
    async def test_pages_tables_coverage_and_unreadable_pages(self, root, ctx) -> None:
        put(root, "report.pdf", _pdf([{"text": PROSE}, {"text": TABLE}, {}, {"image": True}]))
        out = await call(make(root), "parse_document", ctx, path="report.pdf")

        assert out["source"] == {"path": "report.pdf", "kind": "pdf"}
        cov = out["coverage"]
        assert (cov["unit"], cov["total"], cov["read"]) == ("page", 4, 2)
        assert cov["unreadable"] == [4] and cov["blank"] == [3] and cov["complete"] is False
        assert any("OCR is not configured" in n for n in cov["notes"])
        assert cov["tables_found"] == 1

        kinds = [(b["type"], b.get("page")) for b in out["blocks"]]
        assert kinds == [("paragraph", 1), ("table", 2), ("unreadable_page", 4)]
        assert out["blocks"][0]["text"] == "\n".join(PROSE) and out["blocks"][0]["ref"] == "p.1"
        table = out["blocks"][1]
        assert table["rows"][0] == ["Item", "Qty", "Price"]
        assert table["rows"][1:] == [["Widget", "2", "4.00"], ["Gadget", "1", "9.50"], ["Doohickey", "3", "1.25"]]
        assert table["header_row"] is True
        assert "NOT read" in out["blocks"][2]["text"]
        # The picture on page 4 is listed, with the fact that no text came from it.
        assert out["images"] == [{"page": 4, "name": "Im0", "width": 1, "height": 1,
                                  "page_text_from": "none", "ocr_text": False}]
        assert out["window"] == {"offset": 0, "returned": 3, "total_blocks": 3, "next_offset": None, "complete": True}
        assert any("no bookmarks" in n for n in cov["notes"])

    async def test_a_table_inside_prose_is_found_and_separated(self, root, ctx) -> None:
        page = ["Quarterly summary", "All figures are in thousands.", *TABLE]
        put(root, "mixed.pdf", _pdf([{"text": page}]))
        out = await call(make(root), "parse_document", ctx, path="mixed.pdf")
        assert [b["type"] for b in out["blocks"]] == ["paragraph", "table"]
        assert out["blocks"][0]["text"] == "Quarterly summary\nAll figures are in thousands."
        assert out["blocks"][1]["rows"][3] == ["Doohickey", "3", "1.25"]

    async def test_a_large_table_is_split_without_losing_a_row(self, root, ctx) -> None:
        lines = ["Id      Name      Score"] + [f"{i}      name{i}      {i * 3}.5" for i in range(1, 121)]
        pdf = _pdf([{"text": lines[:40]}, {"text": lines[40:80]}, {"text": lines[80:]}])
        put(root, "big.pdf", pdf)
        tools = make(root)
        rows, offset = [], 0
        while True:
            out = await call(tools, "parse_document", ctx, path="big.pdf", offset=offset)
            rows += [r for b in out["blocks"] if b["type"] == "table" for r in b["rows"]]
            offset = out["window"]["next_offset"]
            if offset is None:
                break
        assert len(rows) == 121 and rows[0] == ["Id", "Name", "Score"] and rows[-1][0] == "120"

    @needs_tesseract
    async def test_a_scanned_page_is_read_by_real_ocr_and_marked_as_such(self, root, ctx) -> None:
        put(root, "scan.pdf", scanned_pdf([((40, 40), "Invoice number 4711"), ((40, 120), "Total due 250 EUR")]))
        tools = make(root, ocr_engine=TesseractOcrEngine(lang="eng"))
        out = await call(tools, "parse_document", ctx, path="scan.pdf")
        cov = out["coverage"]
        assert cov["ocr"] == [1] and cov["unreadable"] == [] and cov["complete"] is True
        (block,) = [b for b in out["blocks"] if b["type"] == "paragraph"]
        assert block["ocr"] is True and "4711" in block["text"] and "250" in block["text"]
        assert any("OCR; their text is approximate" in n for n in cov["notes"])
        assert out["images"][0]["ocr_text"] is True and out["images"][0]["page_text_from"] == "ocr"

    async def test_a_scanned_page_without_an_engine_is_listed_not_read(self, root, ctx) -> None:
        put(root, "scan.pdf", scanned_pdf([((40, 40), "Invoice number 4711")]))
        out = await call(make(root), "parse_document", ctx, path="scan.pdf")
        assert out["coverage"]["unreadable"] == [1] and out["coverage"]["complete"] is False
        assert [b["type"] for b in out["blocks"]] == ["unreadable_page"]

    async def test_a_pdf_that_is_not_a_pdf(self, root, ctx) -> None:
        put(root, "fake.pdf", b"this is not a pdf at all")
        with pytest.raises(ToolExecutionError, match="not a valid PDF"):
            await make(root)["parse_document"].execute({"path": "fake.pdf"}, ctx)
        put(root, "notes.txt", "plain text")
        with pytest.raises(ToolExecutionError, match="not a PDF, .docx or .pptx"):
            await make(root)["parse_document"].execute({"path": "notes.txt"}, ctx)


class TestParseDocumentOffice:
    def _docx_with_picture(self) -> bytes:
        body = (
            _p("Introduction", "Heading1") + _p("Welcome to the handbook.") + _p("More intro text.")
            + _p("Scope", "Heading2") + _p("Applies to everyone.", numbered=True)
            + _table([["Region", "Revenue"], ["EMEA", "100"], ["APAC", "200"]])
            + _p("Figures are in thousands.")
            + _p("Next steps", "Heading1") + _p("Ship it.")
        )
        base = _docx(body, styles=None)
        files: dict[str, bytes | str] = {}
        with zipfile.ZipFile(io.BytesIO(base)) as archive:
            for name in archive.namelist():
                files[name] = archive.read(name)
        rel = ('<Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
               'Target="media/logo.png"/>')
        files["word/_rels/document.xml.rels"] = (
            f'{XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rel}</Relationships>')
        files["word/media/logo.png"] = render([((5, 5), "x")], size=(20, 20))
        return _zip(files)

    async def test_docx_outline_blocks_tables_and_images(self, root, ctx) -> None:
        put(root, "handbook.docx", self._docx_with_picture())
        out = await call(make(root), "parse_document", ctx, path="handbook.docx")

        assert out["source"]["kind"] == "docx" and out["coverage"]["unit"] == "document"
        assert out["coverage"]["complete"] is True
        assert any("do not record page numbers" in n for n in out["coverage"]["notes"])
        titles = [(n["level"], n["title"], [c["title"] for c in n.get("children", [])]) for n in out["outline"]]
        assert titles == [(1, "Introduction", ["Scope"]), (1, "Next steps", [])]
        assert all(n["block"].startswith("b") for n in out["outline"])

        types = [(b["type"], b.get("section")) for b in out["blocks"]]
        assert types == [
            ("heading", "Introduction"), ("paragraph", "Introduction"), ("paragraph", "Introduction"),
            ("heading", "Introduction > Scope"), ("paragraph", "Introduction > Scope"),
            ("table", "Introduction > Scope"), ("paragraph", "Introduction > Scope"),
            ("heading", "Next steps"), ("paragraph", "Next steps"),
        ]
        scope_list = out["blocks"][4]
        assert scope_list["text"] == "- Applies to everyone." and scope_list["list_item"] is True
        assert out["blocks"][5]["rows"] == [["Region", "Revenue"], ["EMEA", "100"], ["APAC", "200"]]
        assert out["blocks"][5]["ref"] == "section: Introduction > Scope"
        assert out["images"] == [{"slide": None, "name": "logo.png", "page_text_from": "not_applicable", "ocr_text": False}]

    async def test_pptx_slides_titles_notes_and_picture_only_slides(self, root, ctx) -> None:
        slides = [
            (_slide(_shape(["Roadmap"], "title"), _shape(["Q1: ship search", "Q2: ship voice"])), _notes(["Mention the pilot."])),
            (_slide(_shape(["Pricing"], "title"), _shape(["Free", "Team"])), None),
            (_slide('<p:pic><p:nvPicPr><p:cNvPr id="4" name="chart"/></p:nvPicPr></p:pic>'), None),
        ]
        put(root, "deck.pptx", _pptx(slides))
        out = await call(make(root), "parse_document", ctx, path="deck.pptx")

        cov = out["coverage"]
        assert (cov["unit"], cov["total"], cov["unreadable"]) == ("slide", 3, [3])
        assert cov["complete"] is False and any("Slides [3]" in n for n in cov["notes"])
        assert [(n["title"], n["slide"]) for n in out["outline"]] == [("Roadmap", 1), ("Pricing", 2)]
        by_type = [(b["type"], b.get("slide")) for b in out["blocks"]]
        assert by_type == [("heading", 1), ("paragraph", 1), ("notes", 1), ("heading", 2), ("paragraph", 2),
                           ("unreadable_page", 3)]
        assert out["blocks"][2]["text"] == "Mention the pilot."
        assert out["blocks"][1]["ref"] == "slide 1"

    async def test_windows_continue_without_dropping_or_repeating_anything(self, root, ctx) -> None:
        paragraphs = [f"Paragraph {i}: " + ("lorem ipsum " * 25).strip() for i in range(240)]
        put(root, "long.docx", _docx(_p("Big document", "Heading1") + "".join(_p(t) for t in paragraphs)))
        tools = make(root)

        first = json.loads(await tools["parse_document"].execute({"path": "long.docx"}, ctx))
        assert first["window"]["complete"] is False and "outline" in first
        assert f"offset={first['window']['next_offset']}" in first["continue"]
        seen: list[str] = []
        offset, windows = 0, 0
        while offset is not None:
            raw = await tools["parse_document"].execute({"path": "long.docx", "offset": offset}, ctx)
            assert len(raw) <= 40_000
            out = json.loads(raw)
            if offset:
                assert "outline" not in out and "images" not in out  # only the first window carries them
            seen += [b["text"] for b in out["blocks"] if b["type"] == "paragraph"]
            offset = out["window"]["next_offset"]
            windows += 1
        assert windows >= 3
        assert seen == paragraphs

    async def test_an_offset_past_the_end_is_an_error(self, root, ctx) -> None:
        put(root, "t.docx", _docx(_p("Only paragraph")))
        with pytest.raises(ToolExecutionError, match="past the end"):
            await make(root)["parse_document"].execute({"path": "t.docx", "offset": 5}, ctx)

    async def test_a_corrupt_office_file_is_a_clean_error(self, root, ctx) -> None:
        put(root, "bad.docx", b"PK\x03\x04 nonsense")
        with pytest.raises(ToolExecutionError, match="not a PDF, .docx or .pptx"):
            await make(root)["parse_document"].execute({"path": "bad.docx"}, ctx)


# ======================================================================================
# ocr_image
# ======================================================================================


class ScriptedOcr:
    """A structured OCR double returning a fixed result (and recording how it was called). Used only for
    the confidence rules and the language plumbing, which tesseract cannot be made to produce on demand."""

    name = "scripted-ocr"
    lang = "eng"

    def __init__(self, result: OcrResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def image_text(self, image: bytes, *, page: int) -> str:
        return self.result.text

    async def image_data(self, image: bytes, *, page: int, lang: str | None = None) -> OcrResult:
        self.calls.append({"page": page, "lang": lang})
        return self.result


def ocr_result(*lines: tuple[str, float | None], size=(400, 100)) -> OcrResult:
    built = []
    for i, (text, conf) in enumerate(lines):
        words = tuple(OcrWord(w, conf, OcrBox(10 + 60 * j, 10 + 30 * i, 50, 20)) for j, w in enumerate(text.split()))
        built.append(OcrLine(text, words, conf, OcrBox(10, 10 + 30 * i, 60 * len(words), 20)))
    known = [c for _, c in lines if c is not None]
    return OcrResult("\n".join(t for t, _ in lines), tuple(built), sum(known) / len(known) if known else None, *size)


class TestOcrImage:
    @needs_tesseract
    async def test_real_tesseract_text_confidence_and_boxes(self, root, ctx) -> None:
        put(root, "mock.png", MOCKUP)
        tools = make(root, ocr_engine=TesseractOcrEngine(lang="eng"))
        out = await call(tools, "ocr_image", ctx, path="mock.png")

        assert out["structured"] is True and out["image"] == {"format": "PNG", "width": 900, "height": 500}
        assert "Sign in to AURA" in out["text"] and "LOGIN" in out["text"]
        assert out["mean_confidence"] > 80 and out["line_count"] >= 5
        assert out["lang"] == "eng" and out["engine"].startswith("tesseract")
        for line in out["lines"]:
            left, top, width, height = line["box"]
            assert 0 <= left and left + width <= 900 and 0 <= top and top + height <= 500
            assert 0 <= line["confidence"] <= 100
        words = {w["text"]: w for w in out["words"]}
        assert "LOGIN" in words and words["LOGIN"]["box"][1] > 350  # near the bottom of the mockup
        assert out["low_confidence_word_count"] == 0 and out["low_confidence_words"] == []
        assert out["text_truncated"] is False and out["lines_truncated"] is False

    @needs_tesseract
    async def test_words_can_be_left_out(self, root, ctx) -> None:
        put(root, "mock.png", MOCKUP)
        out = await call(make(root, ocr_engine=TesseractOcrEngine(lang="eng")), "ocr_image", ctx,
                         path="mock.png", include_words=False)
        assert "words" not in out and out["lines"]

    @needs_tesseract
    async def test_arabic_plus_english_is_well_formed(self, root, ctx) -> None:
        """Content is not asserted (an unshaped Arabic rendering is not what a scanner produces): the
        contract is that ``ara+eng`` runs and the result is complete and well-formed."""
        arabic = "مرحبا بالعالم"
        image = Image.new("RGB", (700, 200), "white")
        ImageDraw.Draw(image).text(
            (40, 40), arabic + "  Hello 123", fill="black",
            font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        put(root, "ar.png", buffer.getvalue())
        engine = TesseractOcrEngine(lang="eng")
        if not {"ara", "eng"} <= set(engine.installed_languages()):
            pytest.skip("tesseract Arabic data is not installed")
        out = await call(make(root, ocr_engine=engine), "ocr_image", ctx, path="ar.png", lang="ara+eng")
        assert out["lang"] == "ara+eng" and out["structured"] is True
        assert isinstance(out["text"], str) and isinstance(out["lines"], list)
        assert out["mean_confidence"] is None or 0 <= out["mean_confidence"] <= 100
        assert out["word_count"] == len(out["words"])

    @needs_tesseract
    async def test_a_two_letter_code_and_an_uninstalled_language(self, root, ctx) -> None:
        put(root, "mock.png", MOCKUP)
        tools = make(root, ocr_engine=TesseractOcrEngine(lang="eng"))
        assert (await call(tools, "ocr_image", ctx, path="mock.png", lang="en"))["lang"] == "eng"
        with pytest.raises(ToolExecutionError, match="not installed.*installed:"):
            await tools["ocr_image"].execute({"path": "mock.png", "lang": "jpn"}, ctx)
        with pytest.raises(ToolExecutionError, match="not a tesseract language code"):
            await tools["ocr_image"].execute({"path": "mock.png", "lang": "english please"}, ctx)

    async def test_low_confidence_words_and_the_mean_are_flagged(self, root, ctx) -> None:
        """Scripted OCR: the confidence rules, with figures chosen to cross the threshold."""
        put(root, "a.png", render([((5, 5), "x")], size=(400, 100)))
        engine = ScriptedOcr(ocr_result(("Invoice total", 95.0), ("sma11 smudged text", 41.0)))
        out = await call(make(root, ocr_engine=engine), "ocr_image", ctx, path="a.png", min_confidence=60)
        assert out["low_confidence_word_count"] == 3
        assert [w["text"] for w in out["low_confidence_words"]] == ["sma11", "smudged", "text"]
        assert all(w["confidence"] == 41.0 for w in out["low_confidence_words"])
        assert any("3 word(s) are below 60%" in w for w in out["warnings"])
        assert out["mean_confidence"] == 68.0 and engine.calls == [{"page": 1, "lang": None}]

    async def test_no_text_has_no_confidence_which_is_not_zero(self, root, ctx) -> None:
        put(root, "blank.png", render([], size=(200, 100)))
        out = await call(make(root, ocr_engine=ScriptedOcr(ocr_result())), "ocr_image", ctx, path="blank.png")
        assert out["mean_confidence"] is None and out["lines"] == []
        assert any("No text was recognised" in w and "not the same as 0" in w for w in out["warnings"])

    async def test_a_plain_text_only_engine_is_reported_as_such(self, root, ctx) -> None:
        class TextOnly:
            name = "text-only"

            async def image_text(self, image, *, page):
                return "just text"

        put(root, "a.png", render([((5, 5), "x")], size=(100, 50)))
        out = await call(make(root, ocr_engine=TextOnly()), "ocr_image", ctx, path="a.png")
        assert out["structured"] is False and out["text"] == "just text"
        assert "text only" in out["warnings"][0]
        with pytest.raises(ToolExecutionError, match="cannot change language"):
            await make(root, ocr_engine=TextOnly())["ocr_image"].execute({"path": "a.png", "lang": "eng"}, ctx)

    async def test_a_large_result_degrades_in_stated_steps(self, root, ctx) -> None:
        lines = [(" ".join(f"w{i}x{j}" for j in range(12)), 55.0) for i in range(260)]
        put(root, "dense.png", render([((5, 5), "x")], size=(100, 50)))
        raw = await make(root, ocr_engine=ScriptedOcr(ocr_result(*lines)))["ocr_image"].execute(
            {"path": "dense.png"}, ctx)
        out = json.loads(raw)
        assert len(raw) <= 40_000 and out["trimmed"]
        assert out["word_count"] == 260 * 12  # the totals are never trimmed, only the listings

    async def test_non_images_and_huge_images_are_refused_with_a_reason(self, root, ctx, monkeypatch) -> None:
        tools = make(root, ocr_engine=ScriptedOcr(ocr_result()))
        put(root, "not.png", b"this is plain text pretending to be an image")
        with pytest.raises(ToolExecutionError, match="not an image file"):
            await tools["ocr_image"].execute({"path": "not.png"}, ctx)
        put(root, "doc.pdf", _pdf([{"text": PROSE}]))
        with pytest.raises(ToolExecutionError, match="use parse_document"):
            await tools["ocr_image"].execute({"path": "doc.pdf"}, ctx)

        big = io.BytesIO()
        Image.new("L", (6200, 6200), 255).save(big, "PNG")  # 38 megapixels, a few KB on disk
        put(root, "huge.png", big.getvalue())
        with pytest.raises(ToolExecutionError, match=r"megapixels.*downscale"):
            await tools["ocr_image"].execute({"path": "huge.png"}, ctx)

        monkeypatch.setattr(media, "_MAX_IMAGE_BYTES", 100)
        put(root, "bytes.png", MOCKUP)
        with pytest.raises(ToolExecutionError, match="limit"):
            await tools["ocr_image"].execute({"path": "bytes.png"}, ctx)

    async def test_an_animated_image_says_only_the_first_frame_was_read(self, root, ctx) -> None:
        frames = [Image.new("RGB", (60, 40), c) for c in ("white", "black")]
        gif = io.BytesIO()
        frames[0].save(gif, "GIF", save_all=True, append_images=frames[1:])
        put(root, "anim.gif", gif.getvalue())
        out = await call(make(root, ocr_engine=ScriptedOcr(ocr_result(("hi", 90.0)))), "ocr_image", ctx, path="anim.gif")
        assert out["image"]["frames"] == 2 and any("only the first" in w for w in out["warnings"])


# ======================================================================================
# analyze_image_requirements
# ======================================================================================

GOOD_REQUIREMENTS = {
    "screens": [{
        "name": "Sign in", "description": "Credentials form",
        "regions": [{"name": "content", "components": [
            {"type": "heading", "label": "Sign in to AURA"},
            {"type": "input", "label": "Email", "confidence": 0.9},
            {"type": "input", "label": "Password"},
            {"type": "button", "label": "LOGIN", "bounds": {"x": 0.05, "y": 0.78, "width": 0.11, "height": 0.05}},
            {"type": "link", "label": "Forgot password?"},
        ]}],
    }],
    "texts": ["Sign in to AURA", "Email", "Password", "LOGIN", "Forgot password?"],
    "open_questions": ["Is there a rate limit on failed logins?"],
}


@pytest.fixture
def vision_container(monkeypatch, root):
    from aura_core.config.settings import load_settings
    from aura_core.container import Container

    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AURA_VISION_ENABLED", "true")
    monkeypatch.setenv("AURA_TOOLS_FILES_ROOT", str(root))
    return Container(load_settings(refresh=True, validate=False))


class TestAnalyzeImageRequirements:
    @needs_tesseract
    async def test_the_ocr_fallback_on_a_real_mockup(self, root, ctx) -> None:
        put(root, "mock.png", MOCKUP)
        tools = make(root, ocr_engine=TesseractOcrEngine(lang="eng"))
        out = await call(tools, "analyze_image_requirements", ctx, path="mock.png")

        assert out["source"] == "ocr_heuristic" and out["image"]["width"] == 900
        assert out["summary"]["buttons"] == ["LOGIN"]
        assert out["summary"]["inputs"] == ["Email", "Password"]
        assert out["summary"]["links"] == ["Forgot password?"]
        assert out["summary"]["headings"] == ["Sign in to AURA"]
        (screen,) = out["screens"]
        assert screen["name"] == "Sign in to AURA"
        regions = {r["name"]: r for r in screen["regions"]}
        assert [c["label"] for c in regions["header"]["components"]] == ["Sign in to AURA"]
        login = next(c for c in regions["content"]["components"] if c["label"] == "LOGIN")
        assert login["type"] == "button" and login["evidence"] == "button wording"
        assert 0.7 < login["bounds"]["y"] < 0.85 and login["confidence"] > 0.8  # bottom of the image
        assert "Sign in to AURA" in out["texts"]
        assert any("OCR text only" in q and "guess" in q for q in out["open_questions"])
        assert any("Field constraints" in q for q in out["open_questions"])  # there are inputs

    async def test_heuristic_rules_on_synthetic_ocr(self) -> None:
        from aura_core.vision.requirements import heuristic_requirements

        def line(text, left, top, w=120, h=24, conf=90.0):
            return OcrLine(text, (OcrWord(text, conf, OcrBox(left, top, w, h)),), conf, OcrBox(left, top, w, h))

        lines = (
            line("Home", 20, 10), line("Products", 220, 10), line("Pricing", 420, 10),      # a nav row
            line("Welcome back", 40, 90, 300, 44),                                          # taller: heading
            line("Nickname", 40, 170), line("____________", 220, 172),                      # label + blank field
            line("[x] Remember me", 40, 230), line("SUBMIT", 40, 300),
            line("www.example.com/terms", 40, 340, 260), line("Terms of service", 40, 380),
            line("(c) 2025 Example", 40, 470, conf=45.0),
        )
        out = heuristic_requirements(OcrResult("\n".join(l.text for l in lines), lines, 80.0, 800, 500))
        comps = {c["label"]: c for r in out["screens"][0]["regions"] for c in r["components"]}
        assert {k: comps[k]["type"] for k in ("Home", "Products", "Pricing")} == {
            "Home": "navigation", "Products": "navigation", "Pricing": "navigation"}
        assert comps["Welcome back"]["type"] == "heading"
        assert comps["Nickname"]["type"] == "input" and "blank field" in comps["Nickname"]["evidence"]
        assert "____________" not in comps
        assert comps["Remember me"]["type"] == "checkbox"
        assert comps["SUBMIT"]["type"] == "button"
        assert comps["www.example.com/terms"]["type"] == "link" and comps["Terms of service"]["type"] == "link"
        assert any("low confidence" in q and "(c) 2025" in q for q in out["open_questions"])
        names = [r["name"] for r in out["screens"][0]["regions"]]
        assert names[0] == "header" and names[-1] == "footer"

    async def test_nothing_to_read_says_a_model_is_needed(self) -> None:
        from aura_core.vision.requirements import heuristic_requirements

        out = heuristic_requirements(OcrResult("", (), None, 100, 100))
        assert out["screens"][0]["regions"] == [] and "vision model is needed" in out["open_questions"][0]

    async def test_the_vlm_path_through_the_real_container(self, vision_container, root, ctx) -> None:
        from aura_core.llm.providers.echo import ScriptedTurn

        put(root, "mock.png", MOCKUP)
        container = vision_container
        await container.startup()
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text="```json\n" + json.dumps(GOOD_REQUIREMENTS) + "\n```"))
        result, _ = await container.tools.execute(
            ToolCall(id=ToolCallId("t1"), name="analyze_image_requirements",
                     arguments={"path": "mock.png", "focus": "the login form"}), ctx)
        assert result.ok, result.content
        out = json.loads(result.content)

        assert out["source"] == "vlm" and out["vlm"] == {"provider": "echo", "model": "echo-1", "repaired": False}
        assert out["summary"]["buttons"] == ["LOGIN"] and out["summary"]["links"] == ["Forgot password?"]
        assert out["screens"][0]["regions"][0]["components"][3]["bounds"] == {
            "x": 0.05, "y": 0.78, "width": 0.11, "height": 0.05}
        assert out["open_questions"] == ["Is there a rate limit on failed logins?"]
        (request,) = echo.calls
        assert len(request.images) == 1 and request.images[0].media_type in ("image/png", "image/jpeg")
        sent = "\n".join(m.content for m in request.conversation.messages)
        assert "ONE JSON object" in sent and "the login form" in sent
        assert request.data_classification.name == "SENSITIVE"

    async def test_an_invalid_reply_is_repaired_once_with_the_errors_quoted(self, vision_container, root, ctx) -> None:
        from aura_core.llm.providers.echo import ScriptedTurn

        put(root, "mock.png", MOCKUP)
        container = vision_container
        await container.startup()
        echo = container.router.get("echo")
        broken = {"screens": [{"name": "Sign in", "regions": [{"name": "middle", "components": [
            {"type": "widget", "label": "x"}]}]}]}
        echo.queue(ScriptedTurn(text=json.dumps(broken)), ScriptedTurn(text=json.dumps(GOOD_REQUIREMENTS)))
        result, _ = await container.tools.execute(
            ToolCall(id=ToolCallId("t2"), name="analyze_image_requirements", arguments={"path": "mock.png"}), ctx)
        out = json.loads(result.content)

        assert out["source"] == "vlm" and out["vlm"]["repaired"] is True
        assert len(echo.calls) == 2
        repair = "\n".join(m.content for m in echo.calls[1].conversation.messages)
        assert "regions[0].name: 'middle' is not one of" in repair
        assert "components[0].type: 'widget' is not one of" in repair

    @needs_tesseract
    async def test_a_reply_that_stays_invalid_falls_back_to_ocr_and_says_so(self, vision_container, root, ctx) -> None:
        from aura_core.llm.providers.echo import ScriptedTurn

        put(root, "mock.png", MOCKUP)
        container = vision_container
        await container.startup()
        assert container.ocr_engine is not None
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text="I cannot do that"), ScriptedTurn(text='{"screens": []}'))
        result, _ = await container.tools.execute(
            ToolCall(id=ToolCallId("t3"), name="analyze_image_requirements", arguments={"path": "mock.png"}), ctx)
        out = json.loads(result.content)

        assert out["source"] == "ocr_heuristic" and len(echo.calls) == 2
        assert any("did not match the schema after one repair" in w for w in out["warnings"])
        assert "Fell back to the OCR heuristic." in out["warnings"]

    async def test_without_any_fallback_an_invalid_reply_is_an_error(self, vision_container, root, ctx) -> None:
        from aura_core.llm.providers.echo import ScriptedTurn

        put(root, "mock.png", MOCKUP)
        container = vision_container
        await container.startup()
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text="nope"), ScriptedTurn(text="still nope"))
        tools = make(root, vision_engine=container.vision, image_normalizer=container.image_normalizer)
        with pytest.raises(ToolExecutionError, match="could not be analysed"):
            await tools["analyze_image_requirements"].execute({"path": "mock.png"}, ctx)

    @needs_tesseract
    async def test_method_ocr_never_calls_the_model_and_method_vlm_needs_one(self, vision_container, root, ctx) -> None:
        put(root, "mock.png", MOCKUP)
        container = vision_container
        await container.startup()
        echo = container.router.get("echo")
        out = json.loads((await container.tools.execute(
            ToolCall(id=ToolCallId("t4"), name="analyze_image_requirements",
                     arguments={"path": "mock.png", "method": "ocr"}), ctx))[0].content)
        assert out["source"] == "ocr_heuristic" and echo.calls == []

        tools = make(root, ocr_engine=TesseractOcrEngine(lang="eng"))
        with pytest.raises(ToolExecutionError, match="no vision model is configured"):
            await tools["analyze_image_requirements"].execute({"path": "mock.png", "method": "vlm"}, ctx)

    async def test_a_text_only_deployment_uses_ocr_without_asking_a_model(self, monkeypatch, root, ctx) -> None:
        """Vision on, but the only provider is text-only: no image can be sent, so the tool does not try."""
        from aura_core.config.settings import load_settings
        from aura_core.container import Container
        from aura_core.domain import ProviderCapabilities
        from aura_core.domain.llm import Modality

        root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("AURA_VISION_ENABLED", "true")
        monkeypatch.setenv("AURA_TOOLS_FILES_ROOT", str(root))
        container = Container(load_settings(refresh=True, validate=False))
        await container.startup()
        echo = container.router.get("echo")
        echo._capabilities = ProviderCapabilities(
            model_id="echo-1", context_window=8192, max_output_tokens=2048, is_local=True,
            modalities=(Modality.TEXT,))
        assert container.vision.has_vision_provider() is False
        if container.ocr_engine is None:
            pytest.skip("tesseract is not available")
        put(root, "mock.png", MOCKUP)
        out = json.loads((await container.tools.execute(
            ToolCall(id=ToolCallId("t5"), name="analyze_image_requirements", arguments={"path": "mock.png"}), ctx))[0].content)
        assert out["source"] == "ocr_heuristic" and echo.calls == []

    def test_validation_names_every_problem_by_path(self) -> None:
        from aura_core.vision.requirements import extract_json_object, validate_requirements

        ok, errors, warnings = validate_requirements(GOOD_REQUIREMENTS)
        assert ok is not None and errors == [] and ok["source"] == "vlm"
        assert validate_requirements([])[1] == ["the reply must be a JSON object"]
        assert validate_requirements({"screens": []})[1] == ["screens: must be a non-empty array"]
        bad = {"screens": [{"regions": [{"name": "content", "components": [{"type": "button"}, "oops"]}]},
                           {"name": "S2", "regions": "none"}], "texts": [1], "open_questions": "q"}
        parsed, errors, _ = validate_requirements(bad)
        assert parsed is None
        assert "screens[0].name: is required" in errors
        assert "screens[0].regions[0].components[1]: must be an object" in errors
        assert "screens[1].regions: must be an array" in errors
        assert "texts: must be an array of strings" in errors and "open_questions: must be an array of strings" in errors
        # Out-of-range bounds are dropped with a warning, not a failure.
        soft = {"screens": [{"name": "S", "regions": [{"name": "content", "components": [
            {"type": "button", "label": "Go", "bounds": {"x": 5, "y": 0, "width": 1, "height": 1}, "confidence": 7}]}]}]}
        parsed, errors, warnings = validate_requirements(soft)
        assert errors == [] and parsed["screens"][0]["regions"][0]["components"][0]["bounds"] is None
        assert len(warnings) == 2
        assert extract_json_object('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
        assert extract_json_object('text {"a": {"b": 2}} trailing') == {"a": {"b": 2}}
        with pytest.raises(ValueError):
            extract_json_object("no json here")


# ======================================================================================
# transcribe_audio
# ======================================================================================


class FakeWhisperModelEngine:
    """A ``TimestampedSpeechToText`` built on the real ``map_transcript`` over faster-whisper's own result
    shape (``segments`` with ``words``, ``info`` with ``language``/``duration``): faster-whisper is not
    installable in this environment, so the model is a stand-in with the library's documented fields, and
    everything downstream of it -- the mapping, the tool, the windowing -- is the real code."""

    name = "faster-whisper-fake"
    multilingual = True

    def __init__(self, n_segments: int = 3) -> None:
        self.n = n_segments
        self.calls: list[dict] = []

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return "unused"

    async def transcribe_segments(self, audio: bytes, *, language: str | None = None, word_timestamps: bool = True):
        from types import SimpleNamespace as NS

        from aura_core.adapters.whisper_stt import map_transcript

        self.calls.append({"bytes": len(audio), "language": language, "words": word_timestamps})
        segments = [
            NS(start=i * 2.0, end=i * 2.0 + 1.8, text=f" Sentence number {i} of the recording. ",
               avg_logprob=-0.2, no_speech_prob=0.05,
               words=[NS(start=i * 2.0, end=i * 2.0 + 0.5, word=" Sentence", probability=0.97),
                      NS(start=i * 2.0 + 0.5, end=i * 2.0 + 1.0, word=" number", probability=0.9)] if word_timestamps else None)
            for i in range(self.n)
        ]
        return map_transcript(iter(segments), NS(language="en", language_probability=0.99, duration=self.n * 2.0),
                              language=language)


def wav_bytes(seconds: float = 0.2) -> bytes:
    import wave

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return out.getvalue()


class TestTranscribeAudio:
    async def test_segments_words_language_and_no_speakers(self, root, ctx) -> None:
        put(root, "note.wav", wav_bytes())
        engine = FakeWhisperModelEngine()
        out = await call(make(root, stt_engine=engine), "transcribe_audio", ctx, path="note.wav", include_words=True)

        assert out["source"] == {"path": "note.wav", "kind": "audio"}
        assert out["engine"] == "faster-whisper-fake" and out["language"] == "en" and out["duration"] == 6.0
        assert out["language_probability"] == 0.99
        assert out["text"] == "Sentence number 0 of the recording. Sentence number 1 of the recording. Sentence number 2 of the recording."
        seg = out["segments"][1]
        assert (seg["start"], seg["end"], seg["text"]) == (2.0, 3.8, "Sentence number 1 of the recording.")
        assert seg["speaker"] is None  # never invented
        assert seg["confidence"] == pytest.approx(0.7778, abs=1e-3)  # exp(-0.2) * (1 - 0.05)
        assert seg["words"][0] == {"start": 2.0, "end": 2.5, "text": "Sentence", "confidence": 0.97}
        assert out["diarization"]["available"] is False and "speaker" in out["diarization"]["reason"]
        assert out["window"] == {"offset": 0, "returned": 3, "total_segments": 3, "next_offset": None, "complete": True}
        assert engine.calls == [{"bytes": len(wav_bytes()), "language": None, "words": True}]

    async def test_words_are_off_by_default_and_a_language_hint_passes_through(self, root, ctx) -> None:
        put(root, "note.wav", wav_bytes())
        engine = FakeWhisperModelEngine()
        out = await call(make(root, stt_engine=engine), "transcribe_audio", ctx, path="note.wav", language="AR-eg")
        assert "words" not in out["segments"][0]
        assert engine.calls[0]["language"] == "ar" and engine.calls[0]["words"] is False
        english_only = FakeWhisperModelEngine()
        english_only.multilingual = False
        await call(make(root, stt_engine=english_only), "transcribe_audio", ctx, path="note.wav", language="ar")
        assert english_only.calls[0]["language"] is None  # an English-only model ignores the hint

    async def test_a_long_transcript_comes_back_in_windows_without_loss(self, root, ctx) -> None:
        put(root, "long.wav", wav_bytes())
        engine = FakeWhisperModelEngine(n_segments=900)
        tools = make(root, stt_engine=engine)
        texts, offset, windows = [], 0, 0
        while offset is not None:
            raw = await tools["transcribe_audio"].execute({"path": "long.wav", "offset": offset}, ctx)
            assert len(raw) <= 40_000
            out = json.loads(raw)
            texts += [s["text"] for s in out["segments"]]
            if offset:
                assert "text" not in out  # the flat text rides on the first window only
            offset = out["window"]["next_offset"]
            windows += 1
        assert windows >= 3 and len(texts) == 900 and texts[899] == "Sentence number 899 of the recording."
        assert len(engine.calls) == 1  # one transcription, several windows
        with pytest.raises(ToolExecutionError, match="past the end"):
            await tools["transcribe_audio"].execute({"path": "long.wav", "offset": 901}, ctx)

    async def test_silence_is_an_empty_transcript_with_a_note(self, root, ctx) -> None:
        put(root, "quiet.wav", wav_bytes())
        out = await call(make(root, stt_engine=FakeWhisperModelEngine(n_segments=0)), "transcribe_audio", ctx, path="quiet.wav")
        assert out["segments"] == [] and out["note"] == "No speech was detected in the audio."

    async def test_a_video_without_ffmpeg_is_a_clear_error_never_a_fake(self, root, ctx) -> None:
        put(root, "clip.mp4", b"\x00\x00\x00\x18ftypmp42 not a real video")
        tools = make(root, stt_engine=FakeWhisperModelEngine(), ffmpeg_path="")
        with pytest.raises(ToolExecutionError, match="ffmpeg is not installed"):
            await tools["transcribe_audio"].execute({"path": "clip.mp4"}, ctx)

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
    async def test_a_video_track_is_extracted_with_real_ffmpeg(self, root, ctx, tmp_path) -> None:
        import subprocess

        target = root / "clip.mp4"
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
             "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1.5", "-shortest", "-c:v", "mpeg4", "-c:a", "aac",
             str(target)], check=True)
        engine = FakeWhisperModelEngine()
        out = await call(make(root, stt_engine=engine), "transcribe_audio", ctx, path="clip.mp4")
        assert out["source"]["kind"] == "video" and out["source"]["audio_extracted_with"] == "ffmpeg"
        # What the engine received is a real 16 kHz mono WAV about 1.5 s long: 44 header bytes + 48,000.
        assert 40_000 < engine.calls[0]["bytes"] < 60_000
        assert out["segments"]

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
    async def test_a_video_with_no_audio_track_is_an_error(self, root, ctx) -> None:
        import subprocess

        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                        "-c:v", "mpeg4", str(root / "mute.mp4")], check=True)
        with pytest.raises(ToolExecutionError, match="no audio track"):
            await make(root, stt_engine=FakeWhisperModelEngine())["transcribe_audio"].execute({"path": "mute.mp4"}, ctx)

    async def test_other_file_types_and_bad_languages_and_oversize(self, root, ctx, monkeypatch) -> None:
        tools = make(root, stt_engine=FakeWhisperModelEngine())
        put(root, "notes.txt", "hello")
        with pytest.raises(ToolExecutionError, match="unsupported file type"):
            await tools["transcribe_audio"].execute({"path": "notes.txt"}, ctx)
        put(root, "a.wav", wav_bytes())
        with pytest.raises(ToolExecutionError, match="ISO-639-1"):
            await tools["transcribe_audio"].execute({"path": "a.wav", "language": "klingon!"}, ctx)
        monkeypatch.setattr(media, "_MAX_AUDIO_BYTES", 100)
        with pytest.raises(ToolExecutionError, match="limit"):
            await tools["transcribe_audio"].execute({"path": "a.wav"}, ctx)

    async def test_a_decode_failure_is_a_clean_tool_error(self, root, ctx) -> None:
        class Broken(FakeWhisperModelEngine):
            async def transcribe_segments(self, audio, *, language=None, word_timestamps=True):
                raise RuntimeError("av.error.InvalidDataError: nope")

        put(root, "bad.wav", b"not audio")
        with pytest.raises(ToolExecutionError, match="could not decode bad.wav"):
            await make(root, stt_engine=Broken())["transcribe_audio"].execute({"path": "bad.wav"}, ctx)
