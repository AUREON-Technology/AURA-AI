"""Review of the knowledge / ML / research ``tools/open`` classes.

Real PDFs (built byte by byte by ``tests.unit.test_document_extraction._pdf`` and read by real
``pypdf``) prove that the two classes that take a document read *every* page. The six classes
ADR-036 marks superseded are imported here on purpose -- ``aura_core`` may not import them, tests
may -- to prove they at least never raise on bad input and never give a false all-clear.

Each test pins a behaviour that was wrong before this review.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from aura_core.tools.open.automated_quiz_generator import AutomatedQuizGenerator as Quiz
from aura_core.tools.open.autonomous_research_and_synthesis_tool import (
    AutonomousResearchAndSynthesisTool as Research,
)
from aura_core.tools.open.data_lineage_provenance_tracker import DataLineageProvenanceTracker
from aura_core.tools.open.gpu_resource_scheduler import GpuResourceScheduler
from aura_core.tools.open.knowledge_graph_manager import KnowledgeGraphManager
from aura_core.tools.open.model_registry_and_evaluation_manager import ModelRegistryAndEvaluationManager
from aura_core.tools.open.workflow_dependency_validator import WorkflowDependencyValidator as Workflow
from tests.unit.test_document_extraction import _pdf

NAN = float("nan")
INF = float("inf")


def _tag(i: int) -> str:
    return chr(97 + i // 26) + chr(97 + i % 26)  # 0 -> "aa", 44 -> "bs"


@pytest.fixture(scope="module")
def long_pdf(tmp_path_factory) -> str:
    """45 text pages; each has two terms that occur nowhere else; a number-bearing sentence and a
    distinctive marker sit on the LAST page only."""
    pages = []
    for i in range(45):
        lines = [f"entity{_tag(i)} relates to widget{_tag(i)} in section {i + 1}"]
        if i == 44:
            lines.append("The final benchmark reduced latency by 87% on zebracrossing.")
        pages.append({"text": lines})
    path = tmp_path_factory.mktemp("pdf") / "long.pdf"
    path.write_bytes(_pdf(pages))
    return str(path)


@pytest.fixture()
def scanned_pdf(tmp_path) -> str:
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf([
        {"text": ["Throughput rose 40% after the change."]},
        {"image": True},
        {"text": ["Cost fell 12% too."]},
    ]))
    return str(path)


# ======================================================================================
# AutonomousResearchAndSynthesisTool -- reads files (PDF in full)
# ======================================================================================


class TestResearchFile:
    def test_pdf_last_page_is_read(self, long_pdf) -> None:
        out = Research.extract_claims_from_file(long_pdf)
        assert out["input"]["kind"] == "pdf"
        assert out["input"]["pages"] == 45 and out["input"]["pages_read"] == 45
        assert out["complete"] is True and "warnings" not in out
        assert any("zebracrossing" in f["finding"] and f["contains_quantifier"] for f in out["findings"])

    def test_scanned_page_is_named_never_all_clear(self, scanned_pdf) -> None:
        out = Research.extract_claims_from_file(scanned_pdf)
        assert out["complete"] is False
        assert out["input"]["unreadable_pages"] == [2]
        assert any("page(s) 2" in w for w in out["warnings"])
        # The readable pages are still used.
        assert {"Throughput rose 40% after the change.", "Cost fell 12% too."} <= {
            f["finding"] for f in out["findings"]
        }

    def test_bad_paths_return_an_error(self, tmp_path) -> None:
        (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02binary")
        (tmp_path / "bad.pdf").write_bytes(b"not a pdf at all")
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "outside.txt").write_text("Latency fell 50%.")
        for target, base in [
            (tmp_path / "missing.txt", None),
            (tmp_path, None),  # a directory
            (tmp_path / "bin.dat", None),
            (tmp_path / "bad.pdf", None),
            (sub / ".." / "outside.txt", sub),  # '..' escaping base_dir
            ("", None),
            ("\x00evil", None),
        ]:
            out = Research.extract_claims_from_file(target, base_dir=base)
            assert out["error"] and out["findings"] == [] and out["complete"] is False, target

    def test_text_file(self, tmp_path) -> None:
        f = tmp_path / "n.txt"
        f.write_text("Latency fell 50% overall. Nothing else here.", encoding="utf-8")
        out = Research.extract_claims_from_file(f)
        assert out["findings_count"] == 1 and out["input"]["kind"] == "text"


class TestResearchText:
    def test_percentages_are_found(self) -> None:
        # '\b' after '%' never matched before, so "50% overall" produced no finding at all.
        found = Research.extract_claims_and_evidence("Latency fell 50% overall. We saw 5 users. Plain prose.")
        assert [f["finding"] for f in found] == ["Latency fell 50% overall.", "We saw 5 users."]
        assert all(f["contains_quantifier"] for f in found)

    def test_arabic_sentences_and_digits(self) -> None:
        text = "هل نجح النظام؟ ارتفعت السرعة ٣٠٪ في الاختبار. حقق النموذج نتائج جيدة."
        found = [f["finding"] for f in Research.extract_claims_and_evidence(text)]
        assert "ارتفعت السرعة ٣٠٪ في الاختبار." in found
        assert "حقق النموذج نتائج جيدة." in found
        assert "هل نجح النظام؟" not in found  # split at the Arabic question mark

    def test_none_and_wrapped_lines(self) -> None:
        assert Research.extract_claims_and_evidence(None) == []
        assert Research.extract_claims_and_evidence("")  == []
        wrapped = Research.extract_claims_and_evidence("It reduced\nlatency by 5%\nin total.")
        assert wrapped[0]["finding"] == "It reduced latency by 5% in total."

    def test_domain_is_judged_by_host_not_by_substring(self) -> None:
        ranked = Research.rank_sources_by_credibility([
            {"url": "https://evil.io/?u=github.com", "title": "spoof query"},
            {"url": "https://evil.com/a.org/b", "title": "spoof path"},
            {"url": "https://github.com.evil.io/x", "title": "spoof subdomain"},
            {"url": "https://www.arxiv.org/abs/1", "title": "real"},
        ])
        assert ranked[0]["title"] == "real"
        scores = {r["title"]: r["credibility_score"] for r in ranked}
        assert scores["spoof query"] == scores["spoof path"] == scores["spoof subdomain"] == 1.5
        assert isinstance(Research.HIGH_CREDIBILITY_DOMAINS, tuple)  # not a shared mutable list

    def test_case_and_spacing_differences_are_not_a_conflict(self) -> None:
        none = Research.find_conflicting_claims([
            {"topic": "Speed", "statement": "Fast"}, {"topic": "speed ", "statement": " fast  "},
        ])
        assert none == []
        some = Research.find_conflicting_claims([
            {"topic": "speed", "statement": "fast"}, {"topic": "speed", "statement": "slow"},
        ])
        assert len(some) == 1

    def test_malformed_items_do_not_raise_and_are_reported(self) -> None:
        assert Research.normalize_sources(None)["unique_count"] == 0
        out = Research.normalize_sources(["x", None, {"title": "A", "url": "http://a"}])
        assert out["unique_count"] == 1 and out["skipped_invalid"] == 2 and out["input_count"] == 3
        rep = Research.synthesize_report("t", sources=["x"], claims=[1, 2])
        assert rep["ready"] is False and "warnings" in rep
        assert Research.synthesize_report(None)["ready"] is False
        assert Research.find_conflicting_claims(["x"]) == []
        assert Research.rank_sources_by_credibility(["x"]) == []

    def test_dossier_table_cannot_be_broken_and_duplicates_are_counted_once(self) -> None:
        md = Research.generate_research_dossier(
            "T",
            [{"title": "a|b\nc", "url": "http://x"}, {"title": "again", "url": "HTTP://X"}],
            None,
        )
        rows = [ln for ln in md.splitlines() if ln.startswith("| ") and "Credibility" not in ln]
        assert len(rows) == 1 and "a\\|b c" in rows[0]
        assert "across 1 sources" in md
        assert ":mag: one" in Research.generate_research_dossier("T", [], "one")


# ======================================================================================
# AutomatedQuizGenerator -- reads files (PDF in full)
# ======================================================================================


class TestQuizFile:
    def test_every_page_of_a_pdf_feeds_the_quiz(self, long_pdf) -> None:
        # Asking for more questions than there are terms must reach the last page's terms.
        out = Quiz.generate_quiz_from_file("Report", long_pdf, num_questions=500)
        terms = {q["question"].split("'")[1] for q in out["questions"]}
        assert "entityaa" in terms and "widgetbs" in terms  # first page and 45th page
        assert "zebracrossing" in terms
        assert out["input"]["pages"] == 45 and out["complete"] is True

    def test_few_questions_are_spread_across_the_document(self, long_pdf) -> None:
        # Before: concepts were the first 20 tokens, so a 3-question quiz on a 45-page PDF only
        # ever asked about page 1.
        out = Quiz.generate_quiz_from_file("Report", long_pdf, num_questions=3)
        pages = []
        for q in out["questions"]:
            term = q["question"].split("'")[1]
            m = re.fullmatch(r"(?:entity|widget)([a-z]{2})", term)
            if m:
                pages.append((ord(m.group(1)[0]) - 97) * 26 + ord(m.group(1)[1]) - 97)
        assert pages and max(pages) >= 30, pages
        assert all("source_excerpt" in q for q in out["questions"])

    def test_assessment_from_pdf(self, long_pdf) -> None:
        out = Quiz.generate_comprehension_assessment_from_file("Report", long_pdf)
        assert out["complete"] is True and out["input"]["pages"] == 45
        items = out["true_false_questions"]
        assert [i["expected"] for i in items] == [True, False, True, False]
        late = [i for i in items if i["statement"].startswith("'widget") or i["statement"].startswith("'entity")]
        assert late, "statements must come from the document, not only its opening"

    def test_scanned_page_marks_the_quiz_incomplete(self, scanned_pdf) -> None:
        out = Quiz.generate_quiz_from_file("Report", scanned_pdf)
        assert out["complete"] is False and out["input"]["unreadable_pages"] == [2]
        assert any("page(s) 2" in w for w in out["warnings"])

    def test_document_without_text_gives_no_quiz(self, tmp_path) -> None:
        pdf = tmp_path / "all_scans.pdf"
        pdf.write_bytes(_pdf([{"image": True}, {"image": True}]))
        out = Quiz.generate_quiz_from_file("Report", pdf)
        assert out["questions"] == [] and "no readable text" in out["error"]
        empty = tmp_path / "empty.txt"
        empty.write_text("   ")
        assert Quiz.generate_comprehension_assessment_from_file("T", empty)["true_false_questions"] == []

    def test_bad_paths_return_an_error(self, tmp_path) -> None:
        (tmp_path / "x.bin").write_bytes(b"\x00\xff\x00")
        for target in (tmp_path / "nope.txt", tmp_path, tmp_path / "x.bin"):
            assert Quiz.generate_quiz_from_file("T", target)["error"]
            assert Quiz.generate_comprehension_assessment_from_file("T", target)["error"]
        out = Quiz.generate_quiz_from_file("T", tmp_path / ".." / "x.bin", base_dir=tmp_path)
        assert "outside" in out["error"]


class TestQuizText:
    def test_arabic_terms_are_extracted(self) -> None:
        # '[A-Za-z_]' tokenising returned [] for Arabic, so the quiz was about nothing.
        concepts = Quiz.extract_key_concepts("هذا نظام الذكاء الاصطناعي المتقدم مُحَمَّد")
        assert concepts == ["نظام", "الذكاء", "الاصطناعي", "المتقدم", "محمد"]
        quiz = Quiz.generate_quiz("نظام", "نظام الذكاء الاصطناعي. التعلم العميق مهم.", 2)
        assert quiz["grounded_in_source"] is True and "نظام" in quiz["questions"][0]["question"]

    def test_correct_answer_is_not_always_a(self) -> None:
        text = " ".join(f"concept{_tag(i)}" for i in range(24))
        quiz = Quiz.generate_quiz("Topic", text, 12)
        answers = [q["correct_answer"] for q in quiz["questions"]]
        assert len(set(answers)) > 1
        for q in quiz["questions"]:
            letter = q["correct_answer"]
            marked = [o for o in q["options"] if o.startswith(letter + ")")]
            assert len(marked) == 1 and "core logic" in marked[0]
        assert quiz == Quiz.generate_quiz("Topic", text, 12)  # deterministic

    def test_awkward_counts_and_inputs_never_raise(self) -> None:
        for n in (2.5, NAN, INF, -3, 0, None, "3", True):
            assert Quiz.generate_quiz("t", "alpha beta gamma delta", n)["questions_count"] >= 1
        none = Quiz.generate_quiz(None, None)
        assert none["grounded_in_source"] is False and none["warnings"]
        short = Quiz.generate_quiz("t", "alpha beta", 5)
        assert short["requested_questions"] == 5 and short["questions_count"] == 2 and short["warnings"]
        assert Quiz.extract_key_concepts(None) == []
        assert Quiz.extract_key_concepts("alpha", NAN) == ["alpha"]

    def test_comprehension_true_false_mix(self) -> None:
        # All-True statements meant "answer True to everything" scored 100%.
        out = Quiz.generate_comprehension_assessment("T", "alpha beta gamma delta")
        assert [i["expected"] for i in out["true_false_questions"]] == [True, False, True, False]
        assert Quiz.generate_comprehension_assessment("T", None)["warnings"]

    def test_json_style_string_keys_are_graded(self) -> None:
        # JSON object keys are strings; ``{"1": "A"}`` against ``{1: "A"}`` used to score 0.
        out = Quiz.evaluate_quiz_answers({"1": "A", "2": "b)", "3": "c. text"}, {1: "A", 2: "B", 3: "C"})
        assert out["correct_answers"] == 3 and out["passed"] is True
        out = Quiz.evaluate_quiz_answers({"1": "A", "9": "A"}, {1: "A", 2: "B"})
        assert out["correct_answers"] == 1 and out["unmatched_answer_ids"] == ["9"]

    def test_grading_does_not_treat_a_word_as_its_first_letter(self) -> None:
        out = Quiz.evaluate_quiz_answers({1: "a dog"}, {1: "A cat"})
        assert out["correct_answers"] == 0
        assert Quiz.evaluate_quiz_answers({1: ""}, {1: ""})["correct_answers"] == 0  # blank is not right

    def test_grading_bad_input(self) -> None:
        assert Quiz.evaluate_quiz_answers(None, {1: "A"})["correct_answers"] == 0
        empty = Quiz.evaluate_quiz_answers({}, {})
        assert empty["passed"] is False and empty["score_percentage"] == 0.0 and empty["error"]
        assert Quiz.evaluate_quiz_answers({1: "A"}, None)["total_questions"] == 0

    def test_coding_challenge_is_always_valid_python_and_consistent(self) -> None:
        import ast

        for topic in ("نظام", "", 'he said """hi"""', 'ends with "', "back\\slash", "3d cache", None):
            c = Quiz.generate_coding_challenge(topic)
            ast.parse(c["starter_code"])
            ast.parse(c["test_template"])
            name = re.match(r"def (\w+)\(", c["function_signature"]).group(1)
            assert name.isidentifier() and name in c["test_template"]
        # The tests call the function the starter really defines, not a name derived from the topic.
        c = Quiz.generate_coding_challenge("x", "def parse_rows(rows: list) -> int:")
        assert "parse_rows(" in c["test_template"] and "solve_x" not in c["test_template"]
        a = Quiz.generate_coding_challenge("x", "async def fetch(a):")
        ast.parse(a["test_template"])
        assert "asyncio.run(fetch(" in a["test_template"]

    @pytest.mark.parametrize("sig", ["def f(a): pass", "import os", "def f(:", "x = 1", "def f(a):\nimport os", 5])
    def test_bad_signature_is_refused(self, sig) -> None:
        assert "error" in Quiz.generate_coding_challenge("x", sig)


# ======================================================================================
# Superseded classes: no crash, no false all-clear (kept for reference, never wired)
# ======================================================================================


class TestKnowledgeGraphManager:
    def test_returned_triples_are_copies(self) -> None:
        kg = KnowledgeGraphManager()
        kg.add_relation("a", "r", "b", {"k": {"n": 1}})
        kg.add_relation("a", "r", "b", {"z": 1})["triple"]["metadata"]["k"]["n"] = 99
        kg.query_relations()[0]["metadata"]["k"]["n"] = 98
        assert kg.triples[0]["metadata"] == {"k": {"n": 1}, "z": 1}

    def test_invalid_input_is_rejected_not_stored(self) -> None:
        kg = KnowledgeGraphManager()
        for args in ((None, "r", "t"), ("", "r", "t"), (1, "r", 2), ("a", "r", "b", "not-a-dict")):
            assert "error" in kg.add_relation(*args)
        assert kg.triples == []
        assert kg.export_to_mermaid("bad\nX").startswith("graph TD")

    def test_mermaid_names_cannot_collide_or_break_syntax(self) -> None:
        kg = KnowledgeGraphManager()
        kg.add_relation("a b", "r", "a_b")
        kg.add_relation('x"y', "is", "end")
        kg.add_relation("😀", "r", "😁")
        kg.add_relation("سلام", "يعرف", "مرحبا")
        lines = kg.export_to_mermaid().splitlines()[1:]
        ids = {part.split("[")[0] for ln in lines for part in re.findall(r"n\d+\[", ln)}
        assert len(ids) == 8  # eight distinct names, eight distinct ids
        assert '"x#quot;y"' in lines[1] and "end[" not in lines[1]

    def test_negated_relation_is_a_conflict(self) -> None:
        kg = KnowledgeGraphManager()
        kg.add_relation("A", "is_a", "B")
        assert kg.detect_conflicting_facts() == []
        kg.add_relation("A", "is_not_a", "B")
        kg.add_relation("X", "uses", "Y")
        kg.add_relation("X", "not_uses", "Y")
        found = {(c["entity"], c["relation"]) for c in kg.detect_conflicting_facts()}
        assert found == {("A", "is_a"), ("X", "uses")}
        kg.add_relation("P", "owner", "u1")
        kg.add_relation("P", "owner", "u2")
        assert any(c["relation"] == "owner" for c in kg.detect_conflicting_facts(["owner"]))

    def test_path_search_is_bounded_and_says_so(self) -> None:
        kg = KnowledgeGraphManager()
        names = [f"n{i}" for i in range(10)]
        for a in names:
            for b in names:
                if a != b:
                    kg.add_relation(a, "r", b)
        started = time.monotonic()
        out = kg.find_paths("n0", "n9", max_depth=8, max_paths=50)
        assert time.monotonic() - started < 5
        assert len(out["paths"]) == 50 and out["truncated"] is True
        assert len(kg.find_paths_between_entities("n0", "n9", max_depth=8)) <= 100
        small = KnowledgeGraphManager()
        small.add_relation("a", "r", "b")
        assert small.find_paths("a", "b") == {"paths": [[{"from": "a", "relation": "r", "to": "b"}]], "truncated": False}
        assert "error" in small.find_paths("a", "b", max_depth="x")


class TestDataLineageProvenanceTracker:
    def test_long_chain_and_ring_do_not_raise_recursion_error(self) -> None:
        tracker = DataLineageProvenanceTracker()
        for i in range(3000):
            tracker.record_transformation(f"n{i}", f"n{i + 1}", "op")
        assert tracker.detect_circular_lineage() == []
        tracker.record_transformation("n3000", "n0", "op")
        cycles = tracker.detect_circular_lineage()
        assert len(cycles) == 1 and cycles[0][0] == cycles[0][-1] and len(cycles[0]) == 3002

    def test_cycles_are_reported_once_and_self_loops_found(self) -> None:
        tracker = DataLineageProvenanceTracker()
        tracker.record_transformation("a", "b", "x")
        tracker.record_transformation("a", "b", "x-again")
        tracker.record_transformation("b", "a", "y")
        tracker.record_transformation("c", "c", "self")
        assert sorted(tracker.detect_circular_lineage()) == [["a", "b", "a"], ["c", "c"]]

    def test_a_string_of_stages_is_one_stage(self) -> None:
        payload = DataLineageProvenanceTracker.build_lineage_payload("a", "b", "ingest")
        assert payload["lineage_path"] == ["a", "ingest", "b"] and payload["hops"] == 2
        assert DataLineageProvenanceTracker.build_lineage_payload("a", "b", [])["hops"] == 1

    def test_invalid_edges_are_rejected_and_results_are_copies(self) -> None:
        tracker = DataLineageProvenanceTracker()
        assert tracker.record_transformation(None, "b", "op")["recorded"] is False
        assert tracker.record_transformation("a", "", "op")["recorded"] is False
        assert tracker.record_transformation("a", "b", "op", "x")["recorded"] is False
        assert tracker.edges == []
        res = tracker.record_transformation("a", "b", "op", {"k": [1]})
        res["edge"]["metadata"]["k"].append(2)
        assert tracker.edges[0]["metadata"] == {"k": [1]}
        assert tracker.trace_path("a", max_hops=NAN) == ["a", "b"]

    def test_mermaid_names_cannot_collide_or_break_syntax(self) -> None:
        tracker = DataLineageProvenanceTracker()
        tracker.record_transformation("raw data", "raw_data", 'clean "x"')
        tracker.record_transformation("😀", "end", "op")
        out = tracker.export_to_mermaid("nonsense")
        assert out.startswith("flowchart LR")
        assert len(set(re.findall(r"n\d+\[", out))) == 4 and 'clean #quot;x#quot;' in out


class TestModelRegistry:
    def _reg(self) -> ModelRegistryAndEvaluationManager:
        mgr = ModelRegistryAndEvaluationManager()
        mgr.register_model_version("m", "v1", {"accuracy": 0.90})
        mgr.register_model_version("m", "v2", {"accuracy": 0.95})
        return mgr

    def test_deprecated_version_cannot_be_promoted_or_chosen(self) -> None:
        mgr = self._reg()
        mgr.deprecate_model("m", "v2")
        out = mgr.promote_if_eligible("m", "v2")
        assert out["promoted"] is False and "deprecated" in out["error"]
        assert mgr.get_best_model_for_task("m")["version"] == "v1"
        mgr.deprecate_model("m", "v1")
        assert mgr.get_best_model_for_task("m") is None

    def test_non_finite_metrics_never_win_or_promote(self) -> None:
        mgr = self._reg()
        mgr.register_model_version("m", "v3", {"accuracy": INF})
        mgr.register_model_version("m", "v4", {"accuracy": NAN})
        mgr.register_model_version("m", "v5", {"accuracy": "high"})
        assert mgr.get_best_model_for_task("m")["version"] == "v2"
        for v in ("v3", "v4", "v5"):
            out = mgr.promote_if_eligible("m", v)
            assert out["promoted"] is False and "finite" in out["error"]
        missing = mgr.promote_if_eligible("m", "v1", primary_metric_key="f1")
        assert missing["promoted"] is False and missing["score"] is None and "missing" in missing["error"]

    def test_eligibility_check_rejects_garbage_instead_of_passing_it(self) -> None:
        ev = ModelRegistryAndEvaluationManager.evaluate_eligibility
        for score, threshold in ((None, 0.85), (INF, 0.85), (NAN, 0.85), ("x", 0.85), (0.1, 0), (0.1, -1), (0.9, NAN)):
            out = ev("m", score, threshold=threshold)
            assert out["deployment_eligible"] is False and out["status"] == "INVALID_INPUT", (score, threshold)
        assert ev("m", 0.9)["status"] == "ELIGIBLE" and ev("m", 0.5)["status"] == "BELOW_THRESHOLD"

    def test_malformed_registration_and_comparison_do_not_raise(self) -> None:
        mgr = ModelRegistryAndEvaluationManager()
        assert mgr.register_model_version("m", "v", None)["registered"] is False
        assert mgr.register_model_version("m", "v", "x")["registered"] is False
        assert mgr.register_model_version("", "v", {})["registered"] is False
        assert mgr.register_model_version("m", None, {})["registered"] is False
        assert mgr.list_versions("m") == [] and mgr.promote_if_eligible("m", "v", threshold=None)["promoted"] is False
        cmp = ModelRegistryAndEvaluationManager.compare_models
        assert "error" in cmp(None, {})
        row = cmp({"metrics": {"a": "x", "b": NAN, "c": 1.0}}, {"metrics": {"a": 1, "b": 1, "c": 1.5}})["metric_comparisons"]
        assert row["a"]["delta"] is None and row["b"]["delta"] is None and row["c"]["delta"] == 0.5
        json.dumps({k: v["delta"] for k, v in row.items()}, allow_nan=False)  # a NaN delta is not JSON


class TestGpuResourceScheduler:
    def test_constructor_refuses_non_finite_capacity(self) -> None:
        for bad in (NAN, INF, "lots", None):
            with pytest.raises(ValueError):
                GpuResourceScheduler(bad)
        assert GpuResourceScheduler(-5).total_vram_gb == 0.0

    def test_bad_requests_are_refused_with_the_right_reason(self) -> None:
        gpu = GpuResourceScheduler(12)
        for req in (None, NAN, INF, "x"):
            out = gpu.request_allocation("j", req)
            assert out["allocated"] is False and "finite" in out["error"], req
        assert gpu.request_allocation(None, 1)["allocated"] is False
        assert gpu.request_allocation("", 1)["allocated"] is False
        assert gpu.allocations == {}

    def test_repeat_request_with_a_different_size_is_flagged(self) -> None:
        gpu = GpuResourceScheduler(12)
        gpu.request_allocation("j", 2)
        again = gpu.request_allocation("j", 8)
        assert again["already_allocated"] is True and again["request_differs"] is True
        assert again["allocated_vram_gb"] == 2 and gpu.available_vram_gb() == 10
        assert "not read from the GPU" in gpu.status_snapshot()["note"]

    def test_estimates_refuse_nonsense_instead_of_negative_gigabytes(self) -> None:
        est = GpuResourceScheduler.estimate_model_vram_requirement
        for args in ((0,), (-7,), (NAN,), (INF,), (7, 0), (7, 16, -1), (7, 16, 4096, 0), (None,)):
            out = est(*args)
            assert out["total_vram_recommended_gb"] is None and out["error"], args
        assert est("7")["weights_vram_gb"] == pytest.approx(13.04, abs=0.01)
        off = GpuResourceScheduler.suggest_offload_strategy
        for args in ((10, NAN), (NAN, 10), (10, -5), (-1, 5), (None, 5)):
            out = off(*args)
            assert out["error"] and out["fits_natively"] is False and out["recommendations"] == [], args
        assert off(0, 0)["fits_natively"] is True
        assert "~100.0%" in off(10, 0)["recommendations"][0]

    def test_queue_survives_malformed_jobs_and_reports_them(self) -> None:
        gpu = GpuResourceScheduler(10)
        out = gpu.schedule_workload_queue([
            {"job_id": "hi", "priority": 5, "requested_vram_gb": 6},
            {"job_id": "lo", "priority": 1, "requested_vram_gb": 6},
            {"job_id": "hi", "requested_vram_gb": 1},          # duplicate id
            {"job_id": "badprio", "priority": "high"},          # not a number
            {"requested_vram_gb": 2},                           # no id
            {"job_id": "nan", "requested_vram_gb": NAN},
            "not a dict",
            None,
        ])
        assert out["allocated_jobs"] == ["hi"] and out["queued_jobs"] == ["lo"]
        assert sorted(r["index"] for r in out["rejected_jobs"]) == [2, 3, 4, 5, 6, 7]
        assert gpu.schedule_workload_queue(None)["allocated_jobs"] == []
        assert list(gpu.allocations) == ["hi"]  # nothing is allocated under a bogus name


class TestWorkflowDependencyValidator:
    def test_empty_graph_has_an_empty_critical_path(self) -> None:
        out = Workflow.calculate_critical_path({})
        assert out["valid"] is True and out["critical_path"] == [] and out["critical_duration"] == 0.0

    def test_unreadable_graphs_are_never_reported_valid(self) -> None:
        for bad in (None, [], "a", 5):
            out = Workflow.detect_cycles(bad)
            assert out["workflow_valid"] is False and out["error"], bad
            assert Workflow.topological_sort(bad)["valid"] is False
            assert Workflow.get_parallel_execution_tiers(bad)["valid"] is False
            assert Workflow.calculate_critical_path(bad)["valid"] is False
        assert Workflow.detect_cycles({"a": 5.5, "b": object()})["workflow_valid"] is False

    def test_string_and_none_dependencies(self) -> None:
        # A bare string used to be iterated character by character ("test" -> t, e, s, t).
        assert Workflow.topological_sort({"build": "test", "test": None})["order"] == ["test", "build"]
        assert Workflow.detect_cycles({"a": "b", "b": "a"})["circular_dependencies_detected"] is True
        assert Workflow.detect_cycles({"a": None})["workflow_valid"] is True

    def test_mixed_key_types_are_compared_as_strings(self) -> None:
        out = Workflow.topological_sort({1: ["a"], "a": []})
        assert out["valid"] is True and out["order"] == ["a", "1"]

    def test_bad_durations_are_refused(self) -> None:
        graph = {"a": [], "b": ["a"]}
        for bad in (NAN, -5, "x", INF, True, None):
            out = Workflow.calculate_critical_path(graph, {"a": bad})
            assert out["valid"] is False and "Duration" in out["error"], bad
        ok = Workflow.calculate_critical_path(graph, {"a": 2.5})
        assert ok["critical_duration"] == 3.5 and ok["critical_path"] == ["a", "b"]
        assert Workflow.calculate_critical_path(graph, [1])["valid"] is False

    def test_cycle_is_explained_by_every_dependent_method(self) -> None:
        graph = {"a": ["b"], "b": ["a"]}
        assert Workflow.get_parallel_execution_tiers(graph)["cycles"]
        assert Workflow.calculate_critical_path(graph)["cycles"]
        assert Workflow.topological_sort({"a": ["a"]})["valid"] is False

    def test_ordering_is_linear_not_quadratic(self) -> None:
        n = 20000
        graph = {f"t{i}": [f"t{i - 1}"] if i else [] for i in range(n)}
        started = time.monotonic()
        order = Workflow.topological_sort(graph)["order"]
        assert time.monotonic() - started < 5  # was ~30 s
        assert len(order) == n and order[0] == "t0" and order[-1] == f"t{n - 1}"
        wide = {"root": [], **{f"leaf{i}": ["root"] for i in range(5)}}
        tiers = Workflow.get_parallel_execution_tiers(wide)
        assert tiers["tiers"] == [["root"], [f"leaf{i}" for i in range(5)]] and tiers["max_concurrency"] == 5

    def test_workflow_spec_garbage_returns_errors(self) -> None:
        for spec in (None, [], "x", {"tasks": "abc"}, {"tasks": ["x"]}, {"tasks": [{"id": ["a"]}]},
                     {"tasks": [{"id": "a", "dependencies": {"x": 1}}]}):
            out = Workflow.validate_workflow_spec(spec)
            assert out["valid"] is False and out["errors"], spec
        ok = Workflow.validate_workflow_spec({"tasks": [{"id": "a", "dependencies": "b"}, {"id": "b"}]})
        assert ok["valid"] is True and ok["parallel_tiers"] == [["b"], ["a"]]
        assert ok["deadlines_checked"] is False
        assert Workflow.validate_workflow_spec({"tasks": [{"id": 7}, {"id": "7"}]})["valid"] is False  # duplicate
        cyc = Workflow.validate_workflow_spec({"tasks": [{"id": "a", "dependencies": ["a"]}]})
        assert cyc["valid"] is False and "Cyclic" in cyc["errors"][0]

