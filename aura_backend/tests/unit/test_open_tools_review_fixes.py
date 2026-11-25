"""Regression tests for the code-review findings on ``aura_core/tools/open``.

Each class pins one defect that was reproduced before it was fixed.
"""

from __future__ import annotations

import json

import pytest

from aura_core.tools.open.api_contract_validator import ApiContractValidator
from aura_core.tools.open.database_intelligence_tool import DatabaseIntelligenceTool
from aura_core.tools.open.observability_incident_analyzer import ObservabilityIncidentAnalyzer
from aura_core.tools.open.security_vulnerability_scanner import SecurityVulnerabilityScanner
from aura_core.tools.open.test_coverage_and_mutation_analyzer import TestCoverageAndMutationAnalyzer


class TestScannerLineIndexing:
    def test_a_form_feed_cannot_hide_an_eval_behind_a_comment(self):
        result = SecurityVulnerabilityScanner.scan_source_code("#\x0c#\neval(x)\n")
        assert [f["rule_id"] for f in result["findings"]] == ["SEC-EVAL-001"]
        assert result["findings"][0]["line"] == 2
        assert result["findings"][0]["snippet"] == "eval(x)"

    def test_plain_source_is_unchanged(self):
        assert SecurityVulnerabilityScanner.scan_source_code("eval(x)\n")["findings_count"] == 1

    def test_a_commented_out_eval_is_still_ignored(self):
        assert SecurityVulnerabilityScanner.scan_source_code("# eval(x)\nx = 1\n")["findings_count"] == 0

    def test_diff_line_numbers_ignore_the_no_newline_marker(self):
        diff = (
            "+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old\n"
            "\\ No newline at end of file\n"
            "+eval(x)\n"
        )
        findings = SecurityVulnerabilityScanner.scan_git_diff(diff)["findings"]
        assert findings[0]["line"] == 1


class TestSqlLint:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM users /* WHERE id = 1 */",
            "DELETE FROM users -- WHERE id = 1",
            "UPDATE t SET note = 'where'",
            "SELECT 1; DELETE FROM t",
        ],
    )
    def test_a_where_in_a_comment_or_string_is_not_a_filter(self, sql):
        assert DatabaseIntelligenceTool.inspect_sql(sql)["unbounded_write"] is True

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM users\nWHERE id = 1",
            "UPDATE t SET a = 1 WHERE id = 2",
            "DELETE FROM a WHERE x = 1; DELETE FROM b WHERE y = 2",
            "SELECT * FROM t",
        ],
    )
    def test_a_real_where_on_any_line_bounds_the_write(self, sql):
        assert DatabaseIntelligenceTool.inspect_sql(sql)["unbounded_write"] is False

    def test_drop_inside_a_string_is_not_destructive(self):
        assert DatabaseIntelligenceTool.inspect_sql("SELECT 'drop table x'")["is_destructive"] is False
        assert DatabaseIntelligenceTool.inspect_sql("drop table x")["is_destructive"] is True


class TestTracebackAttribution:
    TRACEBACK = [
        "2026-01-01 ERROR request failed",
        "Traceback (most recent call last):",
        '  File "a.py", line 1, in f',
        "    raise KeyError('x')",
        "KeyError: 'x'",
        "INFO recovered",
    ]

    def test_the_exception_type_on_the_last_line_is_counted(self):
        parsed = ObservabilityIncidentAnalyzer.parse_error_logs(self.TRACEBACK)
        assert parsed["top_exceptions"] == [("KeyError", 1)]
        assert parsed["incidents_count"] == 2
        assert parsed["incidents"][1]["exceptions"] == ["KeyError"]

    def test_root_cause_names_the_exception(self):
        assert ObservabilityIncidentAnalyzer.identify_root_cause(self.TRACEBACK)["most_common_exception"] == "KeyError"

    def test_an_unindented_error_line_after_a_traceback_is_still_an_incident(self):
        logs = ["Traceback (most recent call last):", "ValueError: bad", "ERROR next thing"]
        parsed = ObservabilityIncidentAnalyzer.parse_error_logs(logs)
        assert parsed["top_exceptions"] == [("ValueError", 1)]
        assert parsed["incidents_count"] == 2


class TestApiContract:
    def test_bool_is_not_an_integer_or_number(self):
        for label in ("integer", "number", "int", "float"):
            result = ApiContractValidator.validate_payload_against_schema({"n": True}, ["n"], {"n": label})
            assert result["valid"] is False, label

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_non_finite_is_not_a_number(self, bad):
        result = ApiContractValidator.validate_payload_against_schema({"n": bad}, ["n"], {"n": "number"})
        assert result["valid"] is False

    def test_real_numbers_and_booleans_still_validate(self):
        result = ApiContractValidator.validate_payload_against_schema(
            {"i": 3, "f": 2.5, "b": True}, ["i"], {"i": "integer", "f": "number", "b": "boolean"}
        )
        assert result["valid"] is True

    def test_removing_a_method_from_a_surviving_path_is_breaking(self):
        old = json.dumps({"paths": {"/a": {"get": {}, "post": {}}}})
        new = json.dumps({"paths": {"/a": {"get": {}}}})
        result = ApiContractValidator.compare_route_sets(old, new)
        assert result["compatible"] is False
        assert result["removed_operations"] == ["POST /a"]
        assert result["removed_paths"] == []

    def test_adding_a_method_is_compatible(self):
        old = json.dumps({"paths": {"/a": {"get": {}}}})
        new = json.dumps({"paths": {"/a": {"get": {}, "post": {}}, "/b": {"get": {}}}})
        result = ApiContractValidator.compare_route_sets(old, new)
        assert result["compatible"] is True
        assert result["added_paths"] == ["/b"]


class TestMutationOffsets:
    def test_non_ascii_text_before_the_constant_does_not_drop_the_mutant(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "def f():\n    msg = 'éé'; ok = True\n    return ok\n", encoding="utf-8"
        )
        (tmp_path / "test_mod.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f() is True\n", encoding="utf-8"
        )
        result = TestCoverageAndMutationAnalyzer.evaluate_mutations_against_tests("mod.py", str(tmp_path))
        assert result["evaluated"] is True
        assert result["mutants_total"] == 1
        assert result["mutants_killed"] == 1
        assert result["mutants_skipped"] == 0

    def test_a_form_feed_does_not_shift_the_target_line(self, tmp_path):
        (tmp_path / "mod.py").write_text("X = 1\n\x0c\ndef f():\n    return True\n", encoding="utf-8")
        (tmp_path / "test_mod.py").write_text(
            "from mod import f\n\n\ndef test_f():\n    assert f() is True\n", encoding="utf-8"
        )
        result = TestCoverageAndMutationAnalyzer.evaluate_mutations_against_tests("mod.py", str(tmp_path))
        assert result["mutants_total"] == 1
        assert result["mutants_killed"] == 1
