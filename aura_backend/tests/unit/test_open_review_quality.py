"""Review of the ten quality / analysis / data tools under ``aura_core/tools/open``.

Every test here pins a defect that was found by calling the tool with real input and watching it
misbehave: a false all-clear (an empty or unreadable input reported as safe/compliant/healthy),
a wrong result, an escaping exception, or a regex that could be made to stall. No mocks; the PDF
tests build real PDFs and read them with the platform's own extractor.

None of the ten classes opens a file: they take text (or already-parsed dicts). The PDF tests prove
the intended path -- read the *whole* PDF through ``tools/open/_input`` and hand its text to the tool
-- by putting the finding on the LAST page of a 45-page document.
"""

from __future__ import annotations

import json
import math
import time

import pytest

from aura_core.tools.open._input import read_source_bytes
from aura_core.tools.open.api_contract_validator import ApiContractValidator as Api
from aura_core.tools.open.architectural_consistency_checker import ArchitecturalConsistencyChecker as Arch
from aura_core.tools.open.complexity_and_duplication_analyzer import ComplexityAndDuplicationAnalyzer as Cx
from aura_core.tools.open.data_quality_and_validation_engine import DataQualityAndValidationEngine as Dq
from aura_core.tools.open.database_intelligence_tool import DatabaseIntelligenceTool as Db
from aura_core.tools.open.dependency_license_compliance_scanner import DependencyLicenseComplianceScanner as Lic
from aura_core.tools.open.model_drift_and_performance_monitor import ModelDriftAndPerformanceMonitor as Drift
from aura_core.tools.open.observability_incident_analyzer import ObservabilityIncidentAnalyzer as Obs
from aura_core.tools.open.peer_code_reviewer_agent import PeerCodeReviewerAgent as Peer
from aura_core.tools.open.quality_gate_aggregator import QualityGateAggregator as Gate
from tests.unit.test_document_extraction import _pdf


# ======================================================================================
# database_intelligence_tool
# ======================================================================================


class TestSqlInspection:
    @pytest.mark.parametrize("query", ["", "   \n", None, 42])
    def test_nothing_to_inspect_is_not_safe(self, query) -> None:
        result = Db.inspect_sql(query)
        assert result["is_safe_sql"] is False
        assert result["inspected"] is False

    @pytest.mark.parametrize(
        "query",
        [
            "TRUNCATE users",  # no TABLE keyword
            "truncate table users",
            "DROP SCHEMA app",
            "ALTER TABLE users DROP COLUMN email",
            "SELECT 1; DROP TABLE users",
        ],
    )
    def test_destructive_statements_are_caught(self, query) -> None:
        assert Db.inspect_sql(query)["is_destructive"] is True

    @pytest.mark.parametrize(
        "query",
        [
            'UPDATE "my table" SET a = 1',  # quoted name with a space
            "UPDATE ONLY users SET a = 1",
            "DELETE u FROM users u JOIN banned b ON b.id = u.id",  # MySQL multi-table form
            "UPDATE users SET a = (SELECT b FROM t WHERE t.id = 1)",  # WHERE of the sub-query only
            "DELETE FROM users WHERE 1=1",
            "DELETE FROM users WHERE",
            "DELETE FROM users /* where id = 1",  # unterminated comment
            "delete\nfrom users\n-- where id = 1",
        ],
    )
    def test_unbounded_writes_are_caught(self, query) -> None:
        result = Db.inspect_sql(query)
        assert result["unbounded_write"] is True
        assert result["is_safe_sql"] is False

    @pytest.mark.parametrize(
        "query",
        [
            "DELETE FROM users WHERE id IN (SELECT id FROM banned)",
            "update users set a = 1 where id = 7",
            "ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES u(id) ON DELETE CASCADE",
            "GRANT DELETE ON users TO app",
        ],
    )
    def test_bounded_or_non_write_statements_are_not_flagged(self, query) -> None:
        assert Db.inspect_sql(query)["unbounded_write"] is False

    def test_arabic_table_names_are_found(self) -> None:
        result = Db.inspect_sql("SELECT * FROM مستخدمين WHERE اسم = 'علي'")
        assert result["referenced_tables"] == ["مستخدمين"]

    def test_table_extraction_handles_quoting_if_not_exists_and_comma_lists(self) -> None:
        sql = 'CREATE TABLE IF NOT EXISTS users(id int); SELECT * FROM `orders` JOIN "items" i ON 1=1; SELECT 1 FROM a x, b y'
        names = Db.extract_table_names(sql)
        assert "IF" not in names
        assert {"users", "orders", "items", "a", "b"} <= set(names)

    def test_index_hints_cover_every_predicate_not_only_the_first(self) -> None:
        hints = Db.suggest_index_hints("SELECT * FROM t WHERE a = 1 AND b = 2")
        assert len(hints) == 2


class TestSqlSyntax:
    def test_a_script_is_checked_statement_by_statement(self) -> None:
        assert Db.validate_sql_syntax("SELECT 1; SELECT 2")["valid"] is True
        bad = Db.validate_sql_syntax("SELECT 1; SELEC 2;")
        assert bad["valid"] is False and bad["statement_index"] == 2

    def test_empty_sql_is_invalid(self) -> None:
        assert Db.validate_sql_syntax("-- only a comment")["valid"] is False

    def test_other_dialects_are_not_claimed_valid(self) -> None:
        result = Db.validate_sql_syntax("SELECT FROM WHERE garbage", dialect="postgres")
        assert result["valid"] is not True
        assert result["syntax_verified"] is False


class TestInjectionDetection:
    @pytest.mark.parametrize("hostile", ["f'" * 20000, "f'WHERE " * 8000, "'" + "AND " * 30000, "{" * 50000])
    def test_hostile_lines_cannot_stall_the_scan(self, hostile) -> None:
        started = time.monotonic()
        Db.detect_injection_risks(hostile)
        assert time.monotonic() - started < 2.0

    def test_real_injection_shapes_are_flagged(self) -> None:
        for line in (
            'q = f"SELECT * FROM u WHERE id={user_id}"',
            'cur.execute("SELECT * FROM t WHERE id = %s" % uid)',
            'q = "SELECT * FROM t WHERE id=" + uid',
        ):
            assert Db.detect_injection_risks(line), line

    def test_parameterised_queries_and_plain_code_are_not_flagged(self) -> None:
        for line in (
            'cur.execute("SELECT * FROM t WHERE id = %s", (uid,))',
            "total = a + b + c",
            'msg = "hello {}".format(name)',
            'print("for %s" % x)',
        ):
            assert Db.detect_injection_risks(line) == [], line


class TestSchemaDiff:
    def test_if_not_exists_no_longer_hides_every_change(self) -> None:
        current = "CREATE TABLE IF NOT EXISTS a(x int);"
        target = "CREATE TABLE IF NOT EXISTS a(x int); CREATE TABLE IF NOT EXISTS b(y int);"
        diff = Db.generate_migration_diff(current, target)
        assert diff["added_tables"] == ["b"]
        assert diff["requires_migration"] is True

    def test_a_changed_column_is_noticed(self) -> None:
        diff = Db.generate_migration_diff("CREATE TABLE a(x int);", "CREATE TABLE a(x int, y text);")
        assert diff["changed_tables"] == ["a"]
        assert diff["requires_migration"] is True

    def test_identical_schemas_need_no_migration_and_nothing_compared_is_not_a_yes(self) -> None:
        same = "CREATE TABLE a(x int);"
        assert Db.generate_migration_diff(same, same)["requires_migration"] is False
        empty = Db.generate_migration_diff("", "")
        assert empty["requires_migration"] is None and empty["compared"] is False

    def test_added_tables_are_ordered(self) -> None:
        diff = Db.generate_migration_diff("", "CREATE TABLE z(a); CREATE TABLE b(a); CREATE TABLE m(a);")
        assert diff["added_tables"] == ["b", "m", "z"]

    def test_index_recommendations_keep_their_shape(self) -> None:
        recs = Db.recommend_indexes(["SELECT * FROM t WHERE a = 1 AND b = 2", "select 1 where A = 1"])
        assert [r["column"] for r in recs] == ["a", "b"]
        assert recs[0]["frequency"] == "2"
        assert recs[0]["suggested_sql"].startswith("CREATE INDEX")


# ======================================================================================
# api_contract_validator
# ======================================================================================


class TestApiContracts:
    def test_a_spec_without_paths_is_not_reported_as_having_nothing_deprecated(self) -> None:
        result = Api.check_deprecated_endpoints("{}")
        assert result["valid"] is False

    def test_deprecated_operations_are_still_listed_and_counted(self) -> None:
        spec = json.dumps({"paths": {"/a": {"get": {"deprecated": True}, "parameters": []}, "/b": {"post": {}}}})
        result = Api.check_deprecated_endpoints(spec)
        assert result["deprecated"] == [{"path": "/a", "method": "GET"}]
        assert result["endpoints_checked"] == 2

    def test_a_diff_with_nothing_to_compare_is_not_compatible(self) -> None:
        assert Api.compare_route_sets("{}", "{}")["compatible"] is False
        assert Api.compare_route_sets('{"paths": 5}', '{"paths": {}}')["compatible"] is False  # used to raise
        assert "old_spec" in Api.compare_route_sets("nope", "{}")["error"]

    def test_a_new_required_parameter_breaks_compatibility(self) -> None:
        old = json.dumps({"paths": {"/u": {"get": {"parameters": []}}}})
        new = json.dumps({"paths": {"/u": {"get": {"parameters": [{"name": "k", "in": "query", "required": True}]}}}})
        result = Api.compare_route_sets(old, new)
        assert result["compatible"] is False
        assert result["breaking_changes"] == ["Added required parameter 'k' to 'GET /u'"]

    def test_unchanged_specs_are_compatible(self) -> None:
        spec = json.dumps({"paths": {"/u": {"get": {}}}})
        assert Api.compare_route_sets(spec, spec)["compatible"] is True

    @pytest.mark.parametrize(
        "old,new",
        [
            ({"paths": {"/a": {"get": {"parameters": [1, "x", {"required": True}]}, "parameters": 5}}}, {"paths": {"/a": {"get": {}}}}),
            ({"paths": {"/a": [1]}}, {"paths": {"/a": None}}),
            ({"paths": {"/a": {"get": None}}}, {"paths": {"/a": {}}}),
        ],
    )
    def test_malformed_but_parseable_specs_do_not_raise(self, old, new) -> None:
        assert "is_breaking" in Api.detect_breaking_changes(old, new)

    def test_uncomparable_specs_fail_closed(self) -> None:
        result = Api.detect_breaking_changes(None, {})
        assert result["is_breaking"] is True and result["status"] == "invalid_input"

    def test_path_level_parameters_count(self) -> None:
        old = {"paths": {"/a": {"get": {}}}}
        new = {"paths": {"/a": {"parameters": [{"name": "tenant", "in": "header", "required": True}], "get": {}}}}
        assert Api.detect_breaking_changes(old, new)["is_breaking"] is True

    def test_payload_with_an_unknown_type_label_does_not_pass(self) -> None:
        result = Api.validate_payload_against_schema({"d": "2026-01-01"}, [], {"d": "date"})
        assert result["valid"] is False and result["unknown_types"] == [{"field": "d", "type": "date"}]

    def test_nothing_to_validate_is_not_valid(self) -> None:
        assert Api.validate_payload_against_schema({"a": 1}, [], None)["valid"] is False
        assert Api.validate_payload_against_schema([1], ["a"], None)["valid"] is False

    def test_bool_and_nan_are_not_numbers(self) -> None:
        result = Api.validate_payload_against_schema(
            {"a": True, "b": math.nan}, [], {"a": "integer", "b": "number"}
        )
        assert [m["field"] for m in result["type_mismatches"]] == ["a", "b"]

    def test_yaml_and_bom_and_deep_nesting(self) -> None:
        assert "YAML" in Api.parse_spec("openapi: 3.0.0\npaths: {}")["error"]
        assert Api.parse_spec('\ufeff{"paths": {}}')["valid"] is True
        assert Api.parse_spec("[" * 100000)["valid"] is False  # RecursionError used to escape

    def test_a_changelog_never_claims_compatibility_without_a_comparison(self) -> None:
        assert "UNKNOWN" in Api.generate_api_diff_changelog({})
        assert "BACKWARDS COMPATIBLE" not in Api.generate_api_diff_changelog(Api.detect_breaking_changes(None, None))


# ======================================================================================
# architectural_consistency_checker
# ======================================================================================


class TestArchitecture:
    def test_layers_match_whole_segments_not_substrings(self) -> None:
        # "rapid" contains "api" and "building" contains "ui": neither is a layer.
        report = Arch.check_module_imports("aura_core.domain.thing", ["rapid.client", "building.plans"])
        assert report["is_consistent"] is True

    def test_a_package_name_containing_core_is_not_the_core_layer(self) -> None:
        graph = {"aura_core.domain.x": ["aura_core.api.y"]}
        (violation,) = Arch.detect_layering_violations(graph)
        assert violation["source_tier"] == 0 and violation["target_tier"] == 4

    def test_an_empty_audit_is_not_consistent(self) -> None:
        result = Arch.audit_project_modules({})
        assert result["is_consistent"] is False and result["modules_checked"] == 0

    def test_unclassified_modules_are_reported_as_unchecked(self) -> None:
        result = Arch.audit_project_modules({"misc.thing": ["x"]})
        assert result["unclassified_modules"] == ["misc.thing"]

    def test_deep_import_chains_do_not_exhaust_the_stack(self) -> None:
        graph = {str(i): [str(i + 1)] for i in range(5000)}
        graph["4999"] = ["0"]
        (cycle,) = Arch.detect_circular_package_dependencies(graph)
        assert cycle[0] == cycle[-1] and len(cycle) == 5001

    def test_cycles_including_self_imports(self) -> None:
        cycles = Arch.detect_circular_package_dependencies({"A": ["B"], "B": ["A"], "C": ["C"]})
        assert ["A", "B", "A"] in cycles and ["C", "C"] in cycles


# ======================================================================================
# complexity_and_duplication_analyzer
# ======================================================================================


class TestComplexity:
    def test_back_to_back_copies_are_duplicates(self) -> None:
        block = "\n".join(f"x{i} = {i}" for i in range(30))
        result = Cx.detect_duplicate_blocks(block + "\n" + block)
        assert result["has_duplication"] is True
        assert result["duplicates"][0]["occurrences_count"] == 2

    def test_three_copies_are_one_group(self) -> None:
        block = "\n".join(f"x{i} = {i}" for i in range(10))
        result = Cx.detect_duplicate_blocks("\n".join([block] * 3))
        assert result["duplicates_count"] == 1 and result["duplicates"][0]["occurrences_count"] == 3

    def test_a_file_of_repeated_lines_is_fast_and_bounded(self) -> None:
        started = time.monotonic()
        result = Cx.detect_duplicate_blocks("a = 1\n" * 20000)
        assert time.monotonic() - started < 5.0
        assert result["has_duplication"] is True
        assert len(result["duplicates"][0]["spans"]) <= Cx.MAX_SPANS_PER_GROUP

    @pytest.mark.parametrize("window", [0, -3, 1, True, "5", None])
    def test_a_bad_window_is_an_error_not_an_exception(self, window) -> None:
        result = Cx.detect_duplicate_blocks("a = 1\nb = 2\n" * 5, window_size=window)
        assert "error" in result and result["has_duplication"] is False

    def test_a_bad_window_does_not_make_the_code_maintainable(self) -> None:
        result = Cx.analyze_source("x = 1\n", dup_window=0)
        assert result["maintainable"] is False and "duplication check not run" in result["error"]

    def test_nan_thresholds_are_rejected_instead_of_flagging_nothing(self) -> None:
        result = Cx.analyze_source("def f(a):\n    if a:\n        return 1\n", max_complexity=math.nan)
        assert result["valid_syntax"] is False and result["status"] == "invalid_input"

    @pytest.mark.parametrize("source", [None, b"x = 1", 5, "x = (\x00)", "def f(:\n"])
    def test_hostile_source_returns_an_error_dict(self, source) -> None:
        assert Cx.analyze_source(source)["valid_syntax"] is False

    def test_arabic_identifiers_and_async_code_are_analysed(self) -> None:
        source = "async def مرحبا(a):\n    async for x in a:\n        if x and a:\n            return 'سلام'\n"
        report = Cx.analyze_source(source)
        assert report["functions_analysis"]["functions"][0]["cyclomatic_complexity"] == 4


# ======================================================================================
# peer_code_reviewer_agent
# ======================================================================================


class TestPeerReview:
    def test_an_empty_diff_is_not_approved(self) -> None:
        for text in ("", "just some words\nthat are not a diff"):
            result = Peer.review_diff(text)
            assert result["reviewed"] is False and result["approved"] is False

    def test_nothing_to_review_is_not_approved(self) -> None:
        assert Peer.full_review()["approved"] is False

    def test_source_that_does_not_parse_is_not_approved(self) -> None:
        result = Peer.full_review(source_code="def broken(:\n")
        assert result["approved"] is False and result["errors"]

    def test_clean_source_is_approved(self) -> None:
        assert Peer.full_review(source_code='def f():\n    """ok."""\n')["approved"] is True

    def test_a_blank_source_alone_is_not_a_review(self) -> None:
        assert Peer.full_review(source_code="   \n")["approved"] is False

    DIFF = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,6 @@\n import os\n"
        "+++counter\n+# print(x)\n+model.eval()\n+try: pass\n+except Exception:\n+    print('hi')\n"
    )

    def test_added_lines_that_start_with_plus_plus_are_not_headers(self) -> None:
        assert Peer.review_diff(self.DIFF)["added_lines"] == 6

    def test_comments_and_method_calls_are_not_smells(self) -> None:
        codes = [(f["code"], f["line"]) for f in Peer.review_diff(self.DIFF)["findings"]]
        assert ("REV-INSECURE-EVAL", 4) not in codes  # model.eval() is not eval()
        assert all(line != 3 for _, line in codes)  # commented-out print
        assert ("REV-BROAD-EXCEPT", 6) in codes
        assert ("REV-DEBUG-PRINT", 7) in codes

    def test_a_bare_except_is_broad(self) -> None:
        result = Peer.review_diff("+++ b/a.py\n+try:\n+    pass\n+except:\n+    pass\n")
        assert result["approved"] is False

    def test_the_review_comment_for_an_unreviewed_diff_is_not_a_lgtm(self) -> None:
        comment = Peer.generate_pr_review_comment(Peer.full_review())
        assert "Approved (Looks Good" not in comment and "Review Incomplete" in comment

    def test_markdown_table_cells_are_escaped(self) -> None:
        comment = Peer.generate_pr_review_comment(
            {"approved": False, "findings": [{"severity": "MAJOR", "file": "a|b.py", "code": "X", "message": "m"}]}
        )
        assert "a\\|b.py" in comment


# ======================================================================================
# quality_gate_aggregator
# ======================================================================================


class TestQualityGate:
    def test_aggregating_no_results_is_not_ready(self) -> None:
        report = Gate.aggregate_all_checks()
        assert report["ready_to_merge"] is False
        assert {"security", "tests", "coverage"} <= set(report["not_run"])

    def test_complete_good_results_are_ready(self) -> None:
        report = Gate.aggregate_all_checks(
            test_result={"passed": 10, "failed": 0},
            sec_result={"clean": True, "findings": []},
            cov_result={"coverage_ratio": 0.85},
            arch_result={"is_consistent": True, "violations": []},
        )
        assert report["ready_to_merge"] is True

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"test_result": {}},
            {"test_result": {"passed": 5, "failed": 0, "errors": 2}},
            {"sec_result": {"secure": False, "findings": []}},
            {"sec_result": {"secure": True, "complete": False, "findings": []}},  # partly read PDF
            {"sec_result": {"has_secrets": True, "findings": []}},
            {"cov_result": {}},
            {"cov_result": {"coverage_ratio": math.nan}},
        ],
    )
    def test_incomplete_or_failing_results_block_the_gate(self, kwargs) -> None:
        base = dict(
            test_result={"passed": 10, "failed": 0},
            sec_result={"clean": True, "findings": []},
            cov_result={"coverage_ratio": 0.9},
        )
        base.update(kwargs)
        assert Gate.aggregate_all_checks(**base)["ready_to_merge"] is False

    def test_an_empty_architecture_result_is_a_failed_check_not_a_pass(self) -> None:
        report = Gate.aggregate_all_checks(
            test_result={"passed": 1, "failed": 0},
            sec_result={"clean": True, "findings": []},
            cov_result={"coverage_ratio": 0.9},
            arch_result={},
        )
        assert "architecture" in report["failed_checks"]

    def test_a_string_is_not_a_passing_result(self) -> None:
        result = Gate.evaluate_checks(
            security_pass="false", tests_pass=True, coverage_pass=True, complexity_pass=True
        )
        assert result["status"] == "FAILED" and "security" in result["failed_checks"]

    def test_a_gate_that_ran_nothing_never_approves(self) -> None:
        assert Gate.evaluate_checks(required_checks=[])["status"] == "FAILED"
        report = Gate.build_release_readiness_report(required_checks=[], blocking_checks=[], min_score_threshold=0)
        assert report["ready_to_merge"] is False

    def test_the_badge_and_summary_tolerate_garbage(self) -> None:
        assert "0.0" in Gate.format_quality_gate_badge(math.nan, False)
        assert "Failed" in Gate.generate_ci_blocker_summary(None)


# ======================================================================================
# data_quality_and_validation_engine
# ======================================================================================


class TestDataQuality:
    def test_no_records_or_no_rules_is_not_valid(self) -> None:
        assert Dq.validate_records([], ["a"])["data_valid"] is False
        assert Dq.validate_records([{"a": 1}])["data_valid"] is False

    def test_whitespace_counts_as_blank_and_a_missing_key_is_not_unique(self) -> None:
        result = Dq.validate_records([{"a": "  ", "id": 1}, {"a": "x"}], ["a"], "id")
        assert result["null_violations_count"] == 1 and result["missing_unique_key_count"] == 1

    def test_non_object_rows_are_violations(self) -> None:
        assert Dq.validate_records([1], ["a"])["data_valid"] is False

    def test_bounds_catch_infinity_and_huge_ints_without_raising(self) -> None:
        rows = [{"n": math.inf}, {"n": 10**400}, {"n": "x"}, {"n": True}, {"n": "5"}]
        result = Dq.check_numeric_bounds(rows, "n", min_value=0)
        assert result["violations_count"] == 4 and result["within_bounds"] is False

    def test_bounds_with_nothing_to_check_or_bad_bounds_are_not_a_pass(self) -> None:
        assert Dq.check_numeric_bounds([{"a": 1}], "missing")["within_bounds"] is False
        assert Dq.check_numeric_bounds([], "a")["within_bounds"] is False
        assert Dq.check_numeric_bounds([{"a": 1}], "a", min_value=math.nan)["within_bounds"] is False
        assert Dq.check_numeric_bounds([{"a": 1}], "a", min_value=5, max_value=1)["within_bounds"] is False

    def test_outliers_survive_infinity_and_say_when_the_sample_is_too_small(self) -> None:
        rows = [{"v": i} for i in range(5)] + [{"v": math.inf}]
        result = Dq.detect_statistical_outliers(rows, "v")
        assert result["outliers"][0]["reason"] == "non_finite"
        assert "cannot flag anything" in result["note"]

    def test_a_nan_threshold_is_rejected(self) -> None:
        assert "error" in Dq.detect_statistical_outliers([{"v": 1}] * 5, "v", math.nan)

    def test_types_bool_is_not_int_and_unknown_labels_and_missing_columns_fail(self) -> None:
        result = Dq.validate_types_and_schemas(
            [{"a": True, "c": "x"}], {"a": "int", "c": "date", "d": "str"}
        )
        assert result["schema_compliant"] is False
        assert result["violations_count"] == 1
        assert result["unknown_types"] == {"c": "date"} and result["columns_not_found"] == ["d"]

    def test_types_with_nothing_to_validate_are_not_compliant(self) -> None:
        assert Dq.validate_types_and_schemas([], {"a": "int"})["schema_compliant"] is False
        assert Dq.validate_types_and_schemas([{"a": 1}], {})["schema_compliant"] is False

    def test_duplicates_with_unhashable_values_do_not_raise(self) -> None:
        assert Dq.detect_duplicates([{"a": [1]}, {"a": [1]}], ["a"])["duplicates_count"] == 1
        assert Dq.detect_duplicates([{"a": 1}, {"a": 1}], "a")["duplicates_count"] == 1


# ======================================================================================
# observability_incident_analyzer
# ======================================================================================


class TestObservability:
    def test_an_empty_log_is_no_data_not_healthy(self) -> None:
        assert Obs.identify_root_cause([])["status"] == "no_data"
        assert Obs.identify_root_cause(["   "])["status"] == "no_data"
        assert Obs.identify_root_cause(["all good"])["status"] == "healthy"

    def test_arabic_errors_are_incidents(self) -> None:
        result = Obs.parse_error_logs(["تم بنجاح", "حدث خطأ في الاتصال بقاعدة البيانات"])
        assert result["incidents_count"] == 1

    def test_a_traceback_credits_its_exception_without_a_second_incident(self) -> None:
        logs = [
            "Traceback (most recent call last)",
            '  File "x.py", line 1, in f',
            "    boom()",
            "KeyError: 'a'",
            "INFO later",
        ]
        result = Obs.parse_error_logs(logs)
        assert result["incidents_count"] == 1
        assert result["top_exceptions"] == [("KeyError", 1)]

    def test_non_string_log_items_do_not_raise(self) -> None:
        assert Obs.parse_error_logs([None, 5, "ERROR ValueError x"])["incidents_count"] == 1

    def test_latency_garbage_is_not_reported_as_no_outliers(self) -> None:
        assert Obs.extract_latency_outliers([math.nan, -5, "x"])["status"] == "no_data"
        mixed = Obs.extract_latency_outliers([10, math.nan, 2500], 1000)
        assert mixed["status"] == "partial" and mixed["invalid_count"] == 1 and mixed["outliers_count"] == 1
        assert Obs.extract_latency_outliers([1], math.nan)["status"] == "invalid_input"

    def test_a_burst_across_a_window_boundary_is_a_spike(self) -> None:
        # One early event, then 10 errors within 10 s. A fixed 60 s window anchored at t=0 would
        # see 6 of them and a second window 5, and never reach the threshold of 10.
        stamps = [0.0] + [55.0, 56, 57, 58, 59] + [61.0, 62, 63, 64, 65]
        spikes = Obs.detect_error_spikes(stamps, window_seconds=60.0, spike_threshold=10)
        assert spikes and spikes[0]["count"] == 10

    def test_non_finite_timestamps_are_ignored(self) -> None:
        assert Obs.detect_error_spikes([math.nan, 1.0, 2.0], 10, 2)[0]["count"] == 2

    def test_a_postmortem_for_a_healthy_log_does_not_crash_or_invent_an_exception(self) -> None:
        text = Obs.generate_incident_postmortem("t", Obs.identify_root_cause(["fine"]))
        assert "driven predominantly" not in text and "No error events" in text

    def test_postmortem_markup_cannot_be_broken_by_log_text(self) -> None:
        analysis = {"status": "degraded", "incidents_count": 1, "most_common_exception": "X",
                    "first_incident": {"line_number": 1, "log": "bad `tick`\nnew line"}}
        assert "\nnew line" not in Obs.generate_incident_postmortem("t", analysis)


# ======================================================================================
# dependency_license_compliance_scanner
# ======================================================================================


def _compliant(licence: str, **kwargs) -> bool:
    return Lic.scan_licenses([{"package": "p", "license": licence}], **kwargs)["compliant"]


class TestLicences:
    @pytest.mark.parametrize(
        "licence",
        [
            "GNU General Public License v3 (GPLv3)",
            "GNU GPL v2",
            "GNU Affero General Public License v3",
            "GPL",
            "GPL-3.0 AND (MIT OR BSD-3-Clause)",  # the GPL half is mandatory
            "MIT AND GPL-3.0",
            "Proprietary",
        ],
    )
    def test_copyleft_spellings_and_expressions_are_caught(self, licence) -> None:
        assert _compliant(licence) is False

    @pytest.mark.parametrize(
        "licence",
        [
            "GNU Lesser General Public License v3 (LGPLv3)",
            "MIT OR GPL-3.0",
            "(MIT OR GPL-3.0) AND Apache-2.0",
            "MIT License",
            "Apache Software License",
            "Mozilla Public License 2.0 (MPL 2.0)",
        ],
    )
    def test_permitted_licences_still_pass(self, licence) -> None:
        assert _compliant(licence) is True

    @pytest.mark.parametrize("licence", ["مرخص خاص", "Foo License", "((((MIT", "MIT)(", "Unknown license"])
    def test_unrecognised_or_unparseable_licences_are_not_cleared(self, licence) -> None:
        assert _compliant(licence) is False

    def test_a_pathological_expression_cannot_stall_the_parser(self) -> None:
        started = time.monotonic()
        _compliant("(" * 5000 + "MIT" + ")" * 5000)
        _compliant(" OR ".join(["(MIT AND GPL-3.0)"] * 300))
        assert time.monotonic() - started < 2.0

    def test_scanning_nothing_is_not_compliant(self) -> None:
        assert Lic.scan_licenses([])["compliant"] is False
        assert Lic.scan_requirements_text("")["compliant"] is False
        assert Lic.scan_licenses([None, "x"])["violations_count"] == 2

    def test_forbidden_and_allowed_lists_accept_the_same_aliases(self) -> None:
        assert _compliant("GPLv3", forbidden_tokens=["GPL-3.0"]) is False
        assert _compliant("LGPL-3.0", allowed_licenses=["lgpl-3.0"]) is True

    def test_requirements_option_lines_and_vcs_urls(self) -> None:
        clean = Lic.scan_requirements_text("fastapi==0.100\n-c constraints.txt\n-i https://x/simple\n--hash=sha256:aa\n")
        assert clean["compliant"] is True
        vcs = Lic.scan_requirements_text("git+https://example.org/y.git#egg=zed")
        assert vcs["compliant"] is False and vcs["violations"][0]["package"] == "zed"
        assert clean["licenses_inferred"] is True

    def test_compatibility_uses_and_or_semantics_and_unknowns(self) -> None:
        result = Lic.check_license_compatibility(
            "MIT", ["MIT AND GPL-3.0", "MIT OR GPL-3.0", "AGPL-3.0", "LGPL-3.0", "UNKNOWN"]
        )
        flagged = {c["dependency_license"]: c["severity"] for c in result["conflicts"]}
        assert flagged == {"MIT AND GPL-3.0": "HIGH", "AGPL-3.0": "CRITICAL", "UNKNOWN": "MEDIUM"}

    def test_compatibility_with_nothing_to_check_is_not_compatible(self) -> None:
        assert Lic.check_license_compatibility("MIT", [])["compatible"] is False
        assert Lic.check_license_compatibility("", ["MIT"])["compatible"] is False

    def test_the_report_fills_the_normalised_column_and_escapes_cells(self) -> None:
        report = Lic.generate_compliance_report(Lic.scan_licenses([{"package": "a|b", "license": "GPLv3"}]))
        assert "`GPL-3.0`" in report and "a\\|b" in report


# ======================================================================================
# model_drift_and_performance_monitor
# ======================================================================================


class TestDrift:
    def test_kl_divergence_of_unusable_input_reports_drift_not_stability(self) -> None:
        for base, cur in (([math.nan, 1], [1, 1]), ([1, -1], [1, 1]), ([0, 0], [1, 1]), ([1, 2], [1]), ([], [])):
            result = Drift.calculate_kl_divergence(base, cur)
            assert result["valid"] is False
            assert result["drift_detected"] is True and result["status"] == "invalid_input"

    def test_a_zero_epsilon_does_not_raise(self) -> None:
        assert Drift.calculate_kl_divergence([0.5, 0.0], [0.5, 0.5], epsilon=0)["drift_detected"] is True

    def test_identical_and_diverged_distributions(self) -> None:
        assert Drift.calculate_kl_divergence([0.5, 0.5], [0.5, 0.5])["drift_detected"] is False
        assert Drift.calculate_kl_divergence([0.9, 0.1], [0.1, 0.9])["drift_detected"] is True

    def test_a_mean_that_overflows_is_invalid_not_no_drift(self) -> None:
        assert Drift.check_mean_shift([1e308, 1e308], [1.0, 1.0])["status"] == "invalid_input"

    def test_negative_tolerances_are_invalid(self) -> None:
        assert Drift.check_accuracy_drift(0.9, 0.9, max_allowed_drop=-0.1)["status"] == "invalid_input"
        assert Drift.check_mean_shift([1.0], [1.0], max_rel_shift=-1)["status"] == "invalid_input"

    def test_concept_drift_refuses_mismatched_lengths(self) -> None:
        result = Drift.detect_concept_drift([1, 1, 1], [1, 1], [1, 1, 1])
        assert result["drift_detected"] is True and result["status"] == "invalid_input"

    def test_concept_drift_threshold_is_not_a_float_equality_coin_toss(self) -> None:
        # 20 samples, accuracy 1.0 -> 0.95 is a drop of exactly 0.05, which must not exceed 0.05.
        result = Drift.detect_concept_drift([1] * 20, [1] * 20, [1] * 19 + [0])
        assert result["drift_detected"] is False
        assert Drift.detect_concept_drift([1] * 20, [1] * 20, [1] * 19 + [0], max_allowed_drop=0.0)["drift_detected"]

    def test_the_alert_report_never_says_stable_without_a_verdict(self) -> None:
        assert "STABLE" not in Drift.generate_drift_alert_report({})
        assert "STABLE" not in Drift.generate_drift_alert_report(Drift.check_accuracy_drift(math.nan, 0.5))
        assert "STABLE" in Drift.generate_drift_alert_report(Drift.check_accuracy_drift(0.9, 0.9))


# ======================================================================================
# PDFs: the whole document is read through tools/open/_input, then handed to the tool
# ======================================================================================


def _long_pdf(last_page_lines: list[str], pages: int = 45) -> bytes:
    body = [{"text": [f"filler line on page {n + 1}"]} for n in range(pages - 1)]
    return _pdf(body + [{"text": last_page_lines}])


class TestPdfInputReachesTheTools:
    def test_a_finding_on_the_last_page_of_a_long_pdf_is_found(self) -> None:
        read = read_source_bytes(_long_pdf(["DELETE FROM users"]), name="schema.pdf")
        assert read.pages == 45 and read.complete is True
        assert Db.inspect_sql(read.text)["unbounded_write"] is True

    def test_logs_in_a_pdf_are_analysed_to_the_end(self) -> None:
        read = read_source_bytes(_long_pdf(["ERROR LastPageError boom"]), name="log.pdf")
        result = Obs.parse_error_logs(read.text.splitlines())
        assert result["incidents_count"] == 1
        assert result["top_exceptions"] == [("LastPageError", 1)]

    def test_requirements_in_a_pdf_are_scanned_to_the_end(self) -> None:
        read = read_source_bytes(_long_pdf(["gplonly==1.0  # reporting service"]), name="requirements.pdf")
        # (a page with under 16 characters of text would be classed blank by the extractor)
        result = Lic.scan_requirements_text("\n".join(l for l in read.text.splitlines() if "==" in l))
        assert [v["package"] for v in result["violations"]] == ["gplonly"]

    def test_a_scanned_page_is_reported_and_the_gate_does_not_pass_it(self) -> None:
        pdf = _pdf([{"text": ["print('ok')"]}, {"image": True}])
        read = read_source_bytes(pdf, name="scan.pdf")
        assert read.complete is False and read.unreadable_pages == (2,)
        gate = Gate.aggregate_all_checks(
            test_result={"passed": 1, "failed": 0},
            sec_result={"secure": True, "complete": read.complete, "findings": []},
            cov_result={"coverage_ratio": 1.0},
        )
        assert gate["ready_to_merge"] is False
