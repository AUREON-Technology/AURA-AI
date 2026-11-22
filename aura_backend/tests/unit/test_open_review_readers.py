"""Review of the ``tools/open`` file readers / inspectors.

Real PDFs (built byte by byte by ``tests.unit.test_document_extraction._pdf``) are read by real
``pypdf``; nothing under test is mocked. The recurring check is the PDF rule: a marker on the LAST page
of a 40+ page document must be found, and a scanned page must be *named*, not dropped.
"""

from __future__ import annotations

import io
import json
import tarfile
import time
import zipfile

import pytest

from aura_core.tools.open.archive_extractor import ArchiveExtractor
from aura_core.tools.open.binary_file_inspector import BinaryFileInspector
from aura_core.tools.open.file_type_detector_router import FileTypeDetectorRouter
from aura_core.tools.open.multi_language_ast_analyzer import MultiLanguageAstAnalyzer
from aura_core.tools.open.notebook_reader import NotebookReader
from aura_core.tools.open.project_conventions_loader import ProjectConventionsLoader
from aura_core.tools.open.repo_semantic_indexer import RepoSemanticIndexer
from aura_core.tools.open.skills_rules_workflows_loader import SkillsRulesWorkflowsLoader
from aura_core.tools.open.source_content_explainer import SourceContentExplainer
from aura_core.tools.open.structured_data_parser import StructuredDataParser
from tests.unit.test_document_extraction import _pdf

MARKER = "LAST_PAGE_MARKER_QX7"
PAGES = 44


def _long_pdf(*, scanned_page: bool = False) -> bytes:
    """A PDF of ``PAGES`` pages whose last page carries MARKER; page 4 optionally a scan."""
    pages = [{"text": [f"Body text of page {i} with enough characters to count as text."]} for i in range(1, PAGES)]
    pages.append({"text": [f"Final page says {MARKER} and nothing else of importance."]})
    if scanned_page:
        pages[3] = {"image": True}
    return _pdf(pages)


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return _long_pdf()


@pytest.fixture(scope="module")
def scan_pdf_bytes() -> bytes:
    return _long_pdf(scanned_page=True)


# --------------------------------------------------------------------------- archive_extractor


class TestArchiveExtractor:
    def _zip(self, tmp_path, members: dict, name="a.zip"):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as zf:
            for member, data in members.items():
                zf.writestr(member, data)
        return path

    def test_pdf_member_read_in_full_from_zip(self, tmp_path, pdf_bytes):
        z = self._zip(tmp_path, {"docs/spec.pdf": pdf_bytes})
        res = ArchiveExtractor.read_archive_member(str(z), "docs/spec.pdf")
        assert res["read"] is True
        assert MARKER in res["text"]
        assert res["input"]["pages"] == PAGES and res["input"]["pages_read"] == PAGES
        assert res["complete"] is True

    def test_pdf_member_read_in_full_from_tar_gz(self, tmp_path, pdf_bytes):
        t = tmp_path / "a.tar.gz"
        with tarfile.open(t, "w:gz") as tf:
            info = tarfile.TarInfo("spec.pdf")
            info.size = len(pdf_bytes)
            tf.addfile(info, io.BytesIO(pdf_bytes))
        res = ArchiveExtractor.read_archive_documents(str(t))
        assert res["documents_read"] == 1 and res["complete"] is True
        assert MARKER in res["documents"][0]["text"]

    def test_scanned_pdf_page_is_named_not_dropped(self, tmp_path, scan_pdf_bytes):
        z = self._zip(tmp_path, {"scan.pdf": scan_pdf_bytes, "notes.txt": "héllo عربي"})
        res = ArchiveExtractor.read_archive_documents(str(z))
        assert res["documents_read"] == 2
        assert res["complete"] is False
        assert any("4" in w and "OCR" in w for w in res["warnings"])
        assert MARKER in next(d for d in res["documents"] if d["member"] == "scan.pdf")["text"]
        assert "عربي" in next(d for d in res["documents"] if d["member"] == "notes.txt")["text"]

    def test_binary_member_is_listed_as_skipped(self, tmp_path):
        z = self._zip(tmp_path, {"blob.bin": b"\x00\x01\x02" * 100, "ok.txt": "fine"})
        res = ArchiveExtractor.read_archive_documents(str(z))
        assert [s["member"] for s in res["skipped"]] == ["blob.bin"]
        assert res["complete"] is False

    def test_zip_slip_blocked_and_nothing_written(self, tmp_path):
        z = self._zip(tmp_path, {"ok.txt": "ok", "../evil.txt": "x"})
        res = ArchiveExtractor.extract_archive(str(z), str(tmp_path / "out"))
        assert res["extracted"] is False and "traversal" in res["error"].lower()
        assert not (tmp_path / "evil.txt").exists()
        # two-phase: the harmless first member must not have been written either
        assert not (tmp_path / "out" / "ok.txt").exists()
        audit = ArchiveExtractor.audit_archive_safety(str(z))
        assert audit["safe"] is False and audit["path_traversal_attempts"] == ["../evil.txt"]

    def test_symlink_then_write_through_is_refused(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        t = tmp_path / "s.tar"
        with tarfile.open(t, "w") as tf:
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = str(outside)
            tf.addfile(link)
            payload = tarfile.TarInfo("link/pwned.txt")
            payload.size = 1
            tf.addfile(payload, io.BytesIO(b"x"))
        res = ArchiveExtractor.extract_archive(str(t), str(tmp_path / "out"))
        assert res["extracted"] is False and "link" in res["error"].lower()
        assert not (outside / "pwned.txt").exists()
        audit = ArchiveExtractor.audit_archive_safety(str(t))
        assert audit["safe"] is False and audit["link_members"] == ["link"]

    def test_zip_symlink_member_is_flagged(self, tmp_path):
        path = tmp_path / "l.zip"
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("lnk")
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
        assert ArchiveExtractor.audit_archive_safety(str(path))["link_members"] == ["lnk"]
        assert ArchiveExtractor.extract_archive(str(path), str(tmp_path / "o"))["extracted"] is False

    def test_audit_is_not_an_all_clear_for_what_it_could_not_read(self, tmp_path, pdf_bytes):
        # old behaviour: a directory / a PDF / a non-archive fell through and returned safe=True or crashed
        assert ArchiveExtractor.audit_archive_safety(str(tmp_path))["safe"] is False
        (tmp_path / "plain.txt").write_text("not an archive")
        assert ArchiveExtractor.audit_archive_safety(str(tmp_path / "plain.txt"))["safe"] is False
        (tmp_path / "doc.pdf").write_bytes(pdf_bytes)
        res = ArchiveExtractor.audit_archive_safety(str(tmp_path / "doc.pdf"))
        assert res["safe"] is False and "PDF" in res["error"]
        assert res["input"]["pages"] == PAGES  # the PDF was read in full to say so

    def test_decompression_bomb_blocked_by_actual_bytes(self, tmp_path):
        z = tmp_path / "bomb.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("zeros.bin", b"\x00" * (3 * 1024 * 1024))
        res = ArchiveExtractor.extract_archive(str(z), str(tmp_path / "out"), max_size_mb=1)
        assert res["extracted"] is False and "max size" in res["error"]
        assert not (tmp_path / "out" / "zeros.bin").exists()

    def test_destination_that_is_a_file_does_not_raise(self, tmp_path):
        z = self._zip(tmp_path, {"a.txt": "a"})
        (tmp_path / "dest").write_text("i am a file")
        res = ArchiveExtractor.extract_archive(str(z), str(tmp_path / "dest"))
        assert res["extracted"] is False and "not a directory" in res["error"]

    def test_bad_inputs_never_raise(self, tmp_path):
        for bad in (None, 5, "", "\x00", tmp_path):
            assert ArchiveExtractor.list_archive_contents(bad)["valid"] is False  # type: ignore[arg-type]
        assert ArchiveExtractor.create_archive(str(tmp_path), str(tmp_path / "o.zip"), None)["created"] is False  # type: ignore[arg-type]
        assert ArchiveExtractor.extract_single_file(str(tmp_path / "nope.zip"), "x", str(tmp_path / "x"))["extracted"] is False

    def test_base_dir_confines_paths(self, tmp_path):
        inside = tmp_path / "inside"
        inside.mkdir()
        z = self._zip(tmp_path, {"a.txt": "a"})
        assert ArchiveExtractor.list_archive_contents(str(z), base_dir=str(inside))["valid"] is False
        assert ArchiveExtractor.extract_single_file(str(z), "a.txt", str(tmp_path / "o.txt"), base_dir=str(inside))["extracted"] is False

    def test_pdf_given_as_archive_is_identified(self, tmp_path, pdf_bytes):
        (tmp_path / "x.pdf").write_bytes(pdf_bytes)
        res = ArchiveExtractor.list_archive_contents(str(tmp_path / "x.pdf"))
        assert res["valid"] is False and "PDF" in res["error"] and res["input"]["pages"] == PAGES

    def test_single_file_extract_keeps_pdf_intact_and_flags_it(self, tmp_path, pdf_bytes):
        z = self._zip(tmp_path, {"d/spec.pdf": pdf_bytes})
        res = ArchiveExtractor.extract_single_file(str(z), "d/spec.pdf", str(tmp_path / "out.pdf"))
        assert res["extracted"] is True and res["is_pdf"] is True
        assert (tmp_path / "out.pdf").read_bytes() == pdf_bytes

    def test_create_archive_skips_symlinks_and_itself(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")
        secret = tmp_path / "secret.txt"
        secret.write_text("TOPSECRET")
        try:
            (src / "link.txt").symlink_to(secret)
        except OSError:
            pytest.skip("symlinks unavailable")
        out = src / "out.zip"  # output inside the tree it archives
        res = ArchiveExtractor.create_archive(str(src), str(out))
        assert res["created"] is True and res["files_count"] == 1 and res["skipped_links"] == ["link.txt"]
        with zipfile.ZipFile(out) as zf:
            assert zf.namelist() == ["a.txt"]

    def test_pre_1980_file_does_not_break_zip_creation(self, tmp_path):
        import os

        src = tmp_path / "src"
        src.mkdir()
        old = src / "old.txt"
        old.write_text("x")
        os.utime(old, (0, 0))
        assert ArchiveExtractor.create_archive(str(src), str(tmp_path / "o.zip"))["created"] is True


# --------------------------------------------------------------------------- binary_file_inspector


class TestBinaryFileInspector:
    def test_pdf_identified_with_pages_and_scans(self, tmp_path, scan_pdf_bytes):
        p = tmp_path / "d.pdf"
        p.write_bytes(scan_pdf_bytes)
        res = BinaryFileInspector.inspect(str(p))
        assert res["format_type"] == "PDF Document" and res["is_pdf"] is True
        assert res["pages"] == PAGES and res["readable_pages"] == PAGES - 1
        assert res["unreadable_pages"] == [4]
        assert any("OCR" in w for w in res["warnings"])

    def test_pdf_strings_cover_the_last_page(self, tmp_path, pdf_bytes):
        p = tmp_path / "d.pdf"
        p.write_bytes(pdf_bytes)
        strings = BinaryFileInspector.extract_printable_strings(str(p), max_count=100000)
        assert any(MARKER in s for s in strings)
        report = BinaryFileInspector.extract_printable_strings_report(str(p), max_count=3)
        assert report["truncated"] is True and report["input"]["pages"] == PAGES

    def test_text_starting_with_mz_is_not_a_pe(self, tmp_path):
        p = tmp_path / "mz.txt"
        p.write_text("MZ is the start of this sentence. " * 5)
        assert BinaryFileInspector.inspect(str(p))["format_type"] == "Generic Binary"
        assert BinaryFileInspector.inspect(str(p))["is_executable"] is False
        real = bytearray(b"MZ" + b"\x00" * 58 + (64).to_bytes(4, "little") + b"PE\x00\x00" + b"\x00" * 60)
        assert BinaryFileInspector.detect_format_signature(bytes(real)) == "Windows PE Executable / DLL"

    def test_java_class_is_not_macho(self):
        assert BinaryFileInspector.detect_format_signature(b"\xca\xfe\xba\xbe\x00\x00\x00\x34") == "Java Class File"
        assert BinaryFileInspector.detect_format_signature(b"\xca\xfe\xba\xbe\x00\x00\x00\x02") == "macOS Mach-O Binary"

    def test_directory_and_bad_paths_do_not_raise(self, tmp_path):
        for fn in (BinaryFileInspector.inspect, BinaryFileInspector.compute_multiple_hashes,
                   BinaryFileInspector.calculate_entropy):
            for bad in (tmp_path, tmp_path / "missing", None, 5, "\x00"):
                assert "error" in fn(bad) or fn(bad).get("exists") is False  # type: ignore[arg-type]
        assert BinaryFileInspector.extract_printable_strings(None) == []  # type: ignore[arg-type]

    def test_unreadable_file_does_not_raise(self, tmp_path):
        import os

        p = tmp_path / "locked.bin"
        p.write_bytes(b"abc")
        p.chmod(0)
        if os.access(p, os.R_OK):
            pytest.skip("running as a user that ignores file modes")
        assert "error" in BinaryFileInspector.inspect(str(p))
        assert "error" in BinaryFileInspector.compute_multiple_hashes(str(p))
        assert "error" in BinaryFileInspector.calculate_entropy(str(p))

    def test_hash_algorithms_unknown_reported_and_shake_survives(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"hello")
        res = BinaryFileInspector.compute_multiple_hashes(str(p), ["sha256", "bogus", "shake_128", None])  # type: ignore[list-item]
        assert list(res["digests"]) == ["sha256"]
        assert set(res["unsupported_algorithms"]) == {"bogus", "shake_128", "None"}

    def test_entropy_and_strings_scan_whole_file_not_first_512k(self, tmp_path):
        p = tmp_path / "big.bin"
        p.write_bytes(b"\x00" * 600_000 + b"deep_string_at_the_end" + b"\x00")
        assert "deep_string_at_the_end" in BinaryFileInspector.extract_printable_strings(str(p))
        assert BinaryFileInspector.calculate_entropy(str(p))["total_bytes"] == 600_023


# --------------------------------------------------------------------------- file_type_detector_router


class TestFileTypeDetectorRouter:
    def test_pdf_is_not_text_and_reports_pages(self, tmp_path, scan_pdf_bytes):
        p = tmp_path / "doc.pdf"
        p.write_bytes(scan_pdf_bytes)  # all-ASCII bytes: the old is_text_file() said True
        assert FileTypeDetectorRouter.is_text_file(str(p)) is False
        assert FileTypeDetectorRouter.is_text_file(scan_pdf_bytes) is False
        res = FileTypeDetectorRouter.detect_and_route(str(p))
        assert res["mime_type"] == "application/pdf" and res["is_text"] is False
        assert res["pages"] == PAGES and res["unreadable_pages"] == [4]
        assert res["warnings"]

    def test_pdf_header_only_says_pages_not_inspected(self, pdf_bytes):
        res = FileTypeDetectorRouter.detect_and_route(header_bytes=pdf_bytes[:200])
        assert res["mime_type"] == "application/pdf"
        assert "pages" not in res and "not inspected" in res["warnings"][0]

    def test_prose_mentioning_import_is_not_python(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("We should import the data and define def-initions later.\n")
        res = FileTypeDetectorRouter.detect_and_route(str(p))
        assert res["recommended_parser"] == "source_content_explainer"
        q = tmp_path / "m.py"
        q.write_text("import os\n\ndef f():\n    return 1\n")
        assert FileTypeDetectorRouter.detect_and_route(str(q))["recommended_parser"] == "ast_code_analyzer"

    def test_markdown_link_is_not_json(self, tmp_path):
        p = tmp_path / "README"
        p.write_text("[docs](https://example.com) are here\n")
        assert FileTypeDetectorRouter.detect_and_route(str(p))["recommended_parser"] != "structured_data_parser"

    def test_bad_inputs_never_raise(self, tmp_path):
        for kwargs in ({"file_path": 5}, {"file_path": "\x00"}, {"file_path": str(tmp_path)},
                       {"header_bytes": "text"}, {}):
            assert "mime_type" in FileTypeDetectorRouter.detect_and_route(**kwargs)  # type: ignore[arg-type]
        assert FileTypeDetectorRouter.detect_mime_from_extension(None) == "application/octet-stream"  # type: ignore[arg-type]

    def test_batch_skips_ignored_dirs_below_root_only_and_reports_pdfs(self, tmp_path, pdf_bytes):
        root = tmp_path / "build" / "proj"  # an ancestor named 'build' used to hide everything
        (root / "node_modules").mkdir(parents=True)
        (root / "node_modules" / "x.js").write_text("const a = 1")
        (root / "a.py").write_text("import os\n")
        (root / "d.pdf").write_bytes(pdf_bytes)
        res = FileTypeDetectorRouter.batch_detect_directory(str(root))
        assert res["total_scanned"] == 2 and res["pdf_files"] == ["d.pdf"] and res["truncated"] is False
        deep = FileTypeDetectorRouter.batch_detect_directory(str(root), inspect_pdfs=True)
        assert deep["pdf_coverage"]["d.pdf"]["pages"] == PAGES

    def test_batch_reports_truncation(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text("x")
        res = FileTypeDetectorRouter.batch_detect_directory(str(tmp_path), max_files=3)
        assert res["total_scanned"] == 3 and res["truncated"] is True


# --------------------------------------------------------------------------- notebook_reader


def _nb(**extra) -> str:
    cell = {"cell_type": "code", "source": ["print('hello')\n"] * 30, "outputs": [], "execution_count": 1}
    return json.dumps({"cells": [cell, {"cell_type": "markdown", "source": "# عنوان\n"}], "nbformat": 4, **extra})


class TestNotebookReader:
    def test_long_inline_json_is_not_mistaken_for_a_path(self):
        raw = _nb()
        assert len(raw) > 300
        res = NotebookReader.parse_notebook(raw)  # old: OSError "File name too long" -> valid False
        assert res["valid"] is True and res["code_cells_count"] == 1 and res["markdown_cells"][0]["markdown"] == "# عنوان\n"

    def test_reads_file_and_pdf_is_read_in_full_then_refused(self, tmp_path, pdf_bytes):
        (tmp_path / "n.ipynb").write_text(_nb(), encoding="utf-8")
        assert NotebookReader.parse_notebook(str(tmp_path / "n.ipynb"))["valid"] is True
        (tmp_path / "doc.pdf").write_bytes(pdf_bytes)
        res = NotebookReader.parse_notebook(str(tmp_path / "doc.pdf"))
        assert res["valid"] is False and "not a Jupyter notebook" in res["error"] and str(PAGES) in res["error"]
        assert res["input"]["pages"] == PAGES
        assert NotebookReader.extract_runnable_python(str(tmp_path / "doc.pdf")).startswith("# Error")

    def test_cell_magic_body_is_commented_out(self):
        nb = json.dumps({"cells": [{"cell_type": "code", "source": "%%bash\nrm -rf build\necho hi"}]})
        script = NotebookReader.extract_runnable_python(nb)
        assert "\nrm -rf build" not in script and "# rm -rf build" in script
        compile(script, "<nb>", "exec")  # must be valid Python

    def test_malformed_notebooks_do_not_raise(self):
        weird = json.dumps({"cells": [5, None, {"cell_type": "code", "source": [1, 2], "outputs": ["x", {"data": 3}]}]})
        assert NotebookReader.parse_notebook(weird)["valid"] is True
        assert len(NotebookReader.extract_cell_outputs(weird)) == 1  # the non-dict output is ignored, none raises
        assert NotebookReader.extract_cell_outputs("[1,2,3]") == []
        assert NotebookReader.extract_cell_outputs_report("[1,2,3]")["valid"] is False
        assert NotebookReader.parse_notebook(None)["valid"] is False  # type: ignore[arg-type]

    def test_error_outputs_are_returned(self):
        nb = json.dumps({"cells": [{"cell_type": "code", "source": "1/0", "outputs": [
            {"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
             "traceback": ["\x1b[31mTraceback\x1b[0m", "ZeroDivisionError"]},
            {"output_type": "stream", "name": "stderr", "text": ["warn\n"]}]}]})
        out = NotebookReader.extract_cell_outputs(nb)
        assert out[0]["text"] == "ZeroDivisionError: division by zero" and "Traceback" in out[0]["traceback"]
        assert "\x1b" not in out[0]["traceback"] and out[1]["stream"] == "stderr"

    def test_strip_outputs_keeps_non_latin_text_and_raises_valueerror(self):
        nb = json.dumps({"cells": [{"cell_type": "code", "source": "print('مرحبا')", "outputs": [{"x": 1}], "execution_count": 3}]})
        out = NotebookReader.strip_notebook_outputs(nb)
        assert "مرحبا" in out and "\\u" not in out
        cell = json.loads(out)["cells"][0]
        assert cell["outputs"] == [] and cell["execution_count"] is None
        with pytest.raises(ValueError):
            NotebookReader.strip_notebook_outputs("not json at all")


# --------------------------------------------------------------------------- structured_data_parser


class TestStructuredDataParser:
    def test_none_and_wrong_types_do_not_raise(self):
        assert StructuredDataParser.parse("{}", None)["valid"] is False  # type: ignore[arg-type]
        assert StructuredDataParser.parse(None, "json")["valid"] is False  # type: ignore[arg-type]
        assert StructuredDataParser.parse_with_line_errors(None, "json")["valid"] is False  # type: ignore[arg-type]
        assert StructuredDataParser.parse_with_line_errors("{}", None)["valid"] is False  # type: ignore[arg-type]

    def test_xml_entity_bomb_refused(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]><l>&b;</l>'
        res = StructuredDataParser.parse(bomb, "xml")
        assert res["valid"] is False and "ENTITY" in res["error"]

    def test_yaml_alias_bomb_refused_before_expansion(self):
        text = "a: &a [x,x,x,x,x,x,x,x,x]\n" + "".join(
            f"{c}: &{c} [*{p},*{p},*{p},*{p},*{p},*{p},*{p},*{p},*{p}]\n" for p, c in zip("abcdefgh", "bcdefghi")
        )
        start = time.time()
        res = StructuredDataParser.parse(text, "yaml")
        assert res["valid"] is False and "alias" in res["error"]
        assert time.time() - start < 5
        assert StructuredDataParser.parse("base: &b {x: 1}\nuse: *b\n", "yaml")["parsed_data"] == {"base": {"x": 1}, "use": {"x": 1}}

    def test_xml_parse_keeps_children_and_counts_them(self):
        res = StructuredDataParser.parse("<r><a>1</a><b x='2'>two</b></r>", "xml")
        assert res["items_count"] == 2
        assert [c["tag"] for c in res["parsed_data"]["children"]] == ["a", "b"]

    def test_line_errors_are_json_safe_and_cover_xml_and_yaml(self):
        ok = StructuredDataParser.parse_with_line_errors("<a/>", "xml")
        json.dumps(ok)  # old: a live xml.etree Element
        bad_xml = StructuredDataParser.parse_with_line_errors("<a>\n<b>\n</a>", "xml")
        assert bad_xml["valid"] is False and bad_xml["line"] == 3
        bad_yaml = StructuredDataParser.parse_with_line_errors("a: [1,\n b: }", "yaml")
        assert bad_yaml["line"] == 2

    def test_schema_bool_is_not_int_and_unknown_contract_is_an_error(self):
        res = StructuredDataParser.validate_schema({"n": True}, type_contracts={"n": "int"})
        assert res["valid"] is False
        res = StructuredDataParser.validate_schema({"n": 1}, type_contracts={"n": "integer"})
        assert res["valid"] is False and "Unknown type" in res["errors"][0]
        assert StructuredDataParser.validate_schema({"n": 1.5}, ["n"], {"n": "float"})["valid"] is True

    def test_flatten_keeps_empty_dicts_survives_depth_and_cycles(self):
        assert StructuredDataParser.flatten_nested_dict({"a": {}, "b": {"c": 1}}) == {"a": {}, "b.c": 1}
        deep: dict = {}
        node = deep
        for _ in range(5000):
            node["k"] = {}
            node = node["k"]
        node["leaf"] = 1
        assert list(StructuredDataParser.flatten_nested_dict(deep).values()) == [1]
        loop: dict = {"x": 1}
        loop["self"] = loop
        assert StructuredDataParser.flatten_nested_dict(loop)["self"] == "<circular reference>"

    def test_convert_keeps_non_ascii_readable(self):
        res = StructuredDataParser.convert_format('{"name": "مرحبا 🌍"}', "json", "yaml")
        assert "مرحبا 🌍" in res["output_text"]
        res = StructuredDataParser.convert_format("name: مرحبا", "yaml", "json")
        assert "مرحبا" in res["output_text"] and "\\u" not in res["output_text"]

    def test_parse_file_reads_whole_pdf_then_reports_not_valid_format(self, tmp_path, pdf_bytes):
        (tmp_path / "doc.pdf").write_bytes(pdf_bytes)
        res = StructuredDataParser.parse_file(str(tmp_path / "doc.pdf"), "json")
        assert res["valid"] is False and f"{PAGES} page(s) were read in full" in res["error"]
        assert res["input"]["pages"] == PAGES
        (tmp_path / "c.toml").write_text('title = "x"\n')
        assert StructuredDataParser.parse_file(str(tmp_path / "c.toml"))["parsed_data"] == {"title": "x"}
        assert StructuredDataParser.parse_file(str(tmp_path))["valid"] is False

    def test_pdf_text_with_json_in_the_last_page_is_parsed(self, tmp_path):
        pages = [{"text": ["Some preface text that is long enough to be kept."]} for _ in range(40)]
        pages.append({"text": ['{"last_page_key": "last page value ok"}']})
        (tmp_path / "d.pdf").write_bytes(_pdf(pages))
        # The preface makes the whole text invalid JSON; the point is the last page *was* read.
        res = StructuredDataParser.parse_file(str(tmp_path / "d.pdf"), "json")
        assert res["input"]["pages"] == 41 and res["input"]["chars"] > 40 * 40


# --------------------------------------------------------------------------- source_content_explainer


class TestSourceContentExplainer:
    def test_pdf_bytes_and_file_are_explained_in_full(self, tmp_path, pdf_bytes):
        res = SourceContentExplainer.explain("spec.pdf", pdf_bytes)
        assert res["input"]["pages"] == PAGES and res["line_count"] >= PAGES
        (tmp_path / "spec.pdf").write_bytes(pdf_bytes)
        res = SourceContentExplainer.explain_file(str(tmp_path / "spec.pdf"))
        assert res["input"]["pages_read"] == PAGES
        guide = SourceContentExplainer.generate_study_guide_file(str(tmp_path / "spec.pdf"))
        assert guide["input"]["pages"] == PAGES and guide["study_guide_markdown"]

    def test_scanned_page_is_warned(self, scan_pdf_bytes):
        res = SourceContentExplainer.explain("scan.pdf", scan_pdf_bytes)
        assert res["input"]["unreadable_pages"] == [4] and any("OCR" in w for w in res["warnings"])

    def test_python_comments_are_not_headings(self):
        src = "#!/usr/bin/env python\n# type: ignore\ndef public():\n    pass\n"
        res = SourceContentExplainer.explain("tool.py", src)
        assert res["headings"] == [] and res["public_symbols"] == ["Function: public"]
        md = "# Title\n```bash\n# not a heading\n```\n## Real\n"
        assert SourceContentExplainer.explain("README.md", md)["headings"] == ["Title", "Real"]

    def test_unparseable_python_is_reported_not_zero_symbols(self):
        res = SourceContentExplainer.explain("bad.py", "def (:\n")
        assert "parse_error" in res
        res = SourceContentExplainer.explain_code_architecture("bad.py", "def (:\n")
        assert res["parsed"] is False and "Could not parse" in res["design_notes"]
        assert SourceContentExplainer.explain_code_architecture("x.py", "x = 1\0")["parsed"] is False
        assert SourceContentExplainer.extract_docstring_catalog(None)["parsed"] is True  # type: ignore[arg-type]
        assert SourceContentExplainer.explain("a.py", 5)["parsed"] is False  # type: ignore[arg-type]

    def test_concepts_are_most_frequent_not_first_seen(self):
        text = "Alpha Bravo Charlie Delta Echoes Foxtrot Gamma\n" + "Zulu " * 5 + "Yankee " * 3
        concepts = SourceContentExplainer.generate_study_guide("x.txt", text)["key_concepts"]
        assert concepts[:2] == ["Zulu", "Yankee"]

    def test_explain_function_finds_named_function_and_all_params(self):
        src = "def helper(a):\n    return a\n\ndef target(a, /, b, *args, c=1, **kw):\n    if a:\n        return b\n"
        res = SourceContentExplainer.explain_function("target", src)
        assert res["function_name"] == "target"
        assert res["parameters"] == ["a", "b", "*args", "c", "**kw"]
        method = "    def m(self, x):\n        return x\n"  # an indented method used to be a SyntaxError
        assert SourceContentExplainer.explain_function("m", method)["parsed"] is True

    def test_architecture_relative_imports_and_layer_tokens(self):
        res = SourceContentExplainer.explain_code_architecture(
            "score.py", "import os\nfrom . import sibling\nfrom .pkg import thing\nfrom lib import x\n"
        )
        assert res["architectural_layer"] == "unknown"  # "score" is not "core"
        assert res["external_dependencies"] == ["lib", "os"]
        assert res["relative_imports"] == [".", ".pkg"]
        assert SourceContentExplainer.explain_code_architecture("aura_core/tools/x.py", "")["architectural_layer"] == "tool / execution"


# --------------------------------------------------------------------------- project_conventions_loader


class TestProjectConventionsLoader:
    def test_candidate_outside_workspace_is_not_read(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (tmp_path / "secret.txt").write_text("TOPSECRET")
        res = ProjectConventionsLoader.load_conventions(str(ws), ["../secret.txt", str(tmp_path / "secret.txt")])
        assert res["conventions"] == {}
        assert set(res["errors"]) == {"../secret.txt", str(tmp_path / "secret.txt")}
        assert ProjectConventionsLoader.discover_convention_files(str(ws), extra_patterns=["../secret.txt"]) == []

    def test_symlink_out_of_workspace_is_not_read(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (tmp_path / "secret.txt").write_text("TOPSECRET")
        try:
            (ws / "AGENTS.md").symlink_to(tmp_path / "secret.txt")
        except OSError:
            pytest.skip("symlinks unavailable")
        res = ProjectConventionsLoader.load_conventions(str(ws))
        assert "TOPSECRET" not in json.dumps(res) and "AGENTS.md" in res["errors"]

    def test_truncation_is_reported(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("rule " * 2000, encoding="utf-8")
        res = ProjectConventionsLoader.load_conventions(str(tmp_path))
        assert res["truncated_files"]["AGENTS.md"] == {"chars_returned": 4000, "chars_total": 10000}
        assert res["warnings"]
        full = ProjectConventionsLoader.load_conventions(str(tmp_path), max_chars_per_file=None)
        assert len(full["conventions"]["AGENTS.md"]) == 10000 and not full["truncated_files"]

    def test_pdf_convention_file_is_read_to_the_last_page(self, tmp_path, scan_pdf_bytes):
        (tmp_path / "RULES.pdf").write_bytes(scan_pdf_bytes)
        res = ProjectConventionsLoader.load_conventions(str(tmp_path), ["RULES.pdf"])
        assert MARKER in res["conventions"]["RULES.pdf"]  # default 4000-char cut does not apply to a PDF
        assert res["input"]["RULES.pdf"]["pages"] == PAGES
        assert any("4" in w and "OCR" in w for w in res["warnings"])
        assert ProjectConventionsLoader.extract_agent_rules_file(str(tmp_path / "RULES.pdf"))["input"]["pages"] == PAGES

    def test_latin1_file_is_not_stripped_of_bytes(self, tmp_path):
        (tmp_path / "README.md").write_bytes("café — naïve".encode("latin-1", errors="replace"))
        assert "café" in ProjectConventionsLoader.load_conventions(str(tmp_path))["conventions"]["README.md"]

    def test_long_editorconfig_content_does_not_raise(self):
        content = "[*]\nindent_style = space\n# " + "x" * 400 + "\nmax_line_length = off\n"
        sections = ProjectConventionsLoader.parse_editorconfig(content)  # old: OSError file name too long
        assert sections["*"]["max_line_length"] == "off"
        single = "root = true" + " " * 400
        assert isinstance(ProjectConventionsLoader.parse_editorconfig(single), dict)

    def test_max_line_length_off_does_not_raise(self):
        res = ProjectConventionsLoader.validate_file_against_conventions("a.py", "x" * 500 + "\n", {"max_line_length": "off"})
        assert res["compliant"] is True
        res = ProjectConventionsLoader.validate_file_against_conventions("a.py", "a\x0cb\n")  # form feed is not a line break
        assert res["total_lines"] == 1

    def test_validate_pdf_bytes_says_it_was_read_but_not_checkable(self, pdf_bytes):
        res = ProjectConventionsLoader.validate_file_against_conventions("a.pdf", pdf_bytes)
        assert res["compliant"] is False and "read in full" in res["error"] and res["input"]["pages"] == PAGES

    def test_agent_rules_ignore_fenced_comments_and_scale(self):
        md = "# Rules\nYou must run `pytest -q`.\n```bash\n# not a section\n```\nNever commit secrets.\n"
        res = ProjectConventionsLoader.extract_agent_rules(md)
        assert res["sections"] == ["Rules"] and res["common_commands"] == ["pytest -q"]
        big = "\n".join(f"You must do thing {i}." for i in range(200_000))
        start = time.time()
        assert len(ProjectConventionsLoader.extract_agent_rules(big)["must_do_directives"]) == 30
        assert time.time() - start < 10


# --------------------------------------------------------------------------- skills_rules_workflows_loader


class TestSkillsRulesWorkflowsLoader:
    def test_extra_dirs_cannot_leave_the_workspace(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (tmp_path / "outside").mkdir()
        (tmp_path / "outside" / "leak.md").write_text("x")
        res = SkillsRulesWorkflowsLoader.load_resources(str(ws), extra_dirs=["../outside", str(tmp_path / "outside")])
        assert res["skills_found"] == 0 and len(res["rejected_dirs"]) == 2

    def test_pdf_skill_listed_and_read_to_last_page(self, tmp_path, pdf_bytes):
        d = tmp_path / "skills"
        d.mkdir()
        (d / "handbook.pdf").write_bytes(pdf_bytes)
        (d / "py.md").write_text("---\nname: py\ndescription: x\n---\nbody")
        res = SkillsRulesWorkflowsLoader.load_resources(str(tmp_path))
        assert {(s["name"], s["format"]) for s in res["all_skills"]} == {("handbook", "pdf"), ("py", "markdown")}
        doc = SkillsRulesWorkflowsLoader.read_resource(str(tmp_path), "skills/handbook.pdf")
        assert doc["read"] is True and MARKER in doc["text"] and doc["input"]["pages"] == PAGES
        assert SkillsRulesWorkflowsLoader.read_resource(str(tmp_path), "../x")["read"] is False
        assert SkillsRulesWorkflowsLoader.validate_skill_file(str(d / "handbook.pdf"))["input"]["pages"] == PAGES
        assert SkillsRulesWorkflowsLoader.validate_skill_structure(pdf_bytes)["input"]["pages"] == PAGES

    def test_truncation_flag_and_symlink_loop(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        for i in range(5):
            (d / f"s{i}.md").write_text("x")
        res = SkillsRulesWorkflowsLoader.load_resources(str(tmp_path), max_files=3)
        assert res["truncated"] is True and res["skills_found"] == 3
        try:
            (d / "loop").symlink_to(d)
        except OSError:
            return
        assert SkillsRulesWorkflowsLoader.load_resources(str(tmp_path))["skills_found"] == 5  # no crash, no dupes

    def test_frontmatter_with_dashes_in_value_folded_and_list(self):
        text = (
            "---\nname: my-skill\ndescription: >\n  Use this --- when\n  testing things\ntags:\n  - alpha\n  - beta\n"
            "other: [x, 'y']\n---\n# Body\ntext\n"
        )
        parsed = SkillsRulesWorkflowsLoader.parse_skill_yaml_frontmatter(text)
        assert parsed["metadata"]["name"] == "my-skill"
        assert parsed["description"] == "Use this --- when testing things"
        assert parsed["metadata"]["tags"] == ["alpha", "beta"] and parsed["metadata"]["other"] == ["x", "y"]
        assert parsed["body"].startswith("# Body")

    def test_matching_survives_none_and_string_tags_and_non_latin(self):
        skills = [
            {"name": None, "description": None, "tags": "python, testing"},
            {"name": "", "tags": ["", "go"]},
            {"name": "اختبار الوحدة", "description": "كتابة اختبارات"},
        ]
        hits = SkillsRulesWorkflowsLoader.match_skills_for_task(skills, "write python testing code for google")
        assert [h["tags"] for h in hits] == ["python, testing"]  # empty tag matches nothing; 'go' != 'google'
        arabic = SkillsRulesWorkflowsLoader.match_skills_for_task(skills, "اختبار الوحدة للنظام")
        assert arabic and arabic[0]["name"] == "اختبار الوحدة"
        assert SkillsRulesWorkflowsLoader.match_skills_for_task(skills, None) == []  # type: ignore[arg-type]

    def test_workflow_steps_fences_and_bare_step_header(self):
        text = "## Step 1\nRun the tests.\n```bash\n$ make test\n1. pytest -q\n```\n## Step 2: Ship\n"
        steps = SkillsRulesWorkflowsLoader.load_workflow_steps(text)
        assert [s["step_number"] for s in steps] == [1, 2]  # "1. pytest -q" inside the fence is not a step
        assert steps[0]["commands"] == ["make test", "1. pytest -q"] and steps[0]["description"] == ["Run the tests."]
        assert "```bash" not in steps[0]["commands"]
        huge = "1" * 5000 + ". boom\n"
        SkillsRulesWorkflowsLoader.load_workflow_steps(huge)  # old: int() ValueError past 4300 digits


# --------------------------------------------------------------------------- repo_semantic_indexer


class TestRepoSemanticIndexer:
    def test_pdf_is_indexed_by_its_full_text_and_searchable_to_the_last_page(self, tmp_path, pdf_bytes):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "manual.pdf").write_bytes(pdf_bytes)
        (repo / "a.py").write_text("def alpha():\n    pass\n")
        idx = RepoSemanticIndexer.index_repository(str(repo))
        pdf = next(f for f in idx["files"] if f["file"] == "manual.pdf")
        assert pdf["kind"] == "pdf" and pdf["pages"] == PAGES and pdf["complete"] is True
        assert pdf["lines"] >= PAGES and idx["status"] == "success" and idx["complete"] is True
        hits = RepoSemanticIndexer.search_text_bm25_or_keyword(idx, MARKER)
        assert [h["file"] for h in hits] == ["manual.pdf"]

    def test_scanned_page_makes_the_index_partial_and_is_named(self, tmp_path, scan_pdf_bytes):
        (tmp_path / "scan.pdf").write_bytes(scan_pdf_bytes)
        idx = RepoSemanticIndexer.index_repository(str(tmp_path))
        assert idx["status"] == "partial" and idx["incomplete_files"] == ["scan.pdf"]
        assert any("4" in w and "OCR" in w for w in idx["warnings"])

    def test_pdf_passed_via_extensions_is_not_read_as_garbage(self, tmp_path, pdf_bytes):
        (tmp_path / "m.pdf").write_bytes(pdf_bytes)
        idx = RepoSemanticIndexer.index_repository(str(tmp_path), extensions=["pdf"])
        assert idx["files"][0]["kind"] == "pdf"

    def test_unreadable_files_are_reported_not_skipped_silently(self, tmp_path):
        (tmp_path / "ok.py").write_text("x = 1\n")
        (tmp_path / "bin.json").write_bytes(b"\x00\x01\x02")
        (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 truncated nonsense")
        idx = RepoSemanticIndexer.index_repository(str(tmp_path))
        assert {s["file"] for s in idx["skipped_files"]} == {"bin.json", "broken.pdf"}
        assert idx["status"] == "partial"

    def test_ancestor_named_build_does_not_hide_the_repo(self, tmp_path):
        repo = tmp_path / "build" / "dist" / "proj"
        repo.mkdir(parents=True)
        (repo / "a.py").write_text("def f(): pass\n")
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "b.js").write_text("x")
        idx = RepoSemanticIndexer.index_repository(str(repo))
        assert [f["file"] for f in idx["files"]] == ["a.py"]

    def test_same_symbol_in_many_files_keeps_every_definition(self, tmp_path):
        for name in ("a", "b", "c"):
            (tmp_path / f"{name}.py").write_text("def main():\n    pass\n")
        idx = RepoSemanticIndexer.index_repository(str(tmp_path))
        assert len(RepoSemanticIndexer.search_symbols(idx, "main")) == 3
        assert RepoSemanticIndexer.get_index_statistics(idx)["functions_count"] == 3

    def test_truncation_nul_bytes_and_bad_arguments(self, tmp_path):
        for i in range(4):
            (tmp_path / f"f{i}.py").write_text("x = 1\n")
        (tmp_path / "nul.py").write_text("x = 1\n" + "pad = 0\n" * 2000 + "y = '\0'\n")
        idx = RepoSemanticIndexer.index_repository(str(tmp_path), max_files=3)
        assert idx["truncated"] is True and idx["status"] == "partial"
        full = RepoSemanticIndexer.index_repository(str(tmp_path), chunk_lines=None, max_files=None)  # type: ignore[arg-type]
        assert full["indexed_files_count"] == 5  # NUL byte past 8 KiB used to abort the whole index
        assert RepoSemanticIndexer.index_repository(None)["status"] == "error"  # type: ignore[arg-type]
        assert RepoSemanticIndexer.search_symbols(full, None) == []  # type: ignore[arg-type]
        assert len(RepoSemanticIndexer.search_text_bm25_or_keyword(full, "f0", max_results=-1)) <= 10  # not "all but the last"

    def test_keyword_search_does_not_read_outside_root_and_handles_arabic(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (tmp_path / "secret.txt").write_text("needle " * 50)
        (repo / "ar.md").write_text("مرحبا بالعالم الجميل\n", encoding="utf-8")
        idx = {"root": str(repo), "files": [{"file": "../secret.txt", "lines": 1}, {"file": "ar.md", "lines": 1}]}
        assert RepoSemanticIndexer.search_text_bm25_or_keyword(idx, "needle") == []
        assert [h["file"] for h in RepoSemanticIndexer.search_text_bm25_or_keyword(idx, "بالعالم")] == ["ar.md"]

    def test_chunking_clamps_bad_sizes_and_empty_file_has_no_chunks(self, tmp_path):
        bad = RepoSemanticIndexer.chunk_file_for_indexing("a\nb\nc", chunk_size_lines=0, overlap=-3)  # defaults, no loop
        assert [c["text"] for c in bad] == ["a\nb\nc"]
        assert len(RepoSemanticIndexer.chunk_file_for_indexing("a\nb\nc", chunk_size_lines=1, overlap=5)) == 3
        (tmp_path / "empty.py").write_text("")
        assert RepoSemanticIndexer.index_repository(str(tmp_path))["total_chunks"] == 0

    def test_statistics_extension_of_dotted_directory(self):
        stats = RepoSemanticIndexer.get_index_statistics({"files": [{"file": "pkg.v2/Makefile", "lines": 1}]})
        assert stats["extensions_distribution"] == {"no_ext": 1}


# --------------------------------------------------------------------------- multi_language_ast_analyzer


class TestMultiLanguageAstAnalyzer:
    def test_minified_one_line_bundle_does_not_hang(self):
        start = time.time()
        res = MultiLanguageAstAnalyzer.analyze_javascript_typescript("a ? f(x) : y " * 50_000)
        assert time.time() - start < 5
        assert res["skipped_long_lines"] == 1 and "warnings" in res

    def test_bad_inputs_never_raise(self):
        for lang in ("python", "javascript", "go", "rust", None, 5):
            assert MultiLanguageAstAnalyzer.analyze(None, lang)["valid"] is False  # type: ignore[arg-type]
        assert MultiLanguageAstAnalyzer.analyze_python("x = 1\0")["valid"] is False
        assert MultiLanguageAstAnalyzer.analyze_python("(" * 5000)["valid"] is False
        assert MultiLanguageAstAnalyzer.analyze("x" * 6_000_000, "python")["valid"] is False

    def test_python_finds_guarded_imports_relative_imports_and_all_args(self):
        src = (
            "try:\n    import fast\nexcept ImportError:\n    fast = None\n"
            "if True:\n    from .rel import thing\nfrom . import sib\nz: int = 1\n"
            "def f(a, /, b, *args, c=1, **kw):\n    pass\n"
        )
        res = MultiLanguageAstAnalyzer.analyze_python(src)
        assert res["imports"] == ["fast", ".rel.thing", ".sib"]
        assert "z" in res["global_variables"] and "fast" in res["global_variables"]
        assert res["functions_detailed"][0]["args"] == ["a", "b", "*args", "c", "**kw"]

    def test_js_ignores_comments_and_test_calls_and_finds_require(self):
        src = (
            "// class Hidden {}\n/* class Also {} */\nclass Real {}\nconst fs = require('fs');\n"
            "describe('x', function () {\n});\nit('y', function () {\n});\nfunction run(a, b) {\n}\n"
        )
        res = MultiLanguageAstAnalyzer.analyze_javascript_typescript(src)
        assert res["classes"] == ["Real"] and res["functions"] == ["run"]
        assert res["imports"] == ["const fs = require('fs');"]

    def test_go_and_rust_extras(self):
        go = MultiLanguageAstAnalyzer.analyze_go(
            'package p\nimport (\n  _ "a/b"\n  f "fmt"\n  // "commented"\n)\ntype S[T any] struct {}\n'
            "func Map[T any](x T) T {\n}\nfunc (s *S[T]) Do() {\n}\n"
        )
        assert go["imports"] == ["a/b", "fmt"] and go["classes"] == ["S"] and go["functions"] == ["Map", "Do"]
        assert MultiLanguageAstAnalyzer.analyze_go("package")["package"] == "main"
        rust = MultiLanguageAstAnalyzer.analyze_rust("pub enum E { A }\npub fn g<T: Clone>(x: T) {}\n// fn hidden() {}\n")
        assert rust["enums"] == ["E"] and rust["functions"] == ["g"]

    def test_cross_language_uses_extension_and_skips_unsupported(self):
        out = MultiLanguageAstAnalyzer.extract_cross_language_symbols(
            {"a.PY": "def f(): pass", "notes.md": "# hi", "c.tsx": "export class A {}", "d.json": "{}"}
        )
        assert {s["file"] for s in out["skipped_files"]} == {"notes.md", "d.json"}
        assert {(s["symbol"], s["language"]) for s in out["symbols"]} == {("f", "python"), ("A", "typescript")}

    def test_pdf_is_read_in_full_by_analyze_file_and_not_mistaken_for_source(self, tmp_path, scan_pdf_bytes):
        (tmp_path / "doc.pdf").write_bytes(scan_pdf_bytes)
        res = MultiLanguageAstAnalyzer.analyze_file(str(tmp_path / "doc.pdf"))
        assert res["input"]["pages"] == PAGES and res["input"]["unreadable_pages"] == [4]
        assert any("PDF" in w for w in res["warnings"]) and any("OCR" in w for w in res["warnings"])
        out = MultiLanguageAstAnalyzer.extract_cross_language_symbols({"spec.py": scan_pdf_bytes})  # type: ignore[dict-item]
        assert out["skipped_files"][0]["reason"] == "PDF is not source code"
        assert out["file_analyses"]["spec.py"]["input"]["pages"] == PAGES

    def test_analyze_file_errors_and_confinement(self, tmp_path):
        assert MultiLanguageAstAnalyzer.analyze_file(str(tmp_path / "missing.py"))["valid"] is False
        assert MultiLanguageAstAnalyzer.analyze_file(str(tmp_path))["valid"] is False
        (tmp_path / "a.go").write_text("package q\nfunc A() {\n}\n")
        assert MultiLanguageAstAnalyzer.analyze_file(str(tmp_path / "a.go"))["functions"] == ["A"]
        other = tmp_path / "other"
        other.mkdir()
        assert MultiLanguageAstAnalyzer.analyze_file(str(tmp_path / "a.go"), base_dir=str(other))["valid"] is False


# --------------------------------------------------------------------------- time budget / no network


class TestTimeBudgetAndIsolation:
    def test_budget_is_clamped_to_a_generous_bounded_range(self):
        from aura_core.tools.open._time_budget import DEFAULT_BUDGET_S, MAX_BUDGET_S, Deadline

        assert (DEFAULT_BUDGET_S, MAX_BUDGET_S) == (300.0, 600.0)
        assert Deadline().seconds == 300.0
        assert Deadline(10_000).seconds == 600.0
        for bad in (None, "x", -5, 0, float("nan"), float("inf"), True):
            assert Deadline(bad).seconds in (300.0, 600.0)  # type: ignore[arg-type]
        assert Deadline(150).seconds == 150.0

    def test_indexer_stops_and_says_so_when_the_budget_is_used_up(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("x = 1\n")
        idx = RepoSemanticIndexer.index_repository(str(tmp_path), time_budget_s=1e-9)
        assert idx["timed_out"] is True and idx["truncated"] is True and idx["status"] == "partial"
        assert any("Time budget" in w for w in idx["warnings"])
        # the default budget reads everything
        assert RepoSemanticIndexer.index_repository(str(tmp_path))["indexed_files_count"] == 5
        report = RepoSemanticIndexer.search_text_report({"root": str(tmp_path), "files": [{"file": "f0.py"}]}, "x1f0", time_budget_s=1e-9)
        assert report["timed_out"] is True

    def test_archive_documents_and_extraction_report_timeouts(self, tmp_path):
        z = tmp_path / "a.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for i in range(3):
                zf.writestr(f"{i}.txt", "x" * 100)
        res = ArchiveExtractor.read_archive_documents(str(z), time_budget_s=1e-9)
        assert res["timed_out"] is True and res["complete"] is False and len(res["skipped"]) == 3
        out = ArchiveExtractor.extract_archive(str(z), str(tmp_path / "o"), time_budget_s=1e-9)
        assert out["extracted"] is False and out["timed_out"] is True
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a")
        made = ArchiveExtractor.create_archive(str(src), str(tmp_path / "made.zip"), time_budget_s=1e-9)
        assert made["created"] is False and made["timed_out"] is True and not (tmp_path / "made.zip").exists()

    def test_hash_and_entropy_never_report_a_partial_result_as_complete(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"abc" * 1000)
        for res in (BinaryFileInspector.inspect(str(p), time_budget_s=1e-9),
                    BinaryFileInspector.compute_multiple_hashes(str(p), time_budget_s=1e-9),
                    BinaryFileInspector.calculate_entropy(str(p), time_budget_s=1e-9)):
            assert res["timed_out"] is True and "sha256" not in res and "digests" not in res and "entropy" not in res

    def test_directory_scans_report_timeouts(self, tmp_path):
        (tmp_path / "skills").mkdir()
        for i in range(3):
            (tmp_path / "skills" / f"s{i}.md").write_text("x")
            (tmp_path / f"f{i}.txt").write_text("x")
        assert FileTypeDetectorRouter.batch_detect_directory(str(tmp_path), time_budget_s=1e-9)["timed_out"] is True
        assert SkillsRulesWorkflowsLoader.load_resources(str(tmp_path), time_budget_s=1e-9)["timed_out"] is True
        res = ProjectConventionsLoader.load_conventions(str(tmp_path), ["a.md", "b.md"], time_budget_s=1e-9)
        assert res["timed_out"] is True and set(res["errors"]) == {"a.md", "b.md"}
        out = MultiLanguageAstAnalyzer.extract_cross_language_symbols({"a.py": "x=1", "b.py": "y=2"}, time_budget_s=1e-9)
        assert out["timed_out"] is True and len(out["skipped_files"]) == 2

    def test_readers_do_no_network_io_and_never_import_the_gateway(self):
        """None of the readers talks to a network or to the AURA gateway; they import only siblings."""
        import ast
        import pathlib

        import aura_core.tools.open as pkg

        banned_roots = {"socket", "urllib", "http", "httpx", "requests", "aiohttp", "ssl", "ftplib", "smtplib",
                        "subprocess", "importlib"}
        banned_aura = ("api", "container", "adapters", "config", "auth", "kernel")
        names = ["archive_extractor", "binary_file_inspector", "file_type_detector_router", "notebook_reader",
                 "structured_data_parser", "source_content_explainer", "project_conventions_loader",
                 "skills_rules_workflows_loader", "repo_semantic_indexer", "multi_language_ast_analyzer",
                 "_time_budget"]
        folder = pathlib.Path(pkg.__file__).parent
        for name in names:
            source = (folder / f"{name}.py").read_text(encoding="utf-8")
            assert "/api/v3" not in source and "aureon-ai.tech" not in source, name
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [("." * node.level) + (node.module or "")]
                    if node.level == 1 and node.module not in ("_input", "_time_budget"):
                        pytest.fail(f"{name} imports sibling {node.module}")
                    assert node.level < 2, f"{name} reaches outside tools/open: {modules}"
                else:
                    continue
                for module in modules:
                    assert module.split(".")[0] not in banned_roots, (name, module)
                    assert not any(module.startswith(f"aura_core.{b}") for b in banned_aura), (name, module)
