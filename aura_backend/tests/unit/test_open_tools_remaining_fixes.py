"""Regression tests for the defects left after the first ``tools/open`` review pass.

Most of these modules are classified ``not_exposed`` / ``deferred`` (ADR-036): reference code that
no tool reaches. The file headers say "fix before any exposure", so each defect here is pinned
before anyone wires the module in.
"""

from __future__ import annotations

import http.server
import inspect
import json
import threading

import pytest

from aura_core.tools.open._net import http_url_error, public_http_url_error
from aura_core.tools.open.artifact_feedback_ingestor import ArtifactFeedbackIngestor
from aura_core.tools.open.autonomous_research_and_synthesis_tool import (
    AutonomousResearchAndSynthesisTool,
)
from aura_core.tools.open.browser_subagent import BrowserSubagent
from aura_core.tools.open.deployment_release_manager import DeploymentReleaseManager
from aura_core.tools.open.dev_server_manager import DevServerManager
from aura_core.tools.open.notebook_reader import NotebookReader
from aura_core.tools.open.push_notification_dispatcher import PushNotificationDispatcher
from aura_core.tools.open.quality_gate_aggregator import QualityGateAggregator
from aura_core.tools.open.regression_impact_analyzer import RegressionImpactAnalyzer
from aura_core.tools.open.scope_and_diff_guard import ScopeAndDiffGuard
from aura_core.tools.open.skills_rules_workflows_loader import SkillsRulesWorkflowsLoader


class TestAggregateAllChecksFailsClosed:
    def test_no_results_at_all_is_not_a_pass(self) -> None:
        gate = QualityGateAggregator.aggregate_all_checks()
        assert gate["ready_to_merge"] is False
        assert {"security", "tests", "coverage"} <= set(gate["failed_checks"])

    def test_a_missing_security_result_blocks_even_when_the_rest_pass(self) -> None:
        gate = QualityGateAggregator.aggregate_all_checks(
            test_result={"passed": 10, "failed": 0}, cov_result={"coverage_ratio": 0.9}
        )
        assert gate["ready_to_merge"] is False
        assert "security" in gate["blocker_failures"]

    def test_all_supplied_and_passing_is_still_ready(self) -> None:
        gate = QualityGateAggregator.aggregate_all_checks(
            test_result={"passed": 10, "failed": 0},
            sec_result={"clean": True, "findings": []},
            cov_result={"coverage_ratio": 0.85},
            arch_result={"is_consistent": True, "violations": []},
        )
        assert gate["ready_to_merge"] is True

    def test_checks_this_entry_point_does_not_run_are_not_claimed_as_passed(self) -> None:
        gate = QualityGateAggregator.aggregate_all_checks(
            test_result={"passed": 1, "failed": 0},
            sec_result={"findings": []},
            cov_result={"coverage_ratio": 0.9},
        )
        assert "complexity" in gate["not_run"] and "lint" in gate["not_run"]

    def test_a_failing_supplied_result_still_blocks(self) -> None:
        gate = QualityGateAggregator.aggregate_all_checks(
            test_result={"passed": 9, "failed": 1},
            sec_result={"findings": []},
            cov_result={"coverage_ratio": 0.9},
        )
        assert gate["ready_to_merge"] is False


class TestResearchClaimExtraction:
    @pytest.mark.parametrize(
        "sentence",
        [
            "According to the study, 80% of users prefer X.",
            "Latency fell by 40% after the change.",
            "The new path is 2x faster than the old one.",
            "p99 dropped to 35ms under load.",
        ],
    )
    def test_a_percentage_or_unit_followed_by_a_space_is_a_quantified_claim(self, sentence) -> None:
        found = AutonomousResearchAndSynthesisTool.extract_claims_and_evidence(sentence)
        assert len(found) == 1 and found[0]["contains_quantifier"] is True

    def test_prose_without_numbers_or_evidence_words_is_ignored(self) -> None:
        assert AutonomousResearchAndSynthesisTool.extract_claims_and_evidence(
            "The team met on Tuesday. It was pleasant."
        ) == []

    def test_a_bare_number_that_is_not_a_measurement_is_not_a_claim(self) -> None:
        assert AutonomousResearchAndSynthesisTool.extract_claims_and_evidence(
            "There were 3 chairs in the room."
        ) == []


class TestReverseDependentsMatchWholeModuleNames:
    def test_a_module_whose_name_merely_contains_the_target_is_not_a_dependent(self, tmp_path) -> None:
        (tmp_path / "util.py").write_text("def f(): pass\n")
        (tmp_path / "app.py").write_text("import util\n")
        (tmp_path / "other.py").write_text("import utility_extra\nfrom pkg.my_util_helper import z\n")
        (tmp_path / "rel.py").write_text("from pkg import util\nfrom .util import f\n")

        result = RegressionImpactAnalyzer.find_reverse_dependents(
            str(tmp_path), RegressionImpactAnalyzer._module_fragments("util.py")
        )

        assert sorted(result["dependents"]) == ["app.py", "rel.py"]

    def test_a_dotted_target_matches_its_package_path(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text("from aura_core.llm import router\n")
        (tmp_path / "b.py").write_text("import aura_core.llm.router as r\n")
        (tmp_path / "c.py").write_text("import aura_core.llm.router_extras\n")

        result = RegressionImpactAnalyzer.find_reverse_dependents(
            str(tmp_path), RegressionImpactAnalyzer._module_fragments("aura_core/llm/router.py")
        )

        assert sorted(result["dependents"]) == ["a.py", "b.py"]


class TestScopeGuardPaths:
    def test_a_root_env_file_is_forbidden(self) -> None:
        result = ScopeAndDiffGuard.check_scope_compliance([".env"], ["src/*"])
        assert result["forbidden_files"] == [".env"]
        assert result["compliant"] is False

    def test_dotfiles_keep_their_leading_dot(self) -> None:
        result = ScopeAndDiffGuard.check_scope_compliance(["./.github/workflows/ci.yml"], [".github/*"])
        assert result["compliant"] is True

    def test_check_diff_scope_does_not_eat_a_leading_dot(self) -> None:
        diff = "--- a/.github/ci.yml\n+++ b/.github/ci.yml\n@@ -1 +1 @@\n-a\n+b\n"
        assert ScopeAndDiffGuard.check_diff(diff, allowed_files=[".github/"])["within_scope"] is True
        assert ScopeAndDiffGuard.check_diff(diff, allowed_files=["github/"])["within_scope"] is False

    @pytest.mark.parametrize("path", ["b/bin/run.sh", "blog/post.md", "src/b/x.py"])
    def test_a_path_starting_with_b_is_not_mangled(self, path) -> None:
        diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
        assert ScopeAndDiffGuard.parse_diff_files(diff)[0]["path"] == path

    def test_a_plain_unified_diff_without_a_git_header_is_still_parsed(self) -> None:
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n-a\n+b\n+c\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+b\n"
        files = ScopeAndDiffGuard.parse_diff_files(diff)
        assert [(f["path"], f["lines_added"], f["lines_deleted"]) for f in files] == [
            ("x.py", 2, 1),
            ("y.py", 1, 1),
        ]
        budget = ScopeAndDiffGuard.check_diff_size_budget(diff, max_files=1)
        assert budget["total_files"] == 2 and budget["within_budget"] is False


LONG_NOTEBOOK = json.dumps(
    {
        "cells": [
            {"cell_type": "markdown", "source": ["# T"], "metadata": {}},
            {
                "cell_type": "code",
                "source": ["x = '" + "y" * 400 + "'"],
                "outputs": [{"output_type": "stream", "name": "stdout", "text": ["1\n"]}],
                "execution_count": 3,
                "metadata": {},
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
)


class TestNotebookReaderInlineJson:
    def test_inline_json_longer_than_a_file_name_is_parsed(self) -> None:
        assert len(LONG_NOTEBOOK) > 255
        result = NotebookReader.parse_notebook(LONG_NOTEBOOK)
        assert result["valid"] is True and result["code_cells_count"] == 1

    def test_every_method_accepts_long_inline_json(self) -> None:
        assert "x = " in NotebookReader.extract_runnable_python(LONG_NOTEBOOK)
        assert "# T" in NotebookReader.extract_markdown_document(LONG_NOTEBOOK)
        assert NotebookReader.extract_cell_outputs(LONG_NOTEBOOK)[0]["text"] == "1"
        stripped = json.loads(NotebookReader.strip_notebook_outputs(LONG_NOTEBOOK))
        assert stripped["cells"][1]["outputs"] == [] and stripped["cells"][1]["execution_count"] is None

    def test_a_real_file_path_still_works(self, tmp_path) -> None:
        path = tmp_path / "n.ipynb"
        path.write_text(LONG_NOTEBOOK)
        assert NotebookReader.parse_notebook(str(path))["valid"] is True


class TestSkillsLoaderFindsThisRepositorysLayout:
    def test_agents_and_claude_skill_folders_are_searched(self, tmp_path) -> None:
        for base in (".agents/skills/deploy", ".claude/skills/review"):
            (tmp_path / base).mkdir(parents=True)
            (tmp_path / base / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody\n")

        result = SkillsRulesWorkflowsLoader.load_resources(str(tmp_path))

        assert sorted(item["name"] for item in result["all_skills"]) == ["deploy", "review"]

    def test_a_skill_file_is_named_after_its_folder_not_after_SKILL(self, tmp_path) -> None:
        (tmp_path / "skills" / "lint").mkdir(parents=True)
        (tmp_path / "skills" / "lint" / "SKILL.md").write_text("body")
        (tmp_path / "skills" / "plain.md").write_text("body")

        names = {i["name"] for i in SkillsRulesWorkflowsLoader.load_resources(str(tmp_path))["all_skills"]}

        assert names == {"lint", "plain"}


class TestFeedbackCanRemoveByStepNumber:
    PLAN = ["Write the parser", "Add tests", "Update the docs"]

    def test_remove_step_n_removes_that_step(self) -> None:
        result = ArtifactFeedbackIngestor.apply_feedback(self.PLAN, "remove step 2")
        assert result["revised_plan"] == ["Write the parser", "Update the docs"]
        assert any("Add tests" in a for a in result["actions"])

    def test_a_step_number_out_of_range_changes_nothing_and_says_so(self) -> None:
        result = ArtifactFeedbackIngestor.apply_feedback(self.PLAN, "delete step 9")
        assert result["revised_plan"] == self.PLAN
        assert any("no step 9" in a.lower() for a in result["actions"])

    def test_keyword_removal_still_works(self) -> None:
        result = ArtifactFeedbackIngestor.apply_feedback(self.PLAN, "remove the docs")
        assert result["revised_plan"] == ["Write the parser", "Add tests"]


class TestUrlGuards:
    @pytest.mark.parametrize("url", ["file:///etc/hostname", "ftp://example.com/x", "javascript:alert(1)", "", "http://"])
    def test_only_http_and_https_with_a_host_are_accepted(self, url) -> None:
        assert http_url_error(url)

    @pytest.mark.parametrize("url", ["http://localhost:3000/", "https://example.com/a?b=1"])
    def test_ordinary_http_urls_pass_the_scheme_check(self, url) -> None:
        assert http_url_error(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://localhost/",
            "http://10.1.2.3/",
            "http://192.168.0.1/",
            "http://172.16.5.5/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://0.0.0.0/",
        ],
    )
    def test_private_loopback_and_metadata_addresses_are_refused(self, url) -> None:
        assert "not a public address" in (public_http_url_error(url) or "")

    def test_a_name_that_resolves_to_a_private_address_is_refused(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.7", 0))]
        )
        assert "not a public address" in (public_http_url_error("http://internal.example/") or "")

    def test_a_name_that_resolves_to_a_public_address_is_accepted(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
        )
        assert public_http_url_error("https://hooks.example.com/x") is None

    def test_a_name_that_does_not_resolve_is_refused(self, monkeypatch) -> None:
        import socket

        def boom(*a, **k):
            raise socket.gaierror("nope")

        monkeypatch.setattr("socket.getaddrinfo", boom)
        assert "could not resolve" in (public_http_url_error("http://nowhere.invalid/") or "")


class TestWebhookDispatch:
    def test_a_file_url_is_refused_cleanly(self) -> None:
        result = PushNotificationDispatcher.dispatch_to_webhook("file:///etc/hostname", {"a": 1})
        assert result["success"] is False and "scheme" in result["error"]

    @pytest.mark.parametrize("url", ["http://127.0.0.1:9/h", "http://169.254.169.254/", "http://[::1]/"])
    def test_a_private_target_is_refused_without_connecting(self, url, monkeypatch) -> None:
        def no_network(*a, **k):
            raise AssertionError("a request was attempted")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", no_network)
        result = PushNotificationDispatcher.dispatch_to_webhook(url, {"a": 1})
        assert result["success"] is False and "not a public address" in result["error"]

    def test_a_redirect_is_not_followed(self, monkeypatch) -> None:
        # The outbound policy refuses loopback with no switch to relax it, so the check is
        # replaced here only to reach a local server; the no-redirect behaviour is the real one.
        monkeypatch.setattr("aura_core.tools.open._ops_common.check_url", lambda url: url)
        hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                hits.append(self.path)
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/followed")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/start"
            result = PushNotificationDispatcher.dispatch_to_webhook(url, {"a": 1})
        finally:
            server.shutdown()

        assert hits == ["/start"]
        assert result["success"] is False and result["status_code"] == 302

    def test_there_is_no_switch_to_reach_private_targets(self) -> None:
        parameters = inspect.signature(PushNotificationDispatcher.dispatch_to_webhook).parameters
        assert set(parameters) == {"url", "payload", "headers", "timeout_seconds"}

    def test_a_2xx_reply_is_success(self, monkeypatch) -> None:
        monkeypatch.setattr("aura_core.tools.open._ops_common.check_url", lambda url: url)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/"
            result = PushNotificationDispatcher.dispatch_to_webhook(url, {"a": 1})
        finally:
            server.shutdown()
        assert result["success"] is True and result["status_code"] == 204


class TestProbesRefuseNonHttpSchemes:
    def test_browser_accessibility_probe(self) -> None:
        result = BrowserSubagent.check_url_accessibility("file:///etc/hostname")
        assert result["accessible"] is False and "scheme" in result["error"]

    def test_deployment_readiness_probe(self) -> None:
        result = DeploymentReleaseManager.check_readiness_probes(["file:///etc/hostname"])
        assert result["all_healthy"] is False
        assert "scheme" in result["probe_results"][0]["error"]

    def test_dev_server_health_probe(self) -> None:
        result = DevServerManager.check_http_health("file:///etc/hostname")
        assert result["healthy"] is False and "scheme" in result["error"]
