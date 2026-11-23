"""ADR-040 building blocks beneath the tools: table finding in PDF text, delimiter sniffing and column
statistics (``knowledge/tabular.py``), per-call OCR language, ``VisionEngine.analyze``, and
``pdf_image_inventory`` / ``office_images``."""

from __future__ import annotations

import statistics

import pytest

from aura_core.domain.errors import DependencyUnavailableError
from aura_core.domain.images import ImageInput
from aura_core.knowledge.documents import (
    ExtractedBlock,
    Modality,
    extract_pdf,
    find_text_tables,
    pdf_image_inventory,
    split_cells,
)
from aura_core.knowledge.tabular import friendly_type, profile_column, sniff_delimiter
from tests.unit.test_document_extraction import TABLE, _pdf


class TestFindTextTables:
    def test_cells_split_at_column_gaps_and_a_header_is_guessed(self) -> None:
        (table,) = find_text_tables("\n".join(TABLE))
        assert table.rows[0] == ("Item", "Qty", "Price") and table.header is True
        assert table.rows[2] == ("Gadget", "1", "9.50")
        assert (table.first_line, table.last_line) == (0, 3)

    def test_a_two_column_table_is_found_where_the_page_heuristic_needs_three(self) -> None:
        text = "Name      Value\nalpha      1\nbeta      2\ngamma      3"
        (table,) = find_text_tables(text)
        assert [len(r) for r in table.rows] == [2, 2, 2, 2]

    def test_prose_with_one_wide_space_is_not_a_table(self) -> None:
        assert find_text_tables("This is a paragraph of text.\nIt has a  double space here and there.\nAnd a third line.") == ()

    def test_a_table_amid_prose_keeps_its_edges_and_prose_stays_out(self) -> None:
        text = "\n".join(["Summary of results", "see below   for details", *TABLE, "Notes follow the table."])
        (table,) = find_text_tables(text)
        assert table.rows[0] == ("Item", "Qty", "Price") and len(table.rows) == 4
        assert text.splitlines()[table.first_line] == TABLE[0]

    def test_two_tables_on_one_page(self) -> None:
        text = "\n".join([*TABLE, "", "Region      Total", "EMEA      100", "APAC      200", "LATAM      50"])
        first, second = find_text_tables(text)
        assert len(first.rows) == 4 and len(second.rows) == 4 and second.rows[0] == ("Region", "Total")

    def test_a_numeric_first_row_is_not_called_a_header(self) -> None:
        (table,) = find_text_tables("1      2      3\n4      5      6\n7      8      9")
        assert table.header is False

    def test_split_cells(self) -> None:
        assert split_cells("  a   b\tc  ") == ("a", "b", "c") and split_cells("single words only") == ("single words only",)

    def test_a_table_page_in_a_pdf_now_carries_rows_and_unchanged_text(self) -> None:
        extraction = extract_pdf(_pdf([{"text": TABLE}]))
        (block,) = extraction.blocks
        assert block.modality == Modality.TABLE
        assert block.rows[0] == ("Item", "Qty", "Price") and len(block.rows) == 4
        assert block.text.splitlines()[1].split() == ["Widget", "2", "4.00"]  # ingestion text is as it was


class TestTabular:
    def test_sniffing(self) -> None:
        assert sniff_delimiter(b"a,b,c\n1,2,3\n") == ","
        assert sniff_delimiter(b"a;b;c\n1;2;3\n") == ";"
        assert sniff_delimiter(b"a\tb\n1\t2\n") == "\t"
        assert sniff_delimiter(b"a|b\n1|2\n") == "|"
        assert sniff_delimiter(b"one column\nonly\n") == ","
        assert sniff_delimiter(b"") == ","
        # A comma that appears in a text field does not outvote the real separator.
        assert sniff_delimiter(b'name;note\n"x, y";z\n"p, q";r\n') == ";"

    def test_numeric_statistics_match_the_standard_library(self) -> None:
        values = [3, 9, 1, 14, 7, None, 5]
        stats = profile_column(values, "integer")
        present = [v for v in values if v is not None]
        assert stats["non_null"] == 6 and stats["null"] == 1 and stats["distinct"] == 6
        assert (stats["min"], stats["max"]) == (1, 14)
        assert stats["mean"] == pytest.approx(statistics.fmean(present), abs=1e-5)
        assert stats["median"] == statistics.median(present)
        assert stats["stddev"] == pytest.approx(statistics.stdev(present), abs=1e-5)

    def test_one_value_has_no_standard_deviation_and_an_empty_column_has_no_summary(self) -> None:
        assert profile_column([4.5], "numeric")["stddev"] is None
        empty = profile_column([None, None], "text")
        assert empty == {"non_null": 0, "null": 2, "distinct": 0}

    def test_dates_booleans_and_the_type_names(self) -> None:
        assert profile_column(["2024-05-01", "2023-01-09", None], "date")["min"] == "2023-01-09"
        assert profile_column([True, False, True], "boolean") == {
            "non_null": 3, "null": 0, "distinct": 2, "true": 2, "false": 1}
        assert [friendly_type(t) for t in ("bigint", "numeric", "timestamp without time zone", "text")] == [
            "integer", "number", "datetime", "text"]

    def test_top_values_are_capped_and_clipped(self) -> None:
        values = [f"v{i}" for i in range(50)] + ["x" * 500]
        stats = profile_column(values, "text", top_n=3)
        assert len(stats["top_values"]) == 3
        assert all(len(str(t["value"])) <= 80 for t in profile_column(["y" * 500], "text")["top_values"])


class TestOcrLanguage:
    def test_resolve_language(self) -> None:
        pytest.importorskip("pytesseract")
        from aura_core.adapters.tesseract_ocr import TesseractOcrEngine, tesseract_available

        if not tesseract_available():
            pytest.skip("tesseract is not installed")
        engine = TesseractOcrEngine(lang="eng")
        assert engine.resolve_language(None) == "eng"
        assert engine.resolve_language("eng") == "eng"
        with pytest.raises(ValueError, match=r"\['zzz'\] is not installed \(installed: .*eng"):
            engine.resolve_language("eng+zzz")
        with pytest.raises(ValueError):
            engine.resolve_language("+")


class TestVisionAnalyze:
    def _engine(self, enabled: bool = True, image_capable: bool = True):
        from aura_core.config.settings import VisionSettings
        from aura_core.domain import ProviderCapabilities
        from aura_core.domain.llm import Modality as Mod
        from aura_core.llm import ProviderRouter
        from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
        from aura_core.vision.engine import VisionEngine

        caps = ProviderCapabilities(
            model_id="m", context_window=8192, max_output_tokens=1024, is_local=True,
            modalities=(Mod.TEXT, Mod.IMAGE) if image_capable else (Mod.TEXT,))
        provider = EchoProvider(name="vision", capabilities=caps, script=[ScriptedTurn(text='  {"ok": true}  ')])
        router = ProviderRouter([provider])
        return VisionEngine(router=router, settings=VisionSettings(enabled=enabled)), provider

    IMAGE = ImageInput(media_type="image/png", data=b"\x89PNG\r\n\x1a\nxx", width=10, height=10)

    async def test_the_callers_instruction_replaces_the_perception_prompt(self) -> None:
        from aura_core.domain.llm import DataClassification
        from aura_core.domain.messages import Message

        engine, provider = self._engine()
        text, name, model = await engine.analyze(
            [self.IMAGE], instruction="Return JSON.", question="What is here?", max_tokens=321,
            history=[Message.assistant("first try"), Message.user("fix it")])
        assert (text, name) == ('{"ok": true}', "vision") and model
        (request,) = provider.calls
        roles = [(m.role.value, m.content) for m in request.conversation.messages]
        assert roles[0] == ("system", "Return JSON.") and roles[1] == ("user", "What is here?")
        assert roles[2:] == [("assistant", "first try"), ("user", "fix it")]
        assert request.sampling.max_output_tokens == 321
        assert request.data_classification is DataClassification.SENSITIVE and len(request.images) == 1

    async def test_disabled_no_image_and_no_capable_provider_are_dependency_errors(self) -> None:
        engine, _ = self._engine(enabled=False)
        with pytest.raises(DependencyUnavailableError):
            await engine.analyze([self.IMAGE], instruction="i", question="q")
        engine, _ = self._engine()
        with pytest.raises(DependencyUnavailableError):
            await engine.analyze([], instruction="i", question="q")
        engine, _ = self._engine(image_capable=False)
        with pytest.raises(DependencyUnavailableError, match="image-capable"):
            await engine.analyze([self.IMAGE], instruction="i", question="q")


class TestPictureInventories:
    def test_pdf_image_inventory_reads_resources_without_decoding(self) -> None:
        inventory = pdf_image_inventory(_pdf([{"text": ["hello"]}, {"image": True}, {"image": True}]))
        assert [(i["page"], i["name"], i["width"], i["height"]) for i in inventory] == [(2, "Im0", 1, 1), (3, "Im0", 1, 1)]
        assert pdf_image_inventory(_pdf([{"text": ["no pictures"]}])) == ()

    def test_pdf_image_inventory_refuses_a_non_pdf(self) -> None:
        from aura_core.domain.errors import KnowledgeError

        with pytest.raises(KnowledgeError):
            pdf_image_inventory(b"not a pdf")

    def test_office_images_lists_slide_pictures_in_presentation_order(self) -> None:
        import io
        import zipfile

        from aura_core.knowledge.office import office_images
        from tests.unit.test_docx_pptx_xlsx_readers import _pptx, _shape, _slide, _rels

        data = _pptx([(_slide(_shape(["One"], "title")), None), (_slide(_shape(["Two"], "title")), None)], order=[2, 1])
        files = {}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                files[name] = archive.read(name)
        files["ppt/slides/_rels/slide1.xml.rels"] = _rels(("rId1", "image", "../media/a.png"))
        files["ppt/slides/_rels/slide2.xml.rels"] = _rels(("rId1", "image", "../media/b.png"), ("rId2", "image", "../media/c.png"))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        # Presentation order is slide2.xml then slide1.xml, so b and c belong to slide 1.
        assert office_images(out.getvalue(), "pptx") == [
            {"name": "b.png", "slide": 1}, {"name": "c.png", "slide": 1}, {"name": "a.png", "slide": 2}]
        with pytest.raises(Exception):
            office_images(out.getvalue(), "xlsx")


def test_extracted_block_rows_default_is_empty_for_prose_pages() -> None:
    (block,) = extract_pdf(_pdf([{"text": ["just a sentence of prose here", "and another line of prose."]}])).blocks
    assert isinstance(block, ExtractedBlock) and block.rows == () and block.modality == Modality.PDF_PAGE
