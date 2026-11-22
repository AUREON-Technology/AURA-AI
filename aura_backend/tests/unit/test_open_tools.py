"""The five ``aura_core/tools/open`` tools, run for real.

PDFs here are built byte by byte (``tests.unit.test_document_extraction._pdf``) and read by the
actual ``pypdf``; the test runner really spawns ``python -m pytest``. Nothing is mocked.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from aura_core.tools.open.ast_code_analyzer import ASTCodeAnalyzer
from aura_core.tools.open.automated_test_generator_and_verifier import AutomatedTestGeneratorAndVerifier as Gen
from aura_core.tools.open.mermaid_diagram_generator import MermaidDiagramGenerator as Mermaid
from aura_core.tools.open.security_vulnerability_scanner import SecurityVulnerabilityScanner as Scanner
from aura_core.tools.open.symbol_and_import_verifier import SymbolAndImportVerifier as Verifier
from aura_core.tools.open._input import SourceReadError, read_source_bytes, read_source_file
from tests.unit.test_document_extraction import _pdf

SECRET = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"


def _many_pages(n: int) -> bytes:
    return _pdf([{"text": [f"page {i} marker-{i} line of ordinary prose here"]} for i in range(1, n + 1)])


# -- PDF: the whole document is read -------------------------------------------------------------


class TestPdfIsReadInFull:
    def test_every_page_of_a_long_pdf_is_in_the_text(self, tmp_path: Path) -> None:
        path = tmp_path / "long.pdf"
        path.write_bytes(_many_pages(60))
        read = read_source_file(path)
        assert read.kind == "pdf" and read.pages == 60 and read.complete
        for i in (1, 2, 30, 59, 60):  # first, last and middle: nothing truncated
            assert f"marker-{i} " in read.text + " "
        assert read.describe()["pages_read"] == 60

    def test_scanned_pages_are_named_not_silently_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "mixed.pdf"
        path.write_bytes(_pdf([
            {"text": ["Readable page with plenty of text"]},
            {"image": True},
            {"text": ["Another readable page of text"]},
        ]))
        read = read_source_file(path)
        assert read.unreadable_pages == (2,) and not read.complete
        assert "page(s) 2" in read.warnings()[0]

    def test_pdf_is_detected_by_content_not_only_extension(self) -> None:
        assert read_source_bytes(_many_pages(2), name="upload.bin").kind == "pdf"

    def test_fake_pdf_extension_and_binary_files_are_refused(self, tmp_path: Path) -> None:
        fake = tmp_path / "x.pdf"
        fake.write_text("print('hi')")
        with pytest.raises(SourceReadError):
            read_source_file(fake)
        blob = tmp_path / "blob.dat"
        blob.write_bytes(b"\x00\x01\x02binary")
        with pytest.raises(SourceReadError, match="binary"):
            read_source_file(blob)

    def test_corrupt_pdf_is_an_error_not_a_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.pdf"
        path.write_bytes(b"%PDF-1.4\n garbage \x00\x01")
        with pytest.raises(SourceReadError):
            read_source_file(path)

    def test_base_dir_confines_paths(self, tmp_path: Path) -> None:
        (tmp_path / "in").mkdir()
        outside = tmp_path / "secret.py"
        outside.write_text("x = 1")
        with pytest.raises(SourceReadError, match="outside"):
            read_source_file(outside, base_dir=tmp_path / "in")
        with pytest.raises(SourceReadError, match="outside"):
            read_source_file(tmp_path / "in" / ".." / "secret.py", base_dir=tmp_path / "in")

    def test_scanner_scans_all_pages_of_a_pdf(self, tmp_path: Path) -> None:
        pages = [{"text": [f"page {i} nothing here to see"]} for i in range(1, 41)]
        pages[39] = {"text": [f"page 40 token = '{SECRET}'  and password = \"hunter2hunter2\""]}
        path = tmp_path / "report.pdf"
        path.write_bytes(_pdf(pages))
        result = Scanner.scan_file(path)
        assert result["input"]["pages"] == 40 and result["complete"]
        rules = {f["rule_id"] for f in result["findings"]}
        assert {"SEC-KEY-OPENAI", "SEC-SECRET-ASSIGN"} <= rules  # the finding is on the LAST page
        assert SECRET not in repr(result)

    def test_scanner_never_says_secure_for_a_partly_unreadable_pdf(self, tmp_path: Path) -> None:
        path = tmp_path / "scan.pdf"
        path.write_bytes(_pdf([{"text": ["clean page, nothing to report"]}, {"image": True}]))
        result = Scanner.scan_file(path)
        assert result["findings_count"] == 0
        assert result["secure"] is False and result["complete"] is False and result["warnings"]

    def test_full_scan_reads_pdf_and_skips_bandit_for_it(self, tmp_path: Path) -> None:
        path = tmp_path / "r.pdf"
        path.write_bytes(_many_pages(3))
        result = Scanner.full_scan(file_path=str(path), use_bandit=True)
        assert result["secure"] is True and result["bandit"] is None
        assert any("bandit was skipped" in w for w in result["warnings"])

    def test_analyzer_reports_pdf_input_instead_of_crashing(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.pdf"
        path.write_bytes(_many_pages(5))
        result = ASTCodeAnalyzer.analyze_file(path)
        assert result["status"] == "error" and result["input"]["pages"] == 5
        assert "5 page(s) were read in full" in result["error"]

    def test_analyzer_still_analyzes_a_python_file(self, tmp_path: Path) -> None:
        path = tmp_path / "m.py"
        path.write_text("import os\nclass A(B):\n    def f(self, x: int = 1) -> str:\n        return os.getcwd()\n")
        result = ASTCodeAnalyzer.analyze_file(path)
        assert result["status"] == "success" and result["input"]["kind"] == "text"
        assert result["classes"][0]["methods"][0]["signature"] == "def f(self, x: int = 1) -> str"


# -- ast_code_analyzer ---------------------------------------------------------------------------


class TestAstAnalyzer:
    def test_nul_bytes_and_syntax_errors_return_errors(self) -> None:
        assert ASTCodeAnalyzer.ast_code_analyzer("x = 1\x00")["status"] == "error"
        assert ASTCodeAnalyzer.ast_code_analyzer("def (:")["status"] == "error"

    def test_missing_and_directory_paths(self, tmp_path: Path) -> None:
        assert "not found" in ASTCodeAnalyzer.analyze_file(tmp_path / "nope.py")["error"]
        assert "not a file" in ASTCodeAnalyzer.analyze_file(tmp_path)["error"]

    def test_compact_mode_and_all_param_kinds(self) -> None:
        src = "async def f(a, /, b, *args, c=2, **kw): ...\nCONST = 1\n"
        full = ASTCodeAnalyzer.ast_code_analyzer(src)
        assert full["functions"][0]["signature"] == "async def f(a, /, b, *args, c = 2, **kw)"
        assert ASTCodeAnalyzer.ast_code_analyzer(src, compact=True)["constants"] == ["CONST"]


# -- security_vulnerability_scanner --------------------------------------------------------------


class TestScanner:
    def test_known_risks_are_found(self) -> None:
        src = "import os, pickle\nos.system('ls')\neval(x)\npickle.loads(b)\ntry:\n    pass\nexcept:\n    pass\n"
        ids = {f["rule_id"] for f in Scanner.scan_source_code(src)["findings"]}
        assert {"SEC-OSSYS-004", "SEC-EVAL-001", "SEC-PICKLE-005", "SEC-AST-BARE-EXCEPT"} <= ids

    def test_safe_lookalikes_are_not_flagged(self) -> None:
        src = "model.eval()\nsession.exec(stmt)\nast.literal_eval(s)\n# eval(x)\nyaml.load(f, Loader=yaml.SafeLoader)\n"
        assert Scanner.scan_source_code(src)["findings"] == []

    def test_unsafe_yaml_loader_is_still_flagged(self) -> None:
        assert Scanner.scan_source_code("yaml.load(f, Loader=yaml.Loader)\n")["findings"]

    def test_secrets_are_never_echoed_back(self) -> None:
        src = f"x = eval('{SECRET}')\npassword = 'hunter2hunter2'\n"
        result = Scanner.scan_source_code(src)
        assert SECRET not in repr(result) and "hunter2hunter2" not in repr(result)
        diff = f"diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+k = '{SECRET}'\n"
        d = Scanner.scan_git_diff(diff)
        assert d["findings"][0]["line"] == 2 and d["findings"][0]["file"] == "a.py"
        assert SECRET not in repr(d)

    def test_diff_without_hunk_header_is_still_scanned(self) -> None:
        assert Scanner.scan_git_diff(f"+k = '{SECRET}'\n")["findings_count"] == 1

    def test_file_not_found_and_bandit_options(self, tmp_path: Path) -> None:
        assert "error" in Scanner.scan_file(tmp_path / "missing.py")
        out = Scanner.run_bandit_if_available("-x-not-an-option")
        assert out["secure"] is not True  # a failed/absent bandit run is never an all-clear

    def test_bandit_run_is_real_or_honestly_unavailable(self, tmp_path: Path) -> None:
        target = tmp_path / "t.py"
        target.write_text("import subprocess\nsubprocess.call('ls', shell=True)\n")
        out = Scanner.run_bandit_if_available(str(target))
        if out["available"]:
            assert out["issues"] and out["secure"] is False
        else:
            assert out["secure"] is None


# -- symbol_and_import_verifier ------------------------------------------------------------------


class TestVerifier:
    def test_importing_is_never_executed(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran"
        (tmp_path / "evil_pkg").mkdir()
        (tmp_path / "evil_pkg" / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
        (tmp_path / "evil_pkg" / "sub.py").write_text("def real(): ...\n")
        src = "from evil_pkg.sub import real, ghost\nimport evil_pkg.sub\n"
        out = Verifier.verify_imported_symbols_exist_in_env(src, search_paths=[str(tmp_path)])
        assert not marker.exists(), "verifying an import executed the imported package"
        assert [m["symbol"] for m in out["missing_symbols"]] == ["ghost"]
        assert out["missing_modules"] == []

    def test_search_paths_are_honoured_and_missing_modules_reported(self, tmp_path: Path) -> None:
        (tmp_path / "zz_local.py").write_text("def f(): ...\n")
        out = Verifier.verify_imported_symbols_exist_in_env(
            "from zz_local import f\nimport definitely_not_a_module_xyz\n", search_paths=[str(tmp_path)]
        )
        assert [m["module"] for m in out["missing_modules"]] == ["definitely_not_a_module_xyz"]
        assert out["missing_symbols"] == []

    def test_stdlib_names_and_submodules_are_valid(self) -> None:
        out = Verifier.verify_imported_symbols_exist_in_env(
            "from os import path, getcwd\nfrom xml import etree\nimport os.path\nfrom json import nope\n"
        )
        assert [m["symbol"] for m in out["missing_symbols"]] == ["nope"]

    def test_unverifiable_symbols_are_not_called_missing(self) -> None:
        out = Verifier.verify_imported_symbols_exist_in_env("from math import pi\n")
        assert out["valid"] is True  # C module: listed as unverified, not wrongly reported missing

    def test_arity_counts_keywords_and_skips_unknowable_calls(self) -> None:
        spec = {"f": {"min_args": 1, "max_args": 2}}
        ok = "f(1)\nf(x=1)\nf(1, y=2)\nf(*a)\nf(**k)\n"
        assert Verifier.verify_call_argument_counts(ok, spec)["valid"] is True
        bad = Verifier.verify_call_argument_counts("f()\nf(1, 2, 3)\n", spec)
        assert [m["line"] for m in bad["mismatches"]] == [1, 2]

    def test_nul_byte_source_is_an_error_result(self) -> None:
        assert Verifier.verify_source("x\x00")["valid"] is False


# -- mermaid_diagram_generator -------------------------------------------------------------------


class TestMermaid:
    def test_non_latin_names_stay_distinct_and_labelled(self) -> None:
        out = Mermaid.generate_flowchart(
            [{"id": "مستخدم"}, {"id": "خادم"}, {"id": "a-b"}, {"id": "a_b"}],
            [{"from": "مستخدم", "to": "خادم", "label": "طلب"}],
        )
        assert "مستخدم[" in out and "خادم[" in out
        ids = [line.split("[")[0].strip() for line in out.splitlines()[1:5]]
        assert len(set(ids)) == 4, out

    def test_reserved_words_and_digits(self) -> None:
        assert Mermaid.sanitize_identifier("end") != "end"
        assert Mermaid.sanitize_identifier("1abc").startswith("N_")

    def test_arrow_and_relation_injection_is_neutralised(self) -> None:
        evil = "->>B: hi\n    click A call evil()\n    A->>"
        seq = Mermaid.generate_sequence_diagram(["A", "B"], [{"from": "A", "to": "B", "text": "x", "arrow": evil}])
        assert "click" not in seq and "A->>B: x" in seq
        cls_d = Mermaid.generate_class_diagram(relations=[{"source": "A", "target": "B", "type": "-->\n  evil"}])
        assert "evil" not in cls_d

    def test_malformed_inputs_do_not_crash(self) -> None:
        assert Mermaid.generate_er_diagram({"u": "not-a-list"}).startswith("erDiagram")
        assert Mermaid.generate_flowchart([{"label": "a"}, {"label": "b"}], [{"from": "", "to": ""}]).count("\n") == 2
        assert Mermaid.generate_er_diagram({"u": [{"name": "id", "key": "PK; drop"}]}).count("PK") == 0

    def test_generated_diagrams_validate_even_with_braces_in_labels(self) -> None:
        out = Mermaid.generate_flowchart([{"id": "a", "label": "dict {x"}, {"id": "b", "shape": "diamond"}], [])
        assert Mermaid.validate_mermaid_syntax(out)["valid"] is True
        assert Mermaid.validate_mermaid_syntax("not a diagram")["valid"] is False

    def test_class_diagram_from_source(self) -> None:
        out = Mermaid.generate_class_diagram_from_source("class A(B):\n    x: int = 1\n    def f(self, a): ...\n")
        assert out["generated"] and "B <|-- A : inherits" in out["mermaid"]
        assert Mermaid.generate_class_diagram_from_source("class (")["generated"] is False


# -- automated_test_generator_and_verifier -------------------------------------------------------


class TestGeneratorAndVerifier:
    def test_generated_tests_really_import_and_pass(self) -> None:
        out = Gen.generate_and_verify("def add(a, b):\n    return a + b\nclass K:\n    def go(self): ...\n")
        assert out["verified"] is True
        assert out["execution"]["counts"]["passed"] >= 3
        assert out["verification_level"] == "structural"

    def test_module_import_path_places_source_correctly(self) -> None:
        out = Gen.generate_and_verify("def f(): ...\n", module_import_path="pkg.sub.mod")
        assert out["verified"] is True

    def test_missing_symbol_fails_verification(self) -> None:
        out = Gen.generate_and_verify("def f(): ...\n", custom_test_code="from module_under_test import g\ndef test_g():\n    assert g\n")
        assert out["verified"] is False and out["execution"]["counts"]["errors"] >= 1

    def test_custom_tests_detect_a_real_bug(self) -> None:
        out = Gen.generate_and_verify(
            "def add(a, b):\n    return a - b\n",
            custom_test_code="from module_under_test import add\ndef test_add():\n    assert add(1, 2) == 3\n",
        )
        assert out["verified"] is False and out["verification_level"] == "custom"
        assert out["execution"]["counts"]["failed"] == 1

    @pytest.mark.parametrize("bad", ["os\nimport sys; sys.exit(0)", "a.b; import os", "class", "x y", "../x"])
    def test_module_path_injection_is_rejected(self, bad: str) -> None:
        out = Gen.generate_pytest_code("def f(): ...\n", module_import_path=bad)
        assert out["generated"] is False and "dotted Python identifier" in out["error"]

    def test_colliding_test_names_are_made_unique(self) -> None:
        code = Gen.generate_pytest_code("class Foo:\n    def x(self): ...\nclass FOO:\n    def x(self): ...\n")["test_code"]
        names = [line[4:].split("(")[0] for line in code.splitlines() if line.startswith("def ")]
        assert len(names) == len(set(names))

    def test_bad_source_and_syntax_errors(self) -> None:
        assert Gen.generate_and_verify("def (:")["stage"] == "generation"
        assert Gen.generate_pytest_code("x\x00")["generated"] is False
        assert Gen.run_tests_in_temp_dir("def broken(:")["passed"] is False

    def test_child_process_gets_no_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_FAKE_API_KEY", "leak-me")
        monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
        code = "import os\ndef test_env():\n    assert 'AURA_FAKE_API_KEY' not in os.environ\n    assert 'DATABASE_URL' not in os.environ\n"
        assert Gen.run_tests_in_temp_dir(code)["passed"] is True



    def test_existing_tests_run_and_are_confined(self, tmp_path: Path) -> None:
        (tmp_path / "test_ok.py").write_text("def test_a():\n    assert True\n")
        ok = Gen.run_existing_tests("test_ok.py", working_dir=str(tmp_path))
        assert ok["passed"] and ok["counts"]["passed"] == 1
        assert "inside the working directory" in Gen.run_existing_tests("../x.py", working_dir=str(tmp_path))["error"]
        assert "not found" in Gen.run_existing_tests("nope.py", working_dir=str(tmp_path))["error"]

    @pytest.mark.parametrize("arg", ["-p", "-c", "--rootdir=/", "--version", "-o", "--pyargs", "-k"])
    def test_dangerous_pytest_options_are_refused(self, tmp_path: Path, arg: str) -> None:
        (tmp_path / "test_ok.py").write_text("def test_a():\n    assert True\n")
        out = Gen.run_existing_tests("test_ok.py", working_dir=str(tmp_path), extra_args=[arg])
        assert out["passed"] is False and "not allowed" in out["error"]

    def test_safe_options_are_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "test_ok.py").write_text("def test_a():\n    assert True\ndef test_b():\n    assert True\n")
        out = Gen.run_existing_tests("test_ok.py", working_dir=str(tmp_path), extra_args=["-x", "-k", "test_a", "--maxfail=2"])
        assert out["passed"] and out["counts"]["passed"] == 1
