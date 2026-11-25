"""Review of the project / artifact / evidence tools in ``aura_core/tools/open``.

Every test here fails on the behaviour the tool had before the review. PDFs are real (built by
``tests.unit.test_document_extraction._pdf`` and read by real pypdf); a distinctive marker sits on the
LAST page of a 45-page PDF to prove nothing past the first pages is dropped.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from aura_core.tools.open.acceptance_criteria_verifier import AcceptanceCriteriaVerifier, CriteriaInputError
from aura_core.tools.open.ambiguity_and_clarification_detector import (
    AmbiguityAndClarificationDetector,
    AmbiguityInputError,
)
from aura_core.tools.open.artifact_feedback_ingestor import ArtifactFeedbackIngestor
from aura_core.tools.open.artifact_generator import ArtifactGenerator
from aura_core.tools.open.mobile_artifact_summarizer import MobileArtifactSummarizer
from aura_core.tools.open.project_status_qa_tool import ProjectStatusQaTool
from aura_core.tools.open.regression_impact_analyzer import RegressionImpactAnalyzer
from aura_core.tools.open.scope_and_diff_guard import DiffInputError, ScopeAndDiffGuard
from aura_core.tools.open.stale_context_detector import StaleContextDetector
from aura_core.tools.open.ui_visual_verifier import UiVisualVerifier
from aura_core.tools.open.verification_evidence_collector import VerificationEvidenceCollector
from tests.unit.test_document_extraction import _pdf

MARKER = "ZEBRA-LAST-PAGE-MARKER-7731"


def _long_pdf(marker: str = MARKER, pages: int = 45, scanned_page: int | None = None) -> bytes:
    specs = []
    for i in range(1, pages):
        if i == scanned_page:
            specs.append({"image": True})
        else:
            specs.append({"text": [f"Page {i} filler paragraph of ordinary prose"]})
    specs.append({"text": [marker]})
    return _pdf(specs)


# --------------------------------------------------------------------------- scope_and_diff_guard


class TestScopeAndDiffGuard:
    DIFF = (
        "diff --git a/.github/ci.yml b/.github/ci.yml\n"
        "deleted file mode 100644\n"
        "--- a/.github/ci.yml\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-name: ci\n"
        "-on: push\n"
        "diff --git a/b/backend/x.py b/b/backend/x.py\n"
        "--- a/b/backend/x.py\n"
        "+++ b/b/backend/x.py\n"
        "@@ -1,2 +1,2 @@\n"
        " +context that looks like an addition\n"
        "-old\n"
        "++i;\n"
    )

    def test_deleted_file_is_reported_and_out_of_scope(self):
        res = ScopeAndDiffGuard.check_diff(self.DIFF, allowed_files=["b/backend"])
        assert ".github/ci.yml" in res["modified_files"]
        assert res["within_scope"] is False

    def test_path_prefix_is_removed_not_stripped_by_characters(self):
        paths = [f["path"] for f in ScopeAndDiffGuard.parse_diff_files(self.DIFF)]
        assert "b/backend/x.py" in paths  # lstrip("b/") used to turn this into "ackend/x.py"
        assert ".github/ci.yml" in paths  # lstrip("./") used to drop the leading dot

    def test_hunk_aware_line_counts(self):
        files = {f["path"]: f for f in ScopeAndDiffGuard.parse_diff_files(self.DIFF)}
        x = files["b/backend/x.py"]
        assert (x["lines_added"], x["lines_deleted"]) == (1, 1)  # "++i;" counted, " +ctx" is context
        assert files[".github/ci.yml"]["lines_deleted"] == 2

    def test_removed_dash_dash_comment_is_counted(self):
        diff = "--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1,1 @@\n-- old comment\n select 1;\n"
        assert MobileArtifactSummarizer.count_diff_lines(diff) == {"added": 0, "removed": 1}

    @pytest.mark.parametrize("name", [".env", "sub/.ENV.local", "a/../.env", "credentials.json", "id_ed25519"])
    def test_forbidden_files_are_caught(self, name):
        res = ScopeAndDiffGuard.check_scope_compliance([name], ["src", "sub", "a", "."])
        assert res["compliant"] is False and res["forbidden_files"]

    def test_garbage_and_pdf_are_not_approved(self):
        assert ScopeAndDiffGuard.check_diff("hello world")["status"] == "UNPARSEABLE_DIFF"
        assert ScopeAndDiffGuard.check_diff("hello world")["within_scope"] is False
        res = ScopeAndDiffGuard.check_diff(_long_pdf())
        assert res["status"] == "INVALID_INPUT" and "45 page" in res["error"]
        with pytest.raises(DiffInputError):
            ScopeAndDiffGuard.detect_accidental_secrets_in_diff(_long_pdf())
        assert ScopeAndDiffGuard.check_diff_size_budget("hello world")["within_budget"] is False

    def test_no_allowed_files_is_not_an_approval_of_scope(self):
        res = ScopeAndDiffGuard.check_diff(self.DIFF)
        assert res["status"] == "NO_SCOPE_DEFINED" and res["scope_checked"] is False

    def test_secret_in_added_line_found_but_not_in_context(self):
        diff = (
            "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1,1 +1,2 @@\n"
            " token = 'ctxctxctxctxctxctx'\n+password = 'hunter2hunter2hunter2'\n"
        )
        found = ScopeAndDiffGuard.detect_accidental_secrets_in_diff(diff)
        assert len(found) == 1 and found[0]["file"] == "c.py"


# --------------------------------------------------------------------------- regression_impact_analyzer


class TestRegressionImpactAnalyzer:
    def test_rename_and_binary_changes_are_listed(self):
        diff = (
            "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
            "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"
        )
        changed = {(c["path"], c["deleted"]) for c in RegressionImpactAnalyzer.parse_changed_files_from_diff(diff)}
        assert changed == {("new.py", False), ("old.py", True), ("img.png", False)}

    def test_pdf_is_not_a_diff(self):
        with pytest.raises(DiffInputError, match="PDF"):
            RegressionImpactAnalyzer.parse_changed_files_from_diff(_long_pdf())

    def test_dependents_match_on_module_boundaries_and_from_imports(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "router.py").write_text("X = 1\n")
        (tmp_path / "pkg" / "a.py").write_text("from pkg import router\n")
        (tmp_path / "pkg" / "b.py").write_text("import pkg.router_utils\n")  # a different module
        (tmp_path / "pkg" / "c.py").write_text("import os\n")
        res = RegressionImpactAnalyzer.analyze_impact(["pkg/router.py"], repo_dir=str(tmp_path))
        assert res["affected_modules"] == [str(__import__("pathlib").Path("pkg") / "a.py")]
        assert res["dependents_scanned"] is True

    def test_unparseable_python_is_reported_not_skipped_silently(self, tmp_path):
        (tmp_path / "ok.py").write_text("import x\n")
        (tmp_path / "bad.py").write_text("def (:\n")
        (tmp_path / "nul.py").write_bytes(b"import y\x00\n")
        res = RegressionImpactAnalyzer.analyze_impact(["m.py"], repo_dir=str(tmp_path))
        assert sorted(res["unparseable_files"]) == ["bad.py", "nul.py"]
        assert res["warnings"]

    def test_without_repo_the_result_says_importers_were_not_checked(self):
        res = RegressionImpactAnalyzer.analyze_impact(["a.py"])
        assert res["dependents_scanned"] is False
        assert any("not scanned" in w or "No repository" in w for w in res["warnings"])

    def test_init_file_stands_for_its_package(self):
        assert RegressionImpactAnalyzer._module_fragments("pkg/sub/__init__.py")[-1] == "sub"

    def test_python_file_that_is_really_a_pdf_is_flagged(self, tmp_path):
        p = tmp_path / "fake.py"
        p.write_bytes(_long_pdf())
        info = RegressionImpactAnalyzer.inspect_imports(str(p))
        assert info["parsed"] is False and "PDF" in info["error"]

    def test_base_dir_confines_import_extraction(self, tmp_path):
        outside = tmp_path / "out.py"
        outside.write_text("import secret\n")
        inner = tmp_path / "in"
        inner.mkdir()
        assert RegressionImpactAnalyzer.extract_imported_modules_from_file(str(outside), base_dir=str(inner)) == []

    def test_bad_inputs_return_error_dicts(self, tmp_path):
        assert RegressionImpactAnalyzer.get_git_changed_files(str(tmp_path / "nope"))["success"] is False
        assert RegressionImpactAnalyzer.get_git_changed_files(str(tmp_path), None)["success"] is False  # type: ignore[arg-type]

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
    def test_non_ascii_file_names_survive_git_diff(self, tmp_path):
        def git(*a):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True,
                           capture_output=True)
        git("init", "-q")
        f = tmp_path / "ملف.py"
        f.write_text("a = 1\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-q", "-m", "x")
        f.write_text("a = 2\n", encoding="utf-8")
        res = RegressionImpactAnalyzer.get_git_changed_files(str(tmp_path))
        assert res["success"] and res["files"] == ["ملف.py"]


# --------------------------------------------------------------------------- mobile_artifact_summarizer


class TestMobileArtifactSummarizer:
    def test_summary_never_exceeds_max_chars(self):
        res = MobileArtifactSummarizer.summarize(task_summary="x" * 500, max_chars=2)
        assert res["character_count"] <= 2

    def test_junk_test_results_do_not_raise(self):
        res = MobileArtifactSummarizer.summarize(test_results={"passed": "many", "failed": None})
        assert "Tests: 0 passed, 0 failed" in res["summary_text"]

    def test_unrecognised_test_output_is_unknown_not_failed_or_passed(self):
        assert MobileArtifactSummarizer.summarize_test_results("garbage")["status"] == "UNKNOWN"
        assert MobileArtifactSummarizer.summarize_test_results({})["status"] == "UNKNOWN"
        assert MobileArtifactSummarizer.summarize_test_results("1 passed, 1 error in 1s")["status"] == "FAILED"

    def test_pdf_diff_is_refused_with_a_clear_message(self):
        res = MobileArtifactSummarizer.summarize_diff_compact(_long_pdf())
        assert "PDF" in res["error"] and res["total_files"] == 0
        out = MobileArtifactSummarizer.summarize(diff_text=_long_pdf())  # type: ignore[arg-type]
        assert any("PDF" in w for w in out["warnings"])

    def test_notification_is_one_line_and_bounded(self):
        s = MobileArtifactSummarizer.format_mobile_notification("a\nb", "c\nd", max_length=5)
        assert "\n" not in s and len(s) <= 5


# --------------------------------------------------------------------------- ui_visual_verifier


class TestUiVisualVerifier:
    def test_difference_after_the_first_4096_bytes_is_detected(self):
        a = b"a" * 10000
        b = b"a" * 9000 + b"b" * 1000
        res = UiVisualVerifier.verify_visuals(a, b, tolerance_percentage=1.0)
        assert res["visually_consistent"] is False and res["diff_percentage"] == 10.0

    def test_length_difference_counts(self):
        res = UiVisualVerifier.verify_visuals(b"a" * 100, b"a" * 200, tolerance_percentage=10.0)
        assert res["visually_consistent"] is False

    def test_sampling_can_never_claim_a_match(self):
        res = UiVisualVerifier.verify_visuals(b"a" * 100 + b"x", b"a" * 100 + b"y", sample_limit=100)
        assert res["visually_consistent"] is False and res["complete"] is False

    def test_missing_path_is_an_error_not_text(self):
        res = UiVisualVerifier.verify_visuals("no/such/file.png", "no/such/file.png")
        assert res["status"] == "INPUT_ERROR" and res["visually_consistent"] is False

    def test_base_dir_confines_paths(self, tmp_path):
        inner = tmp_path / "in"
        inner.mkdir()
        outside = tmp_path / "o.png"
        outside.write_bytes(b"x")
        res = UiVisualVerifier.verify_visuals(str(outside), str(outside), base_dir=str(inner))
        assert res["status"] == "INPUT_ERROR"

    def test_pdfs_differing_only_on_the_last_page_are_not_a_match(self, tmp_path):
        a, b = _long_pdf(MARKER), _long_pdf("A COMPLETELY DIFFERENT LAST PAGE TEXT")
        (tmp_path / "a.pdf").write_bytes(a)
        (tmp_path / "b.pdf").write_bytes(b)
        res = UiVisualVerifier.verify_visuals(str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf"), tolerance_percentage=0.0)
        assert res["method"] == "pdf_text_comparison"
        assert res["screenshot_input"]["pages"] == 45 and res["screenshot_input"]["pages_read"] == 45
        assert res["visually_consistent"] is False and res["diff_percentage"] > 0
        same = UiVisualVerifier.verify_visuals(a, a)
        assert same["visually_consistent"] is True

    def test_pdf_with_scanned_page_cannot_claim_a_match(self):
        a, b = _long_pdf(scanned_page=3), _long_pdf(scanned_page=3, marker=MARKER)
        # same text pages but not byte-identical
        b = b.replace(b"%%EOF", b"%%EOF\n")
        res = UiVisualVerifier.verify_visuals(a, b, tolerance_percentage=50.0)
        assert res["visually_consistent"] is False and res["warnings"]

    def test_pdf_versus_image_is_an_error(self):
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00" + b"\x00" * 20
        assert UiVisualVerifier.verify_visuals(png, _long_pdf())["status"] == "INPUT_ERROR"
        err = UiVisualVerifier.compare_image_dimensions(png, _long_pdf())
        assert err["match"] is False and "PDF" in err["error"]

    def test_different_dimensions_do_not_pass_the_combined_check(self):
        def png(w, h):
            return (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big")
                    + b"\x08\x06\x00\x00\x00" + b"\x00" * 3000)
        res = UiVisualVerifier.calculate_pixel_difference(png(100, 100), png(200, 50), threshold=0.5)
        assert res["is_visually_matching"] is False

    def test_unparseable_headers_only_pass_on_exact_match(self):
        res = UiVisualVerifier.calculate_pixel_difference(b"abc" * 100, b"abc" * 99 + b"abd")
        assert res["is_visually_matching"] is False
        assert UiVisualVerifier.calculate_pixel_difference(b"abc" * 100, b"abc" * 100)["is_visually_matching"] is True

    def test_jpeg_with_non_baseline_sof_marker(self):
        jpg = b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc1\x00\x0b\x08\x00\x20\x00\x40\x03\x01\x11\x00" + b"\x00" * 10
        assert UiVisualVerifier.extract_image_dimensions(jpg) == (64, 32, "JPEG")

    def test_perceptual_hash_uses_the_tail_bytes(self):
        base = bytes(range(64)) * 3 + b"\x00" * 7
        assert UiVisualVerifier.compute_perceptual_hash(base) != UiVisualVerifier.compute_perceptual_hash(
            base[:-7] + b"\xff" * 7
        )


# --------------------------------------------------------------------------- verification_evidence_collector


class TestVerificationEvidenceCollector:
    def test_empty_evidence_is_not_a_pass(self):
        res = VerificationEvidenceCollector.collect("t")
        assert res["all_passed"] is False and res["verdict"] == "INCONCLUSIVE"

    def test_missing_or_bad_exit_code_is_not_success(self):
        assert VerificationEvidenceCollector.collect("t", command_results=[{"command": "x"}])["all_passed"] is False
        res = VerificationEvidenceCollector.collect("t", command_results=[{"command": "x", "exit_code": "boom"}])
        assert res["all_passed"] is False

    def test_failing_test_output_fails_even_with_zero_exit(self):
        res = VerificationEvidenceCollector.collect(
            "t", test_outputs=["3 failed, 10 passed in 2s"], command_results=[{"command": "pytest", "exit_code": 0}]
        )
        assert res["all_passed"] is False and res["verdict"] == "FAILED"

    def test_explicit_success_passes(self):
        res = VerificationEvidenceCollector.collect("t", command_results=[{"command": "pytest", "exit_code": 0}])
        assert res["all_passed"] is True

    def test_secrets_are_scrubbed_before_storing(self):
        res = VerificationEvidenceCollector.collect(
            "t", command_results=[{"command": "curl -H 'Authorization: Bearer abcdefghijklmnop' API_KEY=sk-live123", "exit_code": 0}]
        )
        stored = str(res["dossier"])
        assert "abcdefghijklmnop" not in stored and "sk-live123" not in stored

    def test_record_command_keeps_the_tail_and_full_hash(self):
        out = "x" * 2000 + "\n=== 1 failed in 1s ==="
        d = VerificationEvidenceCollector.collect("t")["dossier"]
        rec = VerificationEvidenceCollector.record_command_evidence(d, "pytest", 1, stdout=out)
        entry = rec["command_entry"]
        assert "1 failed" in entry["stdout_tail"] and entry["stdout_truncated"] is True and len(entry["stdout_sha256"]) == 64
        assert VerificationEvidenceCollector.record_command_evidence(d, "x", "nope")["success"] is False

    def test_integrity_check_with_bad_input_does_not_raise(self):
        assert VerificationEvidenceCollector.verify_dossier_integrity(None, None)["valid"] is False  # type: ignore[arg-type]

    def test_pdf_artifact_is_hashed_whole_and_opened_page_by_page(self, tmp_path):
        p = tmp_path / "report.pdf"
        p.write_bytes(_long_pdf())
        d = VerificationEvidenceCollector.collect("t")["dossier"]
        res = VerificationEvidenceCollector.record_file_artifact(d, str(p))
        assert res["success"] and res["file_size_bytes"] == p.stat().st_size
        assert res["input"]["pages"] == 45 and res["input"]["pages_read"] == 45 and res["input"]["complete"] is True

    def test_pdf_with_scanned_page_is_flagged(self, tmp_path):
        p = tmp_path / "scan.pdf"
        p.write_bytes(_long_pdf(scanned_page=7))
        res = VerificationEvidenceCollector.record_file_artifact({}, str(p))
        assert res["input"]["complete"] is False and res["input"]["unreadable_pages"] == [7] and res["warnings"]

    def test_corrupt_pdf_is_hashed_but_flagged(self, tmp_path):
        p = tmp_path / "bad.pdf"
        p.write_bytes(b"%PDF-1.4 nonsense")
        res = VerificationEvidenceCollector.record_file_artifact({}, str(p))
        assert res["success"] and res["input"]["complete"] is False and res["warnings"]

    def test_base_dir_confines_artifacts(self, tmp_path):
        inner = tmp_path / "in"
        inner.mkdir()
        (tmp_path / "o.txt").write_text("x")
        res = VerificationEvidenceCollector.record_file_artifact({}, str(tmp_path / "o.txt"), base_dir=str(inner))
        assert res["success"] is False

    def test_certificate_does_not_overclaim(self):
        d = VerificationEvidenceCollector.collect(
            "t`x", command_results=[{"command": "a`b", "exit_code": 2}, {"command": "no code"}], artifacts_paths=["f.txt"]
        )["dossier"]
        cert = VerificationEvidenceCollector.generate_audit_certificate(d)
        assert "FAILED" in cert and "1 listed, 0 hashed" in cert and "non-repudiation" not in cert
        assert "a`b" not in cert and "(exit code: missing)" in cert


# --------------------------------------------------------------------------- stale_context_detector


class TestStaleContextDetector:
    def test_pdf_diff_reads_every_page(self, tmp_path):
        p = tmp_path / "doc.pdf"
        data = _long_pdf()
        p.write_bytes(data)
        from aura_core.tools.open._input import read_source_bytes

        cached = read_source_bytes(data).text
        res = StaleContextDetector.compute_diff_from_cached(str(p), cached)
        assert res["has_external_edits"] is False and res["verified_unchanged"] is True and res["input"]["pages"] == 45
        # the edit is on the very last page
        p.write_bytes(_long_pdf("LAST PAGE WAS EDITED BY SOMEONE ELSE"))
        res = StaleContextDetector.compute_diff_from_cached(str(p), cached)
        assert res["has_external_edits"] is True and "EDITED BY SOMEONE" in res["unified_diff"]

    def test_scanned_page_prevents_an_unchanged_verdict(self, tmp_path):
        p = tmp_path / "scan.pdf"
        data = _long_pdf(scanned_page=5)
        p.write_bytes(data)
        from aura_core.tools.open._input import read_source_bytes

        res = StaleContextDetector.compute_diff_from_cached(str(p), read_source_bytes(data).text)
        assert res["complete"] is False and res["verified_unchanged"] is False and res["warnings"]

    def test_binary_non_pdf_is_refused_not_decoded(self, tmp_path):
        p = tmp_path / "b.bin"
        p.write_bytes(b"\x00\x01\x02binary")
        res = StaleContextDetector.compute_diff_from_cached(str(p), "")
        assert "binary" in res["error"] and res["has_external_edits"] is True

    def test_empty_batch_is_not_all_fresh(self):
        res = StaleContextDetector.batch_verify_staleness([])
        assert res["all_fresh"] is False and res["recommended_action"] != "PROCEED"

    def test_bad_snapshot_entries_do_not_raise(self):
        res = StaleContextDetector.batch_verify_staleness([None, 5, {}])  # type: ignore[list-item]
        assert res["all_fresh"] is False and res["stale_count"] == 3

    def test_uppercase_cached_hash_is_still_fresh(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("hello")
        snap = StaleContextDetector.record_snapshot(str(p))
        res = StaleContextDetector.inspect_staleness(str(p), snap["hash"].upper(), snap["mtime"])
        assert res["is_stale"] is False

    def test_base_dir_confines_every_entry_point(self, tmp_path):
        inner = tmp_path / "in"
        inner.mkdir()
        out = tmp_path / "o.txt"
        out.write_text("x")
        assert StaleContextDetector.compute_file_sha256(str(out), base_dir=str(inner)) is None
        assert StaleContextDetector.record_snapshot(str(out), base_dir=str(inner))["exists"] is False
        assert StaleContextDetector.inspect_staleness(str(out), "x", base_dir=str(inner))["is_stale"] is True
        assert StaleContextDetector.compute_diff_from_cached(str(out), "x", base_dir=str(inner))["has_external_edits"] is True

    def test_diff_without_trailing_newline_is_well_formed(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("a\nb")
        res = StaleContextDetector.compute_diff_from_cached(str(p), "a\nc")
        assert "-c\n" in res["unified_diff"] and "+b\n" in res["unified_diff"]


# --------------------------------------------------------------------------- project_status_qa_tool


class TestProjectStatusQaTool:
    def test_one_finished_stage_is_not_completed(self):
        hist = [{"stage": "build", "status": "success"}, {"stage": "test", "status": "running"}]
        assert ProjectStatusQaTool.summarize_stage_progress(hist)["overall_status"] == "RUNNING"
        hist[1]["status"] = "passed"
        assert ProjectStatusQaTool.summarize_stage_progress(hist)["overall_status"] == "COMPLETED"

    def test_stage_question_on_empty_history_does_not_crash_or_invent(self):
        res = ProjectStatusQaTool.answer_status_question("which stage are we at?", [])
        assert "No execution history" in res["answer"]

    def test_empty_history_is_not_reported_as_no_errors(self):
        res = ProjectStatusQaTool.answer_status_question("any errors?", [])
        assert "nothing can be said" in res["answer"]

    def test_success_message_mentioning_errors_is_not_an_error(self):
        hist = [{"stage": "lint", "status": "success", "message": "0 errors, error handling added"}]
        assert ProjectStatusQaTool.get_error_digest(hist)["has_errors"] is False

    def test_explicit_error_kind_wins_and_arabic_is_detected(self):
        hist = [{"stage": "a", "status": "failed", "message": "import took a timeout", "error_kind": "DbDown"},
                {"stage": "b", "status": "running", "message": "حدث خطأ في الاتصال"}]
        digest = ProjectStatusQaTool.get_error_digest(hist)
        assert digest["error_categories"] == {"DbDown": 1, "GenericError": 1}

    def test_words_like_is_do_not_match_everything(self):
        hist = [{"stage": "build", "status": "ok", "message": "this list is fine"}]
        assert ProjectStatusQaTool.query_history("what is the status", hist)["matches_found"] == 0
        assert ProjectStatusQaTool.query_history("", hist)["matches_found"] == 0
        assert ProjectStatusQaTool.query_history("build", hist)["matches_found"] == 1

    def test_non_dict_history_entries_do_not_raise(self):
        res = ProjectStatusQaTool.summarize_stage_progress([None, "x", {"stage": "a", "status": "done"}])  # type: ignore[list-item]
        assert res["overall_status"] == "COMPLETED" and res["skipped_records"] == 2

    def test_timeline_orders_iso_timestamps_and_unknown_ones_keep_place(self):
        tl = ProjectStatusQaTool.reconstruct_timeline([
            {"stage": "late", "timestamp": "2026-11-20T10:00:10Z"},
            {"stage": "early", "timestamp": "2026-11-20T10:00:00Z"},
            {"stage": "undated"},
        ])
        # the undated record followed "early" in the input, so it stays next to it
        assert [t["stage"] for t in tl] == ["early", "undated", "late"]
        assert tl[2]["offset_seconds"] == 10.0 and tl[1]["timestamp_known"] is False


# --------------------------------------------------------------------------- acceptance_criteria_verifier


class TestAcceptanceCriteriaVerifier:
    def test_failed_count_in_summary_blocks_acceptance(self):
        res = AcceptanceCriteriaVerifier.verify_against_test_results(
            ["All unit tests pass"], {"passed": 5, "failed": 2, "coverage_percentage": 95}
        )
        assert res["all_satisfied"] is False and res["contradicted_criteria"]

    def test_green_suite_does_not_satisfy_unrelated_password_criterion(self):
        res = AcceptanceCriteriaVerifier.verify_against_test_results(
            ["Passwords are hashed"], {"passed": 5, "failed": 0, "coverage_percentage": 95}
        )
        assert res["all_satisfied"] is False and res["unverified_criteria"] == ["Passwords are hashed"]

    def test_empty_criterion_and_empty_key_never_match(self):
        res = AcceptanceCriteriaVerifier.verify_criteria([""], {"": True, "x": True})
        assert res["all_satisfied"] is False

    def test_failure_words_in_evidence_text_are_not_success(self):
        res = AcceptanceCriteriaVerifier.verify_criteria(["login works"], {"login works": "failed"})
        assert res["breakdown"]["login works"] is False
        res = AcceptanceCriteriaVerifier.verify_criteria(["login works"], {"notes": "login works: 3 failed"})
        assert res["breakdown"]["login works"] is False
        res = AcceptanceCriteriaVerifier.verify_criteria(["login works"], {"login works": "verified by manual run"})
        assert res["all_satisfied"] is True

    def test_duplicate_criteria_are_counted_once(self):
        res = AcceptanceCriteriaVerifier.verify_criteria(["a thing", "a thing"], {"a thing": True})
        assert res["all_satisfied"] is True and res["total_criteria"] == 1

    def test_badge_is_computed_from_counts(self):
        report = AcceptanceCriteriaVerifier.generate_acceptance_badge_report(
            {"all_satisfied": True, "verified_count": 1, "total_criteria": 3, "breakdown": {"a|b\nc": False}}
        )
        assert "PENDING" in report and "a\\|b c" in report

    def test_numbered_and_bulleted_criteria_in_any_script(self):
        md = "# معايير القبول\n- يجب أن يعمل النظام دون أخطاء\n1. يجب حفظ البيانات بنجاح\n2. lower case requirement here\n"
        crit = AcceptanceCriteriaVerifier.parse_criteria_from_markdown(md)
        assert len(crit) == 3

    def test_proof_type_uses_words_not_substrings(self):
        out = AcceptanceCriteriaVerifier.convert_requirements_to_criteria(["Store items in the terms table", "Passwords hashed"])
        assert [c["proof_type"] for c in out] == ["FUNCTIONAL_OUTPUT", "FUNCTIONAL_OUTPUT"]

    def test_spec_pdf_is_read_in_full_including_last_page(self, tmp_path):
        pages = [{"text": [f"1. Requirement number {i} must be satisfied"]} for i in range(1, 45)]
        pages.append({"text": ["2. The ZEBRA-LAST-PAGE-MARKER-7731 requirement holds"]})
        p = tmp_path / "spec.pdf"
        p.write_bytes(_pdf(pages))
        res = AcceptanceCriteriaVerifier.parse_criteria_from_file(str(p))
        assert res["complete"] is True and res["input"]["pages"] == 45 and len(res["criteria"]) == 45
        assert any(MARKER in c for c in res["criteria"])
        assert len(AcceptanceCriteriaVerifier.parse_criteria_from_markdown(p.read_bytes())) == 45

    def test_spec_pdf_with_scanned_page_is_never_silently_partial(self, tmp_path):
        data = _long_pdf(scanned_page=2)
        p = tmp_path / "spec.pdf"
        p.write_bytes(data)
        res = AcceptanceCriteriaVerifier.parse_criteria_from_file(str(p))
        assert res["complete"] is False and res["warnings"]
        with pytest.raises(CriteriaInputError):
            AcceptanceCriteriaVerifier.parse_criteria_from_markdown(data)

    def test_missing_file_and_base_dir(self, tmp_path):
        assert "error" in AcceptanceCriteriaVerifier.parse_criteria_from_file(str(tmp_path / "nope.md"))
        (tmp_path / "o.md").write_text("- [ ] a criterion")
        inner = tmp_path / "in"
        inner.mkdir()
        assert "error" in AcceptanceCriteriaVerifier.parse_criteria_from_file(str(tmp_path / "o.md"), base_dir=str(inner))


# --------------------------------------------------------------------------- ambiguity_and_clarification_detector


class TestAmbiguityDetector:
    def test_non_blocking_alone_is_not_a_conflict(self):
        assert AmbiguityAndClarificationDetector.detect_conflicting_requirements(["The API must be non-blocking"]) == []
        assert AmbiguityAndClarificationDetector.detect_conflicting_requirements(
            ["async handlers", "plus synchronous blocking calls"]
        )

    def test_arabic_vague_terms_and_error_handling_are_understood(self):
        res = AmbiguityAndClarificationDetector.analyze_task_description(
            "اجعل النظام سريع وسهل الاستخدام مع معالجة الأخطاء عند فشل الاتصال"
        )
        assert any("سريع" in a for a in res["ambiguities"])
        assert not any("error handling" in a for a in res["ambiguities"])
        missing = AmbiguityAndClarificationDetector.check_missing_specifications("يجب تخزين البيانات في قاعدة بيانات")
        assert "persistence" not in missing["missing_aspects"]

    def test_none_and_wrong_types(self):
        assert AmbiguityAndClarificationDetector.analyze_task_description(None)["readiness_status"] == "NEEDS_CLARIFICATION"
        with pytest.raises(AmbiguityInputError):
            AmbiguityAndClarificationDetector.score_ambiguity(123)
        with pytest.raises(AmbiguityInputError):
            AmbiguityAndClarificationDetector.score_ambiguity(b"\x00\x01binary")

    def test_domain_context_is_used(self):
        qs = AmbiguityAndClarificationDetector.generate_clarification_questions(
            "Build a report generator for sales data please", domain_context="Exposes a REST api endpoint"
        )
        assert not any("calling convention" in q["question"] for q in qs)

    def test_document_pdf_is_analysed_in_full(self, tmp_path):
        pages = [{"text": [f"Section {i} describes the exported report feature"]} for i in range(1, 45)]
        pages.append({"text": ["Last page: make it fast, robust and seamless etc"]})
        p = tmp_path / "spec.pdf"
        p.write_bytes(_pdf(pages))
        res = AmbiguityAndClarificationDetector.analyze_document(str(p))
        assert res["input"]["pages"] == 45 and res["input"]["complete"] is True
        assert any("fast" in a for a in res["ambiguities"])  # the vague words are only on page 45

    def test_document_with_unread_pages_is_never_ready(self, tmp_path):
        p = tmp_path / "scan.pdf"
        p.write_bytes(_long_pdf(scanned_page=4))
        res = AmbiguityAndClarificationDetector.analyze_document(str(p))
        assert res["readiness_status"] == "INCOMPLETE_INPUT" and res["warnings"]
        with pytest.raises(AmbiguityInputError):
            AmbiguityAndClarificationDetector.analyze_task_description(_long_pdf(scanned_page=4))

    def test_document_errors_and_base_dir(self, tmp_path):
        assert AmbiguityAndClarificationDetector.analyze_document(str(tmp_path / "x"))["readiness_status"] == "UNREADABLE_INPUT"
        (tmp_path / "o.txt").write_text("hello")
        inner = tmp_path / "in"
        inner.mkdir()
        assert "error" in AmbiguityAndClarificationDetector.analyze_document(str(tmp_path / "o.txt"), base_dir=str(inner))


# --------------------------------------------------------------------------- artifact_generator


class TestArtifactGenerator:
    def test_walkthrough_does_not_claim_completion_without_results(self):
        none = ArtifactGenerator.generate_walkthrough("t", "s", [])
        assert "Complete & Ready" not in none["markdown_content"] and none["verified"] is False
        unrun = ArtifactGenerator.generate_walkthrough("t", "s", [{"action": "a"}])
        assert unrun["verified"] is False and "Not verified" in unrun["markdown_content"]
        ok = ArtifactGenerator.generate_walkthrough("t", "s", [{"action": "a", "passed": True}])
        assert ok["verified"] is True
        bad = ArtifactGenerator.generate_walkthrough("t", "s", [{"action": "a", "passed": "false"}])
        assert bad["verification_failed"] == 1

    def test_command_with_a_code_fence_cannot_break_out(self):
        md = ArtifactGenerator.generate_walkthrough("t", "s", [{"action": "a", "command": "echo ```\n## injected"}])["markdown_content"]
        assert "````bash" in md and md.count("````") == 2

    def test_titles_and_cells_are_single_line_and_escaped(self):
        out = ArtifactGenerator.generate("task_list", "T\n## forged", ["a\n- [x] forged"])["markdown_content"]
        assert "\n## forged" not in out and "\n- [x]" not in out
        table = ArtifactGenerator.generate_task_list("t", [{"name": "a|b\nc", "completed": "false"}])
        assert table["completed_tasks"] == 0 and "a\\|b c" in table["markdown_content"]

    def test_summary_cannot_forge_a_heading(self):
        md = ArtifactGenerator.generate_walkthrough("t", "x\n## Verification Status: all good", [])["markdown_content"]
        assert "\n## Verification Status: all good" not in md

    def test_wrong_types_do_not_raise(self):
        assert ArtifactGenerator.generate(None, None, None, "meta")["artifact_type"] == "walkthrough"  # type: ignore[arg-type]
        assert ArtifactGenerator.generate_progress_report("t", None, None)["completion_rate"] == 0.0  # type: ignore[arg-type]
        assert ArtifactGenerator.generate_task_list("t", ["plain string"])["total_tasks"] == 1  # type: ignore[list-item]
        assert ArtifactGenerator.generate_implementation_plan("t", [None])["stages_count"] == 1  # type: ignore[list-item]

    def test_unknown_type_is_flagged(self):
        assert ArtifactGenerator.generate("nonsense", "t", ["a"])["warnings"]

    def test_save_is_confined_and_limited_to_text_files(self, tmp_path):
        inner = tmp_path / "in"
        inner.mkdir()
        assert ArtifactGenerator.save_artifact_to_disk("x", str(tmp_path / "o.md"), base_dir=str(inner))["saved"] is False
        assert ArtifactGenerator.save_artifact_to_disk("x", str(inner / ".." / "o.md"), base_dir=str(inner))["saved"] is False
        assert ArtifactGenerator.save_artifact_to_disk("x", str(inner / "run.sh"))["saved"] is False
        assert ArtifactGenerator.save_artifact_to_disk("x", str(inner))["saved"] is False
        ok = ArtifactGenerator.save_artifact_to_disk("# عربي", str(inner / "sub" / "a.md"), base_dir=str(inner))
        assert ok["saved"] is True and (inner / "sub" / "a.md").read_text(encoding="utf-8") == "# عربي"
        assert ArtifactGenerator.save_artifact_to_disk(None, str(inner / "b.md"))["saved"] is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- artifact_feedback_ingestor


class TestArtifactFeedbackIngestor:
    @pytest.mark.parametrize(
        "text,intent",
        [
            ("I don't approve this", "REJECT"),
            ("disapprove", "REJECT"),
            ("please address the padding", "GENERAL_COMMENT"),
            ("looks good but remove step 3", "REMOVE_STEP"),
            ("looks good", "APPROVE"),
            ("موافق", "APPROVE"),
            ("مرفوض", "REJECT"),
            ("احذف خطوة النشر", "REMOVE_STEP"),
            ("", "GENERAL_COMMENT"),
        ],
    )
    def test_intent(self, text, intent):
        assert ArtifactFeedbackIngestor.classify_feedback_intent(text)["intent"] == intent

    def test_remove_uses_distinctive_words_only(self):
        plan = ["Write tests", "Deploy to prod", "Please review docs"]
        res = ArtifactFeedbackIngestor.apply_feedback(plan, "remove the deploy step please")
        assert res["revised_plan"] == ["Write tests", "Please review docs"]

    def test_remove_with_only_stopwords_removes_nothing(self):
        plan = ["Write tests", "Deploy to prod"]
        res = ArtifactFeedbackIngestor.apply_feedback(plan, "remove everything related to this plan")
        assert res["revised_plan"] == plan

    def test_remove_by_number(self):
        plan = ["a one", "b two", "c three"]
        assert ArtifactFeedbackIngestor.apply_feedback(plan, "remove step 2")["revised_plan"] == ["a one", "c three"]
        out = ArtifactFeedbackIngestor.apply_feedback(plan, "remove step 9")
        assert out["revised_plan"] == plan and "does not exist" in out["actions"][0]

    def test_wrong_types_do_not_split_strings_into_characters(self):
        res = ArtifactFeedbackIngestor.resolve_plan_conflicts(None, "add logging")  # type: ignore[arg-type]
        assert res["resolved_plan"] == ["add logging"]

    def test_patch_prefers_exact_title_and_ignores_fenced_headings(self):
        md = "# T\n## Test-plan\nx\n## Test\nold\n```\n# not a heading\n```\n## Next\nn\n"
        out = ArtifactFeedbackIngestor.patch_markdown_artifact(md, "Test", "new")["content"]
        assert "## Test-plan\nx" in out and "## Test\nnew\n\n## Next" in out and out.endswith("\n")
        fenced = ArtifactFeedbackIngestor.patch_markdown_artifact("## A\n```\n# B\n```\n", "B", "z")
        assert fenced["action"] == "appended_new_section"

    def test_patch_validates_its_arguments(self):
        assert ArtifactFeedbackIngestor.patch_markdown_artifact("# a", "", "x")["patched"] is False
        assert ArtifactFeedbackIngestor.patch_markdown_artifact("# a", "a", "x", mode="nuke")["patched"] is False
        assert ArtifactFeedbackIngestor.patch_markdown_artifact(None, "a", "x")["patched"] is False  # type: ignore[arg-type]

    def test_a_pdf_cannot_be_patched_but_is_read_in_full_first(self):
        res = ArtifactFeedbackIngestor.patch_markdown_artifact(_long_pdf(), "A", "x")  # type: ignore[arg-type]
        assert res["patched"] is False and "45 page" in res["error"]

    def test_diff_of_two_pdfs_sees_the_last_page(self):
        res = ArtifactFeedbackIngestor.generate_feedback_diff(_long_pdf(), _long_pdf("THE NEW LAST PAGE TEXT HERE"))  # type: ignore[arg-type]
        assert res["has_changes"] and MARKER in res["unified_diff"] and "THE NEW LAST PAGE" in res["unified_diff"]
        assert res["complete"] is True and res["input"]["original"]["pages"] == 45

    def test_diff_with_unread_pages_says_so(self):
        res = ArtifactFeedbackIngestor.generate_feedback_diff(_long_pdf(scanned_page=3), _long_pdf())  # type: ignore[arg-type]
        assert res["complete"] is False and res["warnings"]

    def test_diff_without_trailing_newline_and_plus_plus_lines(self):
        res = ArtifactFeedbackIngestor.generate_feedback_diff("a\nb", "a\nc")
        assert "-b\n" in res["unified_diff"] and "+c\n" in res["unified_diff"]
        assert ArtifactFeedbackIngestor.generate_feedback_diff("a\n", "a\n++x\n")["lines_added"] == 1

    def test_bad_diff_types_return_an_error(self):
        assert "error" in ArtifactFeedbackIngestor.generate_feedback_diff(5, "x")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- group-wide constraints


GROUP = [
    "artifact_generator", "artifact_feedback_ingestor", "verification_evidence_collector", "ui_visual_verifier",
    "mobile_artifact_summarizer", "project_status_qa_tool", "stale_context_detector", "scope_and_diff_guard",
    "regression_impact_analyzer", "acceptance_criteria_verifier", "ambiguity_and_clarification_detector",
]


def test_no_tool_in_the_group_can_reach_the_network_or_the_gateway():
    """None of these tools does network I/O, so there is nothing to route through the URL deny policy."""
    import ast
    from pathlib import Path

    import aura_core.tools.open as pkg

    banned_roots = {"requests", "urllib", "urllib3", "http", "socket", "aiohttp", "httpx", "websockets", "ssl", "ftplib", "smtplib"}
    banned_aura = ("aura_core.api", "aura_core.container", "aura_core.adapters", "aura_core.config", "aura_core.auth", "aura_core.kernel")
    for name in GROUP:
        tree = ast.parse((Path(pkg.__file__).parent / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for m in mods:
                assert m.split(".")[0] not in banned_roots, f"{name} imports {m}"
                assert not m.startswith(banned_aura), f"{name} imports {m}"
        source = (Path(pkg.__file__).parent / f"{name}.py").read_text(encoding="utf-8")
        assert "/api/v3" not in source and "aureon-ai.tech" not in source


def test_time_budgets_are_generous_overridable_and_capped():
    from aura_core.tools.open import regression_impact_analyzer as r

    assert r._budget(None) == 180.0 and r._budget("junk") == 180.0 and r._budget(-5) == 180.0
    assert r._budget(300) == 300.0 and r._budget(10_000) == 600.0


def test_repository_scan_that_runs_out_of_time_says_it_is_truncated(tmp_path):
    (tmp_path / "a.py").write_text("import x\n")
    res = RegressionImpactAnalyzer.analyze_impact(["m.py"], repo_dir=str(tmp_path), time_budget_s=1e-9)
    assert res["scan_truncated"] is True and any("limit" in w for w in res["warnings"])
    full = RegressionImpactAnalyzer.analyze_impact(["m.py"], repo_dir=str(tmp_path))
    assert full["scan_truncated"] is False and full["files_scanned"] == 1
