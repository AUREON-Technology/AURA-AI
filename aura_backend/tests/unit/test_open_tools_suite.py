import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from aura_core.tools.open.complexity_and_duplication_analyzer import ComplexityAndDuplicationAnalyzer
from aura_core.tools.open.dependency_manager_validator import DependencyManagerValidator
from aura_core.tools.open.performance_profiler import PerformanceProfiler
from aura_core.tools.open.regression_impact_analyzer import RegressionImpactAnalyzer
from aura_core.tools.open.test_coverage_and_mutation_analyzer import TestCoverageAndMutationAnalyzer


def test_performance_profiler_argument_order():
    def add(a, b):
        return a + b

    # Positional args are forwarded cleanly, not swallowed by iterations/top_n/max_allowed_ms
    res = PerformanceProfiler.benchmark_callable(add, 10, 20, iterations=3)
    assert res["success"] is True
    assert res["iterations"] == 3

    res_budget = PerformanceProfiler.check_latency_budget(add, 5, 10, max_allowed_ms=50.0)
    assert res_budget["success"] is True
    assert res_budget["within_budget"] is True

    res_prof = PerformanceProfiler.profile_cprofile_top_functions(add, 1, 2, top_n=5)
    assert res_prof["success"] is True


def test_performance_profiler_async_support():
    async def async_calc(x):
        return x * 2

    res_async = PerformanceProfiler.profile_callable(async_calc, 21)
    assert res_async["success"] is True
    assert res_async["result"] == 42


def test_performance_profiler_fault_handling():
    def faulty():
        raise RuntimeError("boom")

    res_faulty = PerformanceProfiler.benchmark_callable(faulty, iterations=2)
    assert res_faulty["success"] is False
    assert "boom" in res_faulty["error"]


def test_performance_profiler_tracemalloc_preservation():
    import tracemalloc
    tracemalloc.start()
    try:
        assert tracemalloc.is_tracing() is True
        res_tm = PerformanceProfiler.profile_callable(lambda x: x + 1, 10)
        assert res_tm["success"] is True
        assert tracemalloc.is_tracing() is True, "profile_callable killed external tracemalloc"
    finally:
        tracemalloc.stop()


def test_complexity_with_blocks_not_decisions():
    code_with = """
def f():
    with open('a') as f1:
        with open('b') as f2:
            pass
"""
    rep_with = ComplexityAndDuplicationAnalyzer.analyze_functions(code_with)
    fn_f = rep_with["functions"][0]
    assert fn_f["cyclomatic_complexity"] == 1
    assert fn_f["max_nesting_depth"] == 0


def test_complexity_match_case_counted():
    code_match = """
def check_val(x):
    match x:
        case 1:
            return 'one'
        case 2:
            return 'two'
        case 3:
            return 'three'
        case 4:
            return 'four'
"""
    rep_match = ComplexityAndDuplicationAnalyzer.analyze_functions(code_match)
    fn_match = rep_match["functions"][0]
    assert fn_match["cyclomatic_complexity"] == 4


def test_complexity_nested_functions_not_double_counted():
    code_nested = """
def outer():
    if True:
        pass
    def inner():
        if True:
            pass
        if False:
            pass
"""
    rep_nested = ComplexityAndDuplicationAnalyzer.analyze_functions(code_nested)
    outer_fn = next(f for f in rep_nested["functions"] if f["name"] == "outer")
    inner_fn = next(f for f in rep_nested["functions"] if f["name"] == "inner")
    assert outer_fn["cyclomatic_complexity"] == 2
    assert inner_fn["cyclomatic_complexity"] == 3


def test_complexity_flat_elif_nesting_is_one():
    code_elif = """
def eval_score(s):
    if s > 90:
        return 'A'
    elif s > 80:
        return 'B'
    elif s > 70:
        return 'C'
    else:
        return 'D'
"""
    rep_elif = ComplexityAndDuplicationAnalyzer.analyze_functions(code_elif)
    fn_elif = rep_elif["functions"][0]
    assert fn_elif["max_nesting_depth"] == 1


def test_duplication_merged_windows():
    block = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\ng = 7\nh = 8\ni = 9\nj = 10\n"
    code_dup = f"# Block 1\n{block}\n# Block 2\n{block}"
    rep_dup = ComplexityAndDuplicationAnalyzer.detect_duplicate_blocks(code_dup, window_size=5)
    assert rep_dup["has_duplication"] is True
    assert rep_dup["duplicates_count"] == 1


def test_dependency_pep503_normalization():
    req_content = "Foo_Bar==1.0.0\nfoo-bar==2.0.0\n"
    conflicts = DependencyManagerValidator.detect_duplicate_or_conflicting_specs(req_content)
    assert conflicts["has_duplicates"] is True
    assert conflicts["has_conflicts"] is True


def test_dependency_mixed_operators_detected():
    req_mixed = "requests==2.31.0\nrequests>=2.28.0\n"
    conflicts_mixed = DependencyManagerValidator.detect_duplicate_or_conflicting_specs(req_mixed)
    assert conflicts_mixed["has_conflicts"] is True


def test_dependency_vcs_lines_not_parsed_as_packages():
    req_vcs = "git+https://github.com/psf/requests.git@v2.31.0\npytest==7.4.0\n"
    parsed = DependencyManagerValidator.parse_requirements_text(req_vcs)
    names = [p["name"] for p in parsed["packages"]]
    assert "git" not in names
    assert len(parsed["vcs_references"]) == 1


def test_dependency_package_json_ranges():
    pkg_json = '{"name": "test", "dependencies": {"react": "^18.2.0", "lodash": "~4.17.21", "vue": "*", "axios": "1.x"}}'
    pkg_res = DependencyManagerValidator.validate_package_json(pkg_json)
    assert pkg_res["valid"] is False
    assert len(pkg_res["wildcards"]) == 1
    assert len(pkg_res["ranged"]) == 3


def test_regression_base_ref_injection_rejected():
    res_bad_ref = RegressionImpactAnalyzer.get_git_changed_files(".", base_ref="--output=/tmp/pwned")
    assert res_bad_ref["success"] is False
    assert "Invalid base_ref" in res_bad_ref["error"]


def test_regression_deleted_files_in_diff():
    diff_text = """diff --git a/old_file.py b/old_file.py
deleted file mode 100644
--- a/old_file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def gone():
-    pass
"""
    changed = RegressionImpactAnalyzer.parse_changed_files_from_diff(diff_text)
    assert len(changed) == 1
    assert changed[0]["path"] == "old_file.py"
    assert changed[0]["deleted"] is True


def test_regression_module_fragments():
    frags = RegressionImpactAnalyzer._module_fragments("aura_backend/aura_core/llm/router.py")
    assert "aura_core.llm.router" in frags
    assert "llm.router" in frags
    assert "router" in frags


def test_mutation_ast_precise_no_substring_replace():
    code = "TrueColor = 1\nx = TrueColor if True else False\n"
    muts = TestCoverageAndMutationAnalyzer.generate_boolean_mutations(code)
    assert len(muts) == 2
    assert all(m["original"] in (True, False) for m in muts)


def test_mutation_empty_candidates_returns_none_score():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        src = tdp / "calc.py"
        src.write_text("def add(a, b):\n    return a + b\n")
        res = TestCoverageAndMutationAnalyzer.evaluate_mutations_against_tests("calc.py", td)
        assert res["evaluated"] is True
        assert res["mutation_score"] is None
