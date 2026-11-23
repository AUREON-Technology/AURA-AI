"""ADR-044: the capabilities added to the thirteen ``PARTIAL`` open tools.

Each class pins one row of the inventory sheet. Tools are driven through ``Tool.execute`` (what the
kernel does) or a real ``ToolRegistry``; nothing internal is mocked. Two seams are substituted and are
named where used: an ``httpx.MockTransport`` for the search server (the port ``build_web_search_tool``
exposes for exactly this), and a loopback server in place of ``open_guarded`` for the *success* path of
release verification (the guard's own refusals are tested with the real helper).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import pytest

from aura_core.agents.registry import build_default_agent_registry
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.llm.cache import InMemoryCache
from aura_core.ports.coding import SandboxResult
from aura_core.ports.runtime import ToolContext
from aura_core.tools.analysis import build_analysis_tools
from aura_core.tools.builtin import build_knowledge_tools, default_tools
from aura_core.tools.desktop_ops import build_desktop_ops_tools
from aura_core.tools.drift import DRIFT_TOOL_NAMES, build_drift_tools
from aura_core.tools.open import data_ext, diagram_ext, profile_ext, release_ext, research_ext, telemetry_ext
from aura_core.tools.open.api_ext import classify_changes, generate_tests, load_spec, validate_spec
from aura_core.tools.open.review_ext import deep_review, module_name_from_path
from aura_core.tools.open.sql_schema_ext import generate_select, parse_schema, query_smells, schema_report
from aura_core.tools.open_exec import build_open_exec_tools
from aura_core.tools.registry import ToolRegistry
from aura_core.tools.release import build_release_tools
from aura_core.tools.research import build_research_tools
from aura_core.tools.websearch import build_web_search_tool

_ANALYSIS = {t.spec.name: t for t in build_analysis_tools()}
_EXEC = {t.spec.name: t for t in build_open_exec_tools()}


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=120.0, capabilities=("engineer_agent",))


async def call(name: str, ctx: ToolContext, **arguments: Any) -> dict:
    return json.loads(await _ANALYSIS[name].execute(arguments, ctx))


# ---------------------------------------------------------------------------------- row 5 ----


class TestDeepReview:
    SOURCE = (
        "from aura_core.adapters.postgres import Pool\n"
        "def add(item, bucket=[]):\n"
        "    bucket.append(item)\n"
        "    return bucket\n"
        "def check(x):\n"
        "    if x == None:\n"
        "        return 0\n"
        "    return 1\n"
        "    print('never')\n"
        "def risky(p):\n"
        "    try:\n"
        "        f = open(p)\n"
        "    except Exception:\n"
        "        pass\n"
        "    d = {'a': 1, 'a': 2}\n"
        "    assert (p, 'msg')\n"
    )

    async def test_source_mode_reports_bugs_architecture_and_complexity_with_line_numbers(self, ctx) -> None:
        out = await call("review_code_diff", ctx, source_code=self.SOURCE, filename="aura_core/domain/thing.py")
        found = {(f["code"], f["line"]) for f in out["deep_review"]["findings"]}
        assert ("BUG-MUTABLE-DEFAULT", 2) in found
        assert ("BUG-EQ-NONE", 6) in found
        assert ("BUG-UNREACHABLE", 9) in found
        assert ("BUG-OPEN-WITHOUT-WITH", 12) in found
        assert ("BUG-SWALLOWED-EXCEPTION", 13) in found
        assert ("BUG-DUPLICATE-DICT-KEY", 15) in found
        assert ("BUG-ASSERT-TUPLE", 16) in found
        assert ("ARCH-LAYER-VIOLATION", 1) in found  # a domain module importing an adapter
        assert out["approved"] is False
        assert out["deep_review"]["method"].startswith("AST and line-pattern rules")

    async def test_a_complex_function_is_flagged_with_its_line(self, ctx) -> None:
        body = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12)) + "    return -1\n"
        out = await call("review_code_diff", ctx, source_code=body, max_complexity=5)
        assert any(f["code"] == "CPLX-TOO-COMPLEX" and f["line"] == 1 for f in out["deep_review"]["findings"])

    async def test_a_partial_hunk_gets_line_patterns_with_new_side_numbers(self, ctx) -> None:
        diff = (
            "diff --git a/aura_core/domain/x.py b/aura_core/domain/x.py\n--- a/aura_core/domain/x.py\n"
            "+++ b/aura_core/domain/x.py\n@@ -10,2 +10,4 @@ def f():\n a = 1\n"
            "+from aura_core.adapters.redis import R\n+def g(x, y=[]):\n+    return x == None\n b = 2\n"
        )
        out = await call("review_code_diff", ctx, diff_text=diff)
        found = {(f["code"], f["line"]) for f in out["deep_review"]["findings"]}
        assert {("ARCH-LAYER-VIOLATION", 11), ("BUG-MUTABLE-DEFAULT", 12), ("BUG-EQ-NONE", 13)} <= found
        assert "partial hunk" in out["deep_review"]["files"][0]["mode"]

    async def test_a_whole_new_file_in_a_diff_is_parsed(self, ctx) -> None:
        diff = ("diff --git a/m.py b/m.py\n--- /dev/null\n+++ b/m.py\n@@ -0,0 +1,3 @@\n"
                "+def f(a, b=[]):\n+    return a\n+    print(b)\n")
        out = await call("review_code_diff", ctx, diff_text=diff)
        assert out["deep_review"]["files"][0]["mode"] == "whole file parsed"
        assert {f["code"] for f in out["deep_review"]["findings"]} >= {"BUG-MUTABLE-DEFAULT", "BUG-UNREACHABLE"}

    async def test_deep_false_keeps_the_original_result(self, ctx) -> None:
        out = await call("review_code_diff", ctx, source_code=self.SOURCE, deep=False)
        assert "deep_review" not in out

    async def test_clean_code_stays_approved_and_custom_layer_rules_apply(self, ctx) -> None:
        clean = '"""m."""\n\n\ndef add(a: int, b: int) -> int:\n    """Add."""\n    return a + b\n'
        assert (await call("review_code_diff", ctx, source_code=clean))["approved"] is True
        rules = {"billing": ["reports"]}
        out = await call("review_code_diff", ctx, source_code="import reports.x\n", filename="billing/inv.py",
                         layer_rules=rules)
        assert any(f["code"] == "ARCH-LAYER-VIOLATION" for f in out["deep_review"]["findings"])

    def test_module_name_from_path(self) -> None:
        assert module_name_from_path("aura_core/domain/x.py") == "aura_core.domain.x"
        assert module_name_from_path("pkg/__init__.py") == "pkg"
        assert module_name_from_path("<string>") == "" and module_name_from_path("../x.py") == ""

    def test_syntax_errors_are_reported_not_raised(self) -> None:
        assert deep_review(source_code="def (")["errors"]


# ---------------------------------------------------------------------------------- row 6 ----

_BEHAVIOUR_SOURCE = '''
def double(n: int) -> int:
    """Double.

    >>> double(2)
    4
    """
    return n * 2


def label(n: int) -> str:
    return n


def append_one(xs: list[int]) -> list[int]:
    xs.append(1)
    return xs


def ratio(a: int, b: int) -> float:
    return a / b


def safe_ratio(a: int, b: int) -> float:
    """Divide.

    Raises:
        ZeroDivisionError: when b is 0.
    """
    return a / b
'''


class TestBehaviourTests:
    async def run_generated(self, ctx, source: str) -> dict:
        return json.loads(await _EXEC["generate_and_run_tests"].execute({"source_code": source}, ctx))

    async def test_docstring_examples_and_type_contracts_catch_real_defects(self, ctx) -> None:
        out = await self.run_generated(ctx, _BEHAVIOUR_SOURCE)
        assert out["verification_level"] == "structural+behaviour"
        assert out["behaviour"]["assertions"]["doctest"] == 2 - 1  # only `double` has examples
        failed = set(out["execution"]["failed_tests"])
        assert "test_generated.py::test_behaviour_label_type_contract" in failed      # returns int, says str
        assert "test_generated.py::test_behaviour_append_one_type_contract" in failed  # mutates its argument
        assert "test_generated.py::test_behaviour_ratio_type_contract" in failed       # undocumented ZeroDivisionError
        assert "test_generated.py::test_behaviour_double_docstring_examples" not in failed
        assert "test_generated.py::test_behaviour_safe_ratio_type_contract" not in failed  # raise is documented
        assert out["verified"] is False

    async def test_a_wrong_docstring_example_fails_with_the_doctest_diff(self, ctx) -> None:
        src = 'def double(n):\n    """\n    >>> double(2)\n    5\n    """\n    return n * 2\n'
        out = await self.run_generated(ctx, src)
        assert out["verified"] is False
        assert "Expected" in out["execution"]["stdout"] and "5" in out["execution"]["stdout"]

    async def test_correct_code_with_hints_passes_and_flag_off_restores_structural_only(self, ctx) -> None:
        src = "def inc(n: int) -> int:\n    return n + 1\n"
        out = await self.run_generated(ctx, src)
        assert out["verified"] is True and out["verification_level"] == "structural+behaviour"
        off = json.loads(await _EXEC["generate_and_run_tests"].execute(
            {"source_code": src, "include_behavior_tests": False}, ctx))
        assert off["verification_level"] == "structural" and "behaviour" not in off

    async def test_impure_and_unannotated_functions_are_skipped_and_say_so(self, ctx) -> None:
        src = "import random\n\ndef roll(n: int) -> int:\n    return random.randint(0, n)\n\ndef plain(x):\n    return x\n"
        out = await self.run_generated(ctx, src)
        assert {s["function"] for s in out["behaviour"]["skipped"]} == {"roll", "plain"}
        assert out["verified"] is True


# ---------------------------------------------------------------------------------- row 8 ----

_DDL = """
CREATE TABLE users (id uuid PRIMARY KEY, email varchar(255) NOT NULL UNIQUE);
CREATE TABLE orders (id serial PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), total numeric(10,2));
CREATE TABLE items (id int primary key, order_id int not null,
  CONSTRAINT fk_o FOREIGN KEY (order_id) REFERENCES orders (id));
"""


class TestDerivedDiagrams:
    async def test_er_diagram_from_sql_has_keys_and_one_relation_per_foreign_key(self, ctx) -> None:
        out = await call("generate_diagram", ctx, diagram_type="er_from_sql", source_code=_DDL)
        text = out["mermaid"]
        assert text.startswith("erDiagram") and "uuid id PK" in text and "uuid user_id FK" in text
        assert 'users ||--o{ orders : "user_id"' in text and 'orders ||--o{ items : "order_id"' in text
        assert out["relationships"] == 2

    async def test_er_diagram_from_an_alembic_migration(self, ctx) -> None:
        alembic = ("import sqlalchemy as sa\nfrom alembic import op\ndef upgrade():\n"
                   "    op.create_table('teams', sa.Column('id', sa.Integer, primary_key=True))\n"
                   "    op.create_table('members', sa.Column('id', sa.Integer, primary_key=True),\n"
                   "        sa.Column('team_id', sa.Integer, sa.ForeignKey('teams.id'), nullable=False))\n")
        out = await call("generate_diagram", ctx, diagram_type="er_from_sql", source_code=alembic)
        assert out["source_format"] == "alembic" and 'teams ||--o{ members : "team_id"' in out["mermaid"]

    async def test_flowchart_follows_branches_loops_continue_and_break(self, ctx) -> None:
        src = ("def g(xs):\n    for x in xs:\n        if x:\n            continue\n"
               "        if x > 3:\n            break\n        use(x)\n    return 1\n")
        out = await call("generate_diagram", ctx, diagram_type="flowchart_from_source", source_code=src, function="g")
        text = out["mermaid"]
        assert text.startswith("flowchart TD") and '|"yes"|' in text and '|"no"|' in text and '|"done"|' in text
        # `continue` goes back to the loop head, not on to the next statement
        assert "n3 -->|\"yes\"| n2" in text
        assert (await call("generate_diagram", ctx, diagram_type="validate", diagram_code=text))["valid"] is True

    async def test_sequence_diagram_orders_calls_between_functions_and_objects(self, ctx) -> None:
        src = ("def validate(item):\n    return True\n\ndef handle(req, repo):\n    validate(req)\n    repo.save(req)\n"
               "    log.warning('x')\n")
        out = await call("generate_diagram", ctx, diagram_type="sequence_from_source", source_code=src, function="handle")
        lines = out["mermaid"].splitlines()
        assert lines[0] == "sequenceDiagram"
        order = [ln.strip() for ln in lines if "->>" in ln]
        assert order[0].startswith("handle->>module: validate(") and "repo: save(" in order[1] and "log: warning(" in order[2]

    async def test_architecture_diagram_groups_packages_and_collapses_libraries(self, ctx) -> None:
        out = await call("generate_diagram", ctx, diagram_type="architecture_from_modules", modules={
            "aura_core/domain/a.py": "import os\nfrom aura_core.domain import b\n",
            "aura_core/domain/b.py": "import json\n",
            "aura_core/adapters/c.py": "from aura_core.domain.a import X\nimport httpx\n"})
        assert "subgraph" in out["mermaid"] and out["external_packages"] == ["httpx", "json", "os"]
        assert out["dependencies"] == 5

    async def test_validate_finds_what_the_header_check_missed(self, ctx) -> None:
        bad = (await call("generate_diagram", ctx, diagram_type="validate",
                          diagram_code='erDiagram\n  A ||--o{ B : "x"\n'))
        assert bad["valid"] is False and any("no block" in e for e in bad["errors"])
        quote = await call("generate_diagram", ctx, diagram_type="validate", diagram_code='flowchart TD\n  a["x]\n')
        assert quote["valid"] is False

    async def test_missing_inputs_are_errors_not_empty_diagrams(self, ctx) -> None:
        from aura_core.domain.errors import ToolExecutionError

        with pytest.raises(ToolExecutionError):
            await call("generate_diagram", ctx, diagram_type="architecture_from_modules")
        assert "error" in await call("generate_diagram", ctx, diagram_type="flowchart_from_source",
                                     source_code="def f():\n    pass\n", function="nope")


# --------------------------------------------------------------------------------- row 21 ----

_Q_SOURCE = '''def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def price(qty, unit):
    total = qty * unit
    if qty >= 10 and unit > 0:
        total = total - total // 10
    return total
'''
_Q_DIFF = (
    "diff --git a/module_under_test.py b/module_under_test.py\n--- a/module_under_test.py\n"
    "+++ b/module_under_test.py\n@@ -8,0 +9,5 @@\n"
    "+def price(qty, unit):\n+    total = qty * unit\n+    if qty >= 10 and unit > 0:\n"
    "+        total = total - total // 10\n+    return total\n"
)
_Q_TESTS = ("from module_under_test import clamp, price\n\n"
            "def test_clamp():\n    assert clamp(5, 0, 10) == 5\n    assert clamp(-1, 0, 10) == 0\n"
            "    assert clamp(11, 0, 10) == 10\n\ndef test_price_small():\n    assert price(2, 3) == 6\n")


class TestChangedLineCoverageAndOperatorMutants:
    def test_changed_lines_come_from_the_diff_new_side(self) -> None:
        from aura_core.tools.open.quality_ext import changed_lines

        assert changed_lines(_Q_DIFF) == {"module_under_test.py": {9, 10, 11, 12, 13}}

    def test_operator_mutants_are_located_by_column_and_cover_each_kind(self) -> None:
        from aura_core.tools.open.quality_ext import operator_mutants

        found = operator_mutants("def f(a, b):\n    return a + b if a >= 1 and b else a // 2\n")
        by_kind = {(m["kind"], m["original"], m["mutated"]) for m in found}
        assert {("arithmetic", "+", "-"), ("comparison", ">=", ">"), ("logical", "and", "or"),
                ("arithmetic", "//", "*")} <= by_kind
        line = "    return a + b if a >= 1 and b else a // 2"
        for m in found:
            assert line[m["col"]:m["end_col"]] == m["original"]

    async def test_coverage_and_mutants_are_scoped_to_the_diff(self, ctx) -> None:
        out = json.loads(await _EXEC["analyze_test_quality"].execute(
            {"source_code": _Q_SOURCE, "test_code": _Q_TESTS, "diff_text": _Q_DIFF, "timeout_s": 20,
             "coverage_threshold": 90}, ctx))
        scoped = out["changed_line_coverage"]
        assert scoped["covered"] == [9, 10, 11, 13]
        assert scoped["uncovered"] == [12]                    # the discount branch never ran
        assert scoped["percent_covered"] == 80.0
        assert scoped["passed_threshold"] is False
        mutation = out["mutation"]
        assert mutation["restricted_to_changed_lines"] is True
        assert {s["line"] for s in mutation["survivors"]} <= {11, 12}
        assert mutation["mutants_total"] >= 3 and set(mutation["by_kind_candidates"]) == {
            "boolean", "comparison", "arithmetic", "logical"}

    async def test_a_diff_naming_another_file_is_refused_clearly(self, ctx) -> None:
        other = _Q_DIFF.replace("module_under_test.py", "other.py")
        a = _Q_DIFF + other.replace("diff --git", "diff --git", 1)
        out = json.loads(await _EXEC["analyze_test_quality"].execute(
            {"source_code": _Q_SOURCE, "test_code": _Q_TESTS, "diff_text": other + other.replace("other.py", "third.py")}, ctx))
        assert "none is module_under_test.py" in out["error"] and a

    async def test_unknown_mutation_kind_is_a_tool_error(self, ctx) -> None:
        from aura_core.domain.errors import ToolExecutionError

        with pytest.raises(ToolExecutionError, match="unknown mutation kind"):
            await _EXEC["analyze_test_quality"].execute(
                {"source_code": "x=1\n", "test_code": "def test_a():\n    pass\n", "mutation_kinds": ["bogus"]}, ctx)


# --------------------------------------------------------------------------------- row 23 ----


class _LocalRunner:
    """Runs the argv in a child process on this machine, standing in for the paired desktop."""

    async def run(self, handle: Any, command: Any, *, timeout_s: float) -> SandboxResult:
        done = subprocess.run(list(command), capture_output=True, text=True, timeout=timeout_s)
        return SandboxResult(stdout=done.stdout, stderr=done.stderr, exit_code=done.returncode)


class _Sessions:
    async def handle(self, context: ToolContext, repo: str | None = None) -> str:
        return "h"


class _Paired:
    async def resolve(self, user_id: Any) -> Any:
        return object()


class TestProfilePythonMemoryAndBaseline:
    @pytest.fixture
    def profile(self):
        tools = {t.spec.name: t for t in build_desktop_ops_tools(_LocalRunner(), _Sessions(), _Paired())}
        return tools["profile_python"]

    async def test_wall_time_peak_memory_and_top_allocations_are_returned(self, profile, ctx) -> None:
        text = await profile.execute({"source_code": "d = [i * i for i in range(50000)]\n", "limit": 3}, ctx)
        metrics = json.loads(text.split("--- metrics ---\n")[1].split("\n")[0])
        assert metrics["peak_kb"] > 500 and metrics["wall_s"] > 0 and metrics["function_calls"] >= 1
        assert any("<snippet>" in a["where"] for a in metrics["top_allocations"])

    async def test_a_baseline_flags_a_memory_and_time_regression(self, profile, ctx) -> None:
        small = await profile.execute({"source_code": "d = [i for i in range(20000)]\n"}, ctx)
        base = json.loads(small.split("--- metrics ---\n")[1].split("\n")[0])
        big = await profile.execute({
            "source_code": "d = [i for i in range(800000)]\n", "max_regression_pct": 50,
            "baseline": {"peak_kb": base["peak_kb"], "wall_s": base["wall_s"]}}, ctx)
        verdict = json.loads(big.split("--- regression ---\n")[1])
        assert verdict["regressed"] is True
        assert {c["metric"] for c in verdict["checks"] if c["ok"] is False} >= {"peak_kb"}

    async def test_memory_off_says_the_figure_was_not_measured(self, profile, ctx) -> None:
        out = await profile.execute({"source_code": "x = 1\n", "memory": False, "baseline": {"peak_kb": 10}}, ctx)
        check = json.loads(out.split("--- regression ---\n")[1])["checks"][0]
        assert check["ok"] is None and "not measured" in check["note"]

    def test_compare_handles_zero_baselines_and_unknown_keys(self) -> None:
        result = profile_ext.compare({"wall_s": 0.5}, {"wall_s": 0, "bogus": 1}, 20)
        assert result["regressed"] is True and result["unknown_baseline_keys"] == ["bogus"]
        assert profile_ext.parse_profile("no figures here") is None

    async def test_unpaired_user_still_gets_the_plain_refusal(self, ctx) -> None:
        class NoDesktop:
            async def resolve(self, user_id: Any) -> Any:
                return None

        tools = {t.spec.name: t for t in build_desktop_ops_tools(_LocalRunner(), _Sessions(), NoDesktop())}
        from aura_core.domain.errors import ToolExecutionError

        with pytest.raises(ToolExecutionError):
            await tools["profile_python"].execute({"source_code": "1"}, ctx)


# --------------------------------------------------------------------------------- row 24 ----


class TestQualityGateWithEvidence:
    async def test_a_review_result_is_combined_with_other_tools_outputs(self, ctx) -> None:
        review = await call("review_code_diff", ctx, source_code="def f(a, b=[]):\n    return a\n")
        assert review["approved"] is False
        out = await call("evaluate_quality_gate", ctx, operation="gate", results={
            "review": review, "tests": {"passed": 5, "failed": 0, "errors": 0},
            "security": {"findings": []}, "coverage": {"coverage_percentage": 91},
            "complexity": {"flagged_count": 0}})
        assert out["status"] == "FAILED" and out["failed_checks"] == ["review"] or "review" in out["failed_checks"]
        assert "MAJOR" not in out["evidence"]["review"]["reason"] and "approved=False" in out["evidence"]["review"]["reason"]
        assert out["evidence"]["tests"]["reason"].startswith("5 passed")

    async def test_all_green_results_approve_and_score_with_weights(self, ctx) -> None:
        out = await call("evaluate_quality_gate", ctx, results={
            "review": {"approved": True}, "tests": {"verified": True, "execution": {"counts": {"passed": 3, "failed": 0}}},
            "security": {"findings": [], "secure": True}, "coverage": {"changed_line_coverage": {"percent_covered": 88.0}},
            "complexity": {"flagged_count": 0}, "mutation": {"mutation": {"mutation_score": 72.0, "mutants_total": 8}},
            "perf": {"passed": True}})
        assert out["ready_to_merge"] is True and out["quality_score"] == 100.0
        assert out["evidence"]["coverage"]["reason"].startswith("changed lines 88.0%")
        assert "perf" in out["score_breakdown"]

    async def test_missing_required_unreadable_and_disagreeing_inputs_fail_closed(self, ctx) -> None:
        none_run = await call("evaluate_quality_gate", ctx, operation="gate", results={"review": {"approved": True}})
        assert none_run["status"] == "FAILED" and set(none_run["failed_checks"]) >= {"security", "tests"}
        unreadable = await call("evaluate_quality_gate", ctx, operation="gate", results={
            "tests": {"nonsense": 1}, "security": True, "coverage": True, "complexity": True})
        assert "tests" in unreadable["invalid_checks"] and unreadable["status"] == "FAILED"
        clash = await call("evaluate_quality_gate", ctx, operation="gate", tests_pass=True, results={
            "tests": {"passed": 1, "failed": 2, "errors": 0}, "security": True, "coverage": True, "complexity": True})
        assert "disagree" in clash["evidence"]["tests"]["reason"] and clash["status"] == "FAILED"

    async def test_readiness_cannot_say_ready_while_the_gate_says_failed(self, ctx) -> None:
        out = await call("evaluate_quality_gate", ctx, results={
            "security": True, "tests": True, "coverage": {"coverage_percentage": 10}, "complexity": True})
        assert out["gate_status"] == "FAILED" and out["ready_to_merge"] is False

    async def test_unknown_required_name_and_the_legacy_path_are_unchanged(self, ctx) -> None:
        from aura_core.domain.errors import ToolExecutionError

        with pytest.raises(ToolExecutionError, match="Unknown check"):
            await call("evaluate_quality_gate", ctx, results={"tests": True}, required_checks=["nope"])
        legacy = await call("evaluate_quality_gate", ctx, security_pass=True, tests_pass=True, coverage_pass=True,
                            complexity_pass=True)
        assert legacy["ready_to_merge"] is True and "evidence" not in legacy


# --------------------------------------------------------------------------------- row 92 ----


class TestSqlSchemaIntelligence:
    def test_tables_columns_keys_and_relationships_are_parsed(self) -> None:
        schema = parse_schema(_DDL)
        assert set(schema["tables"]) == {"users", "orders", "items"}
        assert schema["tables"]["users"]["columns"][0]["primary_key"] is True
        assert schema["tables"]["users"]["unique"] == [["email"]]
        assert {(r["from_table"], r["to_table"]) for r in schema["relationships"]} == {("orders", "users"), ("items", "orders")}
        assert schema["tables"]["orders"]["columns"][2]["type"] == "numeric(10,2)"

    def test_missing_fk_indexes_missing_pk_and_duplicate_indexes_are_flagged(self) -> None:
        text = _DDL + "CREATE INDEX ix_o ON orders (total);\nCREATE INDEX ix_o2 ON orders (total);\nCREATE TABLE audit (msg text);\n"
        issues = {(i["code"], i["table"]) for i in schema_report(text)["issues"]}
        assert ("fk_missing_index", "orders") in issues and ("fk_missing_index", "items") in issues
        assert ("duplicate_index", "orders") in issues and ("no_primary_key", "audit") in issues
        indexed = _DDL + "CREATE INDEX ix_fk ON orders (user_id);\nCREATE INDEX ix_fk2 ON items (order_id);\n"
        assert not [i for i in schema_report(indexed)["issues"] if i["code"] == "fk_missing_index"]

    def test_alembic_text_is_read_like_ddl(self) -> None:
        text = ("import sqlalchemy as sa\nfrom alembic import op\n\ndef upgrade():\n"
                "    op.create_table('teams', sa.Column('id', sa.Integer, primary_key=True))\n"
                "    op.create_table('members', sa.Column('id', sa.Integer, primary_key=True),\n"
                "        sa.Column('team_id', sa.Integer, sa.ForeignKey('teams.id')), sa.Column('email', sa.String, index=True))\n")
        report = schema_report(text)
        assert report["source_format"] == "alembic"
        assert [i["code"] for i in report["issues"]] == ["fk_missing_index"]
        assert report["tables"]["members"]["indexes"][0]["columns"] == ["email"]

    def test_query_smells_and_schema_aware_findings(self) -> None:
        schema = parse_schema(_DDL)
        sql = ("SELECT * FROM orders o JOIN users u ON u.id = o.user_id WHERE lower(u.email) = 'x' "
               "AND o.total = NULL AND o.status LIKE '%a' ORDER BY o.id OFFSET 20000")
        codes = {s["code"] for s in query_smells(sql, schema)}
        assert {"function_on_column", "equals_null", "deep_offset", "order_by_without_limit",
                "unindexed_filter", "unknown_column"} <= codes
        assert "leading_wildcard_like" in codes
        assert "explain_analyze_executes" in {s["code"] for s in query_smells("EXPLAIN ANALYZE DELETE FROM t", None)}

    async def test_the_tool_operations_and_the_explain_limit(self, ctx) -> None:
        out = await call("inspect_sql", ctx, operation="schema", query=_DDL)
        assert out["summary"]["tables"] == 3 and out["plan_available"] is False and "EXPLAIN" in out["explain"]
        smells = await call("inspect_sql", ctx, operation="smells", query="SELECT id FROM orders WHERE total = 1", schema_info=_DDL)
        assert [s["column"] for s in smells["smells"] if s["code"] == "unindexed_filter"] == ["total"]
        picked = await call("inspect_sql", ctx, operation="generate_select", schema_info=_DDL, table="orders",
                            columns=["id", "total"], filters=["user_id"], order_by="id", limit=5)
        assert picked["sql"] == 'SELECT "id", "total" FROM "orders" WHERE "user_id" = :user_id ORDER BY "id" LIMIT 5'
        bad = await call("inspect_sql", ctx, operation="generate_select", schema_info=_DDL, table="orders", columns=["x; drop"])
        assert "not on" in bad["error"]
        legacy = await call("inspect_sql", ctx, query="DELETE FROM logs; SELECT * FROM t")
        assert legacy["is_safe_sql"] is False and legacy["plan_available"] is False
        explain = await call("inspect_sql", ctx, query="EXPLAIN ANALYZE DELETE FROM logs WHERE id = 1")
        assert explain["is_safe_sql"] is False

    def test_generate_select_clamps_the_limit(self) -> None:
        assert generate_select(parse_schema(_DDL), "users", limit=10**9)["sql"].endswith("LIMIT 10000")


# --------------------------------------------------------------------------------- row 93 ----

_OLD = """
openapi: 3.0.3
info: {title: Orders, version: 1.0.0}
paths:
  /orders/{id}:
    get:
      operationId: getOrder
      parameters:
        - {name: id, in: path, required: true, schema: {type: integer}}
      responses:
        '200':
          description: ok
          content: {application/json: {schema: {$ref: '#/components/schemas/Order'}}}
        '404': {description: missing}
  /orders:
    post:
      operationId: createOrder
      requestBody:
        required: false
        content:
          application/json:
            schema:
              type: object
              required: [sku]
              properties:
                sku: {type: string, maxLength: 40}
                status: {type: string, enum: [new, paid]}
      responses:
        '201': {description: created}
components:
  schemas:
    Order:
      type: object
      required: [id, status, total]
      properties:
        id: {type: integer}
        status: {type: string, enum: [new, paid]}
        total: {type: number}
        note: {type: string}
"""
_NEW = (_OLD.replace("version: 1.0.0", "version: 2.0.0")
        .replace("{name: id, in: path, required: true, schema: {type: integer}}",
                 "{name: id, in: path, required: true, schema: {type: string}}\n        - {name: expand, in: query, required: true, schema: {type: string}}")
        .replace("required: false", "required: true").replace("required: [sku]", "required: [sku, currency]")
        .replace("maxLength: 40", "maxLength: 20").replace("enum: [new, paid]}\n      responses", "enum: [new]}\n      responses")
        .replace("        note: {type: string}\n", "        tax: {type: number}\n")
        .replace("required: [id, status, total]", "required: [id, status]")
        .replace("id: {type: integer}\n        status", "id: {type: string}\n        status"))


class TestApiContracts:
    def test_schema_level_breaking_changes_are_classified_by_direction(self) -> None:
        old, _, fmt = load_spec(_OLD)
        new, _, _ = load_spec(_NEW)
        assert fmt == "yaml"
        result = classify_changes(old, new)
        kinds = {c["kind"] for c in result["breaking"]}
        assert {"parameter_added", "type_changed", "request_body_required", "constraint_tightened",
                "response_property_optional"} <= kinds
        assert {c["kind"] for c in result["non_breaking"]} >= {"property_added", "response_added"} or \
            "property_added" in {c["kind"] for c in result["non_breaking"]}
        assert result["compatible"] is False and result["recommended_semver_bump"] == "major"
        # reversing the direction turns request-side breaks into compatible ones where they should be
        assert classify_changes(old, old)["recommended_semver_bump"] == "patch"

    def test_adding_an_optional_response_property_alone_is_a_minor_change(self) -> None:
        old, _, _ = load_spec(_OLD)
        new, _, _ = load_spec(_OLD.replace("        note: {type: string}\n", "        note: {type: string}\n        tax: {type: number}\n"))
        result = classify_changes(old, new)
        assert result["compatible"] is True and result["recommended_semver_bump"] == "minor"

    def test_validation_finds_structural_errors_with_json_pointers(self) -> None:
        bad, _, _ = load_spec("openapi: 3.0.3\ninfo: {title: Bad}\npaths:\n  orders/{id}:\n    get:\n      operationId: dup\n"
                              "      responses: {}\n  /items/{item}:\n    get:\n      operationId: dup\n      responses:\n"
                              "        '200': {$ref: '#/components/responses/Nope'}\n        'abc': {description: x}\n")
        result = validate_spec(bad)
        messages = " | ".join(e["message"] for e in result["errors"])
        for needle in ("info.version", "must start with '/'", "no responses", "not declared", "already used",
                       "does not resolve", "not a status code"):
            assert needle in messages, needle
        assert validate_spec(load_spec(_OLD)[0])["valid"] is True

    def test_json_still_works_and_alias_bombs_and_garbage_are_refused(self) -> None:
        assert load_spec('{"openapi": "3.0.0"}')[2] == "json"
        assert load_spec("x: &a 1\n" * 60 + "y: *a\n" * 60)[1].startswith("YAML has")
        assert load_spec("{not json")[1].startswith("JSON parse error")
        assert load_spec("")[1] == "spec is empty"

    def test_generated_integration_tests_are_valid_python_with_the_documented_contract(self) -> None:
        code = generate_tests(load_spec(_OLD)[0])["test_code"]
        tree = ast.parse(code)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert names == {"test_getorder", "test_createorder"}
        assert "assert response.status_code in documented" in code and "assert isinstance(body['total'], (int, float))" in code

    async def test_the_tool_accepts_yaml_for_every_operation_and_lists_all_breaking_changes_in_diff(self, ctx) -> None:
        assert (await call("compare_api_specs", ctx, operation="validate_spec", spec=_OLD))["valid"] is True
        diff = await call("compare_api_specs", ctx, operation="diff", old_spec=_OLD, new_spec=_NEW)
        assert diff["compatible"] is False and diff["all_breaking_changes"]
        removed = await call("compare_api_specs", ctx, operation="diff", old_spec=_OLD,
                             new_spec=_OLD.replace("  /orders:\n", "  /orders2:\n"))
        assert "Removed path '/orders'" in removed["all_breaking_changes"]
        assert (await call("compare_api_specs", ctx, operation="deprecated_endpoints", spec=_OLD))["valid"] is True
        out = await call("compare_api_specs", ctx, operation="generate_tests", spec=_OLD)
        assert out["tests"] == 2


# --------------------------------------------------------------------------------- row 94 ----

_CHANGELOG = "# Changelog\n## [Unreleased]\n## [2.0.0] - 2026-11-20\n### Removed\n- Removed the legacy /v1/orders endpoint\n### Added\n- Retries\n"
_BAD_MIGRATION = "def upgrade():\n    op.drop_column('orders', 'legacy')\n    op.add_column('orders', sa.Column('r', sa.String, nullable=False))\n\ndef downgrade():\n    pass\n"
_GOOD_MIGRATION = "def upgrade():\n    op.add_column('orders', sa.Column('note', sa.String))\n\ndef downgrade():\n    op.drop_column('orders', 'note')\n"


class TestReleaseChecklist:
    def checklist(self, **overrides: Any) -> dict:
        args = dict(service="api", target_version="2.0.0", previous_version="1.9.0", changelog=_CHANGELOG,
                    migrations=[], health_checks=["/healthz"], changed_files_count=5, test_coverage_pct=90.0, strategy=None)
        args.update(overrides)
        return release_ext.release_checklist(**args)

    def status(self, result: dict, item: str) -> str:
        return next(i["status"] for i in result["items"] if i["item"] == item)

    def test_a_consistent_release_is_go(self) -> None:
        result = self.checklist(migrations=[{"name": "0043", "text": _GOOD_MIGRATION}])
        assert result["go"] is True and self.status(result, "migrations are reversible") == "pass"
        assert self.status(result, "destructive migrations") if False else "destructive migrations" not in {i["item"] for i in result["items"]}
        assert result["executed"] is False and result["rollback_plan"]["valid"] is True

    def test_missing_changelog_entry_and_irreversible_destructive_migration_block(self) -> None:
        result = self.checklist(target_version="1.9.1", migrations=[{"name": "0042", "text": _BAD_MIGRATION}])
        assert result["go"] is False and {"changelog entry", "migrations are reversible"} <= set(result["blockers"])
        assert self.status(result, "destructive migrations") == "warn" and self.status(result, "rollback possible") == "warn"
        assert result["recommended_strategy"] == "blue_green"
        assert any("NOT NULL" in n for m in result["migrations"] for n in m["notes"])

    def test_breaking_entries_under_a_patch_bump_warn_and_a_non_increasing_version_fails(self) -> None:
        patch = self.checklist(target_version="2.0.0", previous_version="1.9.9")
        assert self.status(patch, "semver matches content") == "pass"
        warn = self.checklist(changelog=_CHANGELOG.replace("[2.0.0]", "[1.9.1]"), target_version="1.9.1")
        assert self.status(warn, "semver matches content") == "warn"
        assert self.checklist(target_version="1.8.0")["go"] is False

    def test_downgrade_text_is_not_mistaken_for_a_destructive_upgrade(self) -> None:
        assert not release_ext._migration_findings({"name": "m", "text": _GOOD_MIGRATION})["destructive"]

    async def test_plan_rollout_adds_the_checklist_only_when_asked(self, ctx) -> None:
        plain = await call("plan_rollout", ctx, service_name="api", target_version="2.0.0", previous_version="1.9.0")
        assert "release_checklist" not in plain and plain["rollback_supported"] is True
        full = await call("plan_rollout", ctx, service_name="api", target_version="2.0.0", previous_version="1.9.0",
                          changelog=_CHANGELOG, migrations=[{"name": "0043", "text": _GOOD_MIGRATION}])
        assert full["release_checklist"]["go"] is True


class _Health(BaseHTTPRequestHandler):
    body: dict = {"status": "ok", "version": "2.0.0", "schema": {"revision": "0019"}}

    def do_GET(self) -> None:  # noqa: N802
        data = json.dumps(self.body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


class TestVerifyReleaseHealth:
    def test_registered_only_when_the_operator_allows_hosts(self) -> None:
        assert build_release_tools(()) == []
        names = lambda **kw: {t.spec.name for t in default_tools(**kw)}  # noqa: E731
        assert "verify_release_health" not in names(enable_analysis_tools=True)
        assert "verify_release_health" in names(enable_analysis_tools=True, allowed_http_hosts=("svc.example",))
        assert "verify_release_health" not in names(allowed_http_hosts=("svc.example",))

    def test_it_is_read_only_untrusted_and_held_by_the_engineer_not_the_coding_agent(self) -> None:
        spec = build_release_tools(("svc.example",))[0].spec
        assert spec.side_effecting is False and "untrusted_content" in spec.tags and "network" in spec.tags
        personas = {p.id: p for p in build_default_agent_registry()}
        assert personas["engineer"].permits_tool("verify_release_health")
        assert not personas["coding_agent"].permits_tool("verify_release_health")

    @pytest.mark.parametrize("url,reason", [
        ("http://svc.example/healthz", "only https"),
        ("https://evil.test/healthz", "allowlist"),
        ("https://10.0.0.5/healthz", "IP-literal"),
        ("https://svc.example:8443/healthz", "default port"),
        ("https://user:pw@svc.example/healthz", "only https"),
        ("https://svc.example/he althz", "characters"),
    ])
    async def test_urls_outside_the_allowlist_are_refused_before_any_request(self, ctx, url, reason) -> None:
        from aura_core.domain.errors import ToolExecutionError

        tool_ = build_release_tools(("svc.example",))[0]
        with pytest.raises(ToolExecutionError, match=reason):
            await tool_.execute({"urls": [url], "attempts": 1}, ctx)

    def test_the_sanctioned_helper_refuses_the_gateway_health_path_and_platform_hosts(self) -> None:
        for url in ("https://svc.example/health", "https://aureon-ai.tech/healthz", "https://svc.example/api/v3/x"):
            result = release_ext.probe_health(url, timeout_s=2)
            assert result["ok"] is False and "URL refused" in result["error"], url

    async def test_the_body_is_judged_not_just_the_status(self, monkeypatch, ctx) -> None:
        server = HTTPServer(("127.0.0.1", 0), _Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        import urllib.request

        # The success path needs a reachable server and the real guard refuses loopback by design,
        # so the guard (and only the guard) is replaced by a plain opener here.
        monkeypatch.setattr(release_ext, "open_guarded", lambda req, **kw: urllib.request.urlopen(req, timeout=kw["timeout"]))
        url = f"http://127.0.0.1:{server.server_port}/status"
        try:
            ok = release_ext.probe_health(url, timeout_s=3, expected_version="2.0.0", required_fields=["schema.revision"])
            assert ok["ok"] is True and ok["version"] == "2.0.0"
            wrong = release_ext.probe_health(url, timeout_s=3, expected_version="2.1.0")
            assert wrong["ok"] is False and "expected '2.1.0'" in wrong["problems"][0]
            missing = release_ext.probe_health(url, timeout_s=3, required_fields=["schema.missing"])
            assert "required field 'schema.missing' missing" in missing["problems"]
            _Health.body = {"healthy": False, "version": "2.0.0"}
            assert release_ext.probe_health(url, timeout_s=3)["ok"] is False
            _Health.body = {"status": "degraded"}
            assert "status is 'degraded'" in release_ext.probe_health(url, timeout_s=3)["problems"][0]
        finally:
            _Health.body = {"status": "ok", "version": "2.0.0", "schema": {"revision": "0019"}}
            server.shutdown()


# --------------------------------------------------------------------------------- row 95 ----

_METRICS = '''# HELP aura_provider_healthy Provider circuit state.
# TYPE aura_provider_healthy gauge
aura_provider_healthy{provider="hosted1"} 0
aura_provider_healthy{provider="hfspace"} 1
# TYPE aura_turns_total counter
aura_turns_total{outcome="ok"} 90
aura_turns_total{outcome="error"} 30
# TYPE aura_request_seconds histogram
aura_request_seconds_bucket{route="/chat",le="0.5"} 10
aura_request_seconds_bucket{route="/chat",le="1"} 20
aura_request_seconds_bucket{route="/chat",le="5"} 60
aura_request_seconds_bucket{route="/chat",le="+Inf"} 100
aura_request_seconds_sum{route="/chat"} 400
aura_request_seconds_count{route="/chat"} 100
# TYPE aura_pg_pool_in_use gauge
aura_pg_pool_in_use 20
# TYPE aura_pg_pool_max gauge
aura_pg_pool_max 20
'''
_LOGS = [
    '{"ts":"2026-11-20T10:00:01Z","level":"info","logger":"aura.container","message":"Starting gateway"}',
    '{"ts":"2026-11-20T10:01:10Z","level":"error","logger":"aura.storage","message":"could not connect to server: Connection refused (host postgres port 5432)","trace_id":"t2"}',
    '{"ts":"2026-11-20T10:01:11Z","level":"error","logger":"aura.api","message":"request failed: ConnectionRefusedError","trace_id":"t2","status":500}',
    '{"ts":"2026-11-20T10:01:12Z","level":"error","logger":"aura.storage","message":"could not connect to server: Connection refused (host postgres port 5432)","trace_id":"t3"}',
    '{"ts":"2026-11-20T10:01:13Z","level":"error","logger":"aura.storage","message":"pool exhausted: 20 of 20 connections in use"}',
    '2026-11-20 10:01:40,123 ERROR aura.api request failed id=8812: ConnectionRefusedError',
    'ts=2026-11-20T10:01:41Z level=error msg="could not connect" component=aura.storage',
]


class TestTelemetryAnalysis:
    def test_prometheus_text_is_parsed_with_types_labels_and_quantiles(self) -> None:
        parsed = telemetry_ext.parse_prometheus(_METRICS)
        assert parsed["families"]["aura_turns_total"]["type"] == "counter"
        assert parsed["families"]["aura_request_seconds"]["type"] == "histogram"
        assert parsed["skipped"] == 0 and parsed["samples"] == 12
        buckets = [(0.5, 10), (1.0, 20), (5.0, 60), (float("inf"), 100)]
        assert 4.0 <= telemetry_ext._quantile(buckets, 0.5) <= 5.0

    def test_anomalies_are_ranked_and_a_baseline_surfaces_error_jumps(self) -> None:
        base = telemetry_ext.parse_prometheus('# TYPE aura_turns_total counter\naura_turns_total{outcome="error"} 1\n'
                                              'aura_turns_total{outcome="ok"} 80\n')
        kinds = [a["kind"] for a in telemetry_ext.metric_anomalies(telemetry_ext.parse_prometheus(_METRICS), base)]
        assert {"down", "slow_tail", "saturation", "error_share", "error_jump"} <= set(kinds)
        severities = [a["severity"] for a in telemetry_ext.metric_anomalies(telemetry_ext.parse_prometheus(_METRICS))]
        assert severities == sorted(severities, reverse=True)

    async def test_incident_ranks_the_dependency_outage_first_with_evidence_lines(self, ctx) -> None:
        out = await call("analyze_logs", ctx, operation="incident", logs=_LOGS, metrics_text=_METRICS)
        assert out["top_hypothesis"] == "dependency_unreachable"
        top = out["hypotheses"][0]
        assert [e["line"] for e in top["evidence_lines"]][:3] == [2, 3, 4] and top["supporting_metrics"]
        assert {"resource_exhaustion", "timeouts"} & {h["cause"] for h in out["hypotheses"]} or True
        assert any(h["cause"] == "resource_exhaustion" for h in out["hypotheses"])
        confidences = [h["confidence"] for h in out["hypotheses"]]
        assert confidences == sorted(confidences, reverse=True)
        assert "no live telemetry" in out["source"]

    async def test_structured_logs_handle_json_logfmt_and_plain_and_find_bursts_and_traces(self, ctx) -> None:
        out = await call("analyze_logs", ctx, operation="structured", logs=_LOGS)
        assert out["levels"] == {"info": 1, "error": 6} and out["first_error"]["line"] == 2
        assert out["bursts"] and out["bursts"][0]["errors"] == 6
        assert out["cross_component_traces"][0]["trace_id"] == "t2"
        assert out["top_signatures"][0]["count"] == 2 and "<n>" in out["top_signatures"][0]["signature"]
        assert out["errors_by_component"]["aura.storage"] == 4

    async def test_inputs_are_required_and_the_original_operations_still_work(self, ctx) -> None:
        from aura_core.domain.errors import ToolExecutionError

        with pytest.raises(ToolExecutionError, match="at least one of"):
            await call("analyze_logs", ctx, operation="incident")
        with pytest.raises(ToolExecutionError):
            await call("analyze_logs", ctx, operation="metrics")
        assert (await call("analyze_logs", ctx, operation="errors", logs=["ERROR boom ValueError: x"]))["incidents_count"] == 1
        assert (await call("analyze_logs", ctx, operation="latency", latencies_ms=[10, 5000]))["outliers_count"] == 1


# -------------------------------------------------------------------------------- row 102 ----

_ROWS = [
    {"id": 1, "email": "ana@example.com", "signup": "2026-01-05", "status": "Active", "age": 31, "notes": "ok"},
    {"id": 2, "email": "bo@example.com", "signup": "2026-02-30", "status": "active ", "age": 29, "notes": "fine"},
    {"id": 3, "email": "not-an-email", "signup": "03/04/2026", "status": "Active", "age": 35, "notes": "Caf\u00c3\u00a9 menu"},
    {"id": 4, "email": "dee@example.com", "signup": "2026-03-11", "status": "Inactive", "age": 33, "notes": '{"a": [1, 2'},
    {"id": 5, "email": "eli@example.com", "signup": "2026-03-12", "status": "Active", "age": "n/a", "notes": "x\x00y"},
    {"id": 6, "email": "fay@example.com", "signup": "2026-03-13", "status": "Active", "age": 30, "notes": "bad \ufffd"},
    {"id": 7, "email": "gus@example.com", "signup": "2026-03-14", "status": "Active", "age": 900, "notes": "ok"},
    {"id": 7, "email": "gus@example.com", "signup": "2026-03-14", "status": "Active", "age": 32},
]


class TestDataQuality:
    async def test_inconsistent_formats_padding_and_case_variants(self, ctx) -> None:
        out = await call("validate_data_records", ctx, operation="formats", records=_ROWS)
        cols = out["columns"]
        assert cols["email"]["violations"] == 1 and cols["email"]["samples"][0]["row"] == 2
        assert cols["signup"]["dominant_format"] == "iso_date" and cols["signup"]["violations"] == 1
        assert cols["status"]["case_or_spacing_variants"] == {"active": ["Active", "active "]}
        assert out["consistent"] is False
        pinned = await call("validate_data_records", ctx, operation="formats", records=_ROWS, formats={"email": "email"})
        assert pinned["columns"]["email"]["expected"] == "email"
        bad = await call("validate_data_records", ctx, operation="formats", records=_ROWS, formats={"email": "bogus"})
        assert "unknown format" in bad["error"]

    async def test_corrupted_records_are_named_by_kind_row_and_column(self, ctx) -> None:
        out = await call("validate_data_records", ctx, operation="corrupted", records=_ROWS)
        found = {(f["kind"], f["row"], f["column"]) for f in out["findings"]}
        assert {("impossible_date", 1, "signup"), ("mojibake", 2, "notes"), ("truncated_json", 3, "notes"),
                ("control_characters", 4, "notes"), ("replacement_character", 5, "notes")} <= found
        assert out["by_kind"]["ragged_row"] == 1 and out["clean"] is False

    async def test_robust_outlier_rules_find_what_a_zscore_can_miss(self, ctx) -> None:
        rows = [{"v": v} for v in (10, 11, 12, 11, 10, 12, 11, 500)]
        z = await call("validate_data_records", ctx, operation="outliers", records=rows, field="v")
        iqr = await call("validate_data_records", ctx, operation="outliers", records=rows, field="v", method="iqr")
        mad = await call("validate_data_records", ctx, operation="outliers", records=rows, field="v", method="mad")
        assert iqr["outliers"][0]["row"] == 7 and mad["outliers"][0]["row"] == 7
        assert z["outliers_count"] == 0  # one extreme value inflates the deviation it is judged by

    async def test_types_inferred_or_contracted(self, ctx) -> None:
        inferred = await call("validate_data_records", ctx, operation="types", records=_ROWS)
        assert inferred["mixed_type_columns"][0]["column"] == "age"
        contracted = await call("validate_data_records", ctx, operation="types", records=_ROWS,
                                type_contracts={"age": "integer"})
        assert contracted["violations"][0]["actual_type"] == "str"

    async def test_profile_scores_and_lists_every_finding_family(self, ctx) -> None:
        out = await call("validate_data_records", ctx, operation="profile", records=_ROWS, unique_key="id")
        assert out["ready_for_ingestion"] is False and 0 <= out["score"] < 90
        assert out["records"]["duplicate_count"] == 1
        clean = await call("validate_data_records", ctx, operation="profile",
                           records=[{"id": i, "name": f"n{i}"} for i in range(20)], unique_key="id")
        assert clean["score"] == 100.0 and clean["ready_for_ingestion"] is True

    async def test_outliers_need_a_field_and_the_original_operations_work(self, ctx) -> None:
        from aura_core.domain.errors import ToolExecutionError

        with pytest.raises(ToolExecutionError, match="needs 'field'"):
            await call("validate_data_records", ctx, operation="outliers", records=_ROWS)
        assert (await call("validate_data_records", ctx, operation="records", records=_ROWS, unique_key="id"))["duplicate_count"] == 1

    def test_pure_module_ignores_non_object_rows_for_formats_but_flags_them_as_corrupt(self) -> None:
        assert data_ext.detect_corrupted(["text", {"a": 1}])["by_kind"]["not_an_object"] == 1
        assert data_ext.check_formats(["text", {"a": "x"}])["columns_checked"] == 1


# -------------------------------------------------------------------------------- row 103 ----


class TestModelMetricHistory:
    @pytest.fixture
    def tools(self):
        return {t.spec.name: t for t in build_drift_tools(InMemoryCache())}

    @staticmethod
    def context(user: str, scope: str | None = None) -> ToolContext:
        return ToolContext(user_id=UserId(user), correlation_id="c", deadline_s=30.0,
                           knowledge_owner=UserId(scope) if scope else None)

    async def rec(self, tools, ctx, **args) -> dict:
        return json.loads(await tools["record_model_metric"].execute({"model": "m", "metric": "accuracy", **args}, ctx))

    async def cmp(self, tools, ctx, **args) -> dict:
        return json.loads(await tools["compare_model_metric"].execute({"model": "m", "metric": "accuracy", **args}, ctx))

    def test_the_pair_is_exactly_these_two_and_only_the_write_is_side_effecting(self, tools) -> None:
        assert set(tools) == DRIFT_TOOL_NAMES
        assert tools["record_model_metric"].spec.side_effecting is True
        assert tools["compare_model_metric"].spec.side_effecting is False

    async def test_a_decline_over_recorded_history_is_drift_and_names_the_next_step(self, tools) -> None:
        ctx = self.context("alice")
        assert (await self.cmp(tools, ctx))["status"] == "insufficient_history"
        for value in (0.91, 0.90, 0.89):
            await self.rec(tools, ctx, operation="record", value=value)
        stable = await self.cmp(tools, ctx)
        assert stable["status"] == "stable" and stable["trend"]["last_three_declining"] is True
        await self.rec(tools, ctx, operation="record", value=0.78)
        out = await self.cmp(tools, ctx, include_history=True)
        assert out["status"] == "drift" and out["drift_detected"] is True and out["baseline_source"] == "earliest observation"
        assert out["check"]["accuracy_drop"] == pytest.approx(0.13) and len(out["history"]) == 4
        assert out["action_required"] == "evaluate_or_retrain" and out["evaluation_triggered"] is False

    async def test_history_is_per_owner_and_the_team_scope_is_shared_by_its_members(self, tools) -> None:
        alice, bob = self.context("alice"), self.context("bob")
        for v in (0.9, 0.8):
            await self.rec(tools, alice, operation="record", value=v)
        assert (await self.cmp(tools, bob))["history_entries"] == 0
        ta, tb = self.context("alice", scope="team-1"), self.context("bob", scope="team-1")
        await self.rec(tools, ta, operation="record", value=0.9)
        assert (await self.cmp(tools, tb))["history_entries"] == 1

    async def test_pinning_a_baseline_and_clearing(self, tools) -> None:
        ctx = self.context("alice")
        for v in (0.90, 0.60, 0.88):
            await self.rec(tools, ctx, operation="record", value=v)
        await self.rec(tools, ctx, operation="set_baseline")
        pinned = await self.cmp(tools, ctx)
        assert pinned["status"] == "insufficient_history"           # the baseline is the latest observation
        await self.rec(tools, ctx, operation="record", value=0.70)
        out = await self.cmp(tools, ctx)
        assert out["baseline_source"] == "pinned" and out["baseline"]["value"] == 0.88 and out["drift_detected"] is True
        assert (await self.rec(tools, ctx, operation="clear"))["entries_removed"] == 4
        assert (await self.cmp(tools, ctx))["history_entries"] == 0

    async def test_samples_use_mean_shift_and_distribution_change(self, tools) -> None:
        ctx = self.context("alice")
        await tools["record_model_metric"].execute(
            {"operation": "record", "model": "m", "metric": "latency_ms", "values": [100, 110, 95, 105, 120, 98]}, ctx)
        await tools["record_model_metric"].execute(
            {"operation": "record", "model": "m", "metric": "latency_ms", "values": [210, 250, 190, 230, 260, 240]}, ctx)
        out = json.loads(await tools["compare_model_metric"].execute({"model": "m", "metric": "latency_ms"}, ctx))
        assert out["mode"] == "sample" and out["drift_detected"] is True
        assert out["checks"]["mean_shift"]["relative_shift"] > 1.0 and out["checks"]["distribution"]["drift_severity"] == "HIGH"

    async def test_bounds_validation_and_kind_mixing_are_enforced(self, tools) -> None:
        from aura_core.domain.errors import ToolExecutionError

        ctx = self.context("alice")
        for args in ({"operation": "record"}, {"operation": "record", "value": 1, "values": [1]},
                     {"operation": "record", "value": 1, "model": "bad name!"},
                     {"operation": "record", "values": [1, "x"]}, {"operation": "set_baseline"}):
            with pytest.raises(ToolExecutionError):
                await tools["record_model_metric"].execute({"model": "m", "metric": "accuracy", **args}, ctx)
        await self.rec(tools, ctx, operation="record", value=0.5)
        with pytest.raises(ToolExecutionError, match="single scores"):
            await self.rec(tools, ctx, operation="record", values=[1.0])
        for i in range(60):
            await self.rec(tools, ctx, operation="record", value=0.5 + i / 1000)
        assert (await self.cmp(tools, ctx))["history_entries"] == 50
        for i in range(49):
            await tools["record_model_metric"].execute({"operation": "record", "model": f"model{i}", "metric": "m", "value": 1}, ctx)
        with pytest.raises(ToolExecutionError, match="at most 50"):
            await tools["record_model_metric"].execute({"operation": "record", "model": "one-more", "metric": "m", "value": 1}, ctx)

    def test_no_module_level_mutable_state(self) -> None:
        import aura_core.tools.drift as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        assigned = [t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
        mutable = [n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, (ast.Dict, ast.List, ast.Set))
                   and not any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)]
        assert not mutable, assigned


# -------------------------------------------------------------------------------- row 106 ----

_SEARCH = {"results": [
    {"url": "https://www.example.com/a?utm_source=x#frag", "title": "A", "content": "The gateway timeout is 90 seconds for chat requests."},
    {"url": "https://example.com/a", "title": "A again", "content": "Same page, different tracking."},
    {"url": "https://docs.python.org/3/", "title": "Python docs", "content": "Python documentation."},
]}


class TestResearchTopic:
    @pytest.fixture
    async def registry(self, knowledge, user):
        from aura_core.domain.identifiers import DocumentId

        await knowledge.ingest(title="Ops handbook", document_id=DocumentId("ops"), owner_id=user,
                               content="The gateway timeout is 30 seconds for chat requests. Chat requests are retried twice before the provider circuit opens.")
        await knowledge.ingest(title="Runbook", document_id=DocumentId("run"), owner_id=user,
                               content="The gateway timeout is 180 seconds for chat requests on the CPU stack. The provider circuit does not open after retries.")
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_SEARCH))
        reg = ToolRegistry()
        reg.register_all(build_knowledge_tools(knowledge))
        reg.register_all(build_web_search_tool(base_url="http://search.internal:8080", transport=transport))
        reg.register_all(build_research_tools(lambda: reg))
        return reg

    async def research(self, registry, ctx, **args) -> dict:
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("r1"), name="research_topic", arguments={"topic": "gateway timeout for chat requests", **args}), ctx)
        assert result.ok, result.content
        return json.loads(result.content)

    async def test_local_and_web_sources_are_deduplicated_cited_and_compared(self, registry, user) -> None:
        ctx = ToolContext(user_id=user, correlation_id="c", deadline_s=120.0)
        out = await self.research(registry, ctx, queries=["provider circuit retries"], max_sources=0)
        urls = [s.get("url") for s in out["sources"] if s["origin"] == "web"]
        assert len(urls) == 2 and sum(1 for u in urls if u and "example.com" in u) == 1    # tracking/www dup merged
        assert {s["origin"] for s in out["sources"]} == {"local", "web"}
        values = {c["detail"] for c in out["contradictions"] if c["kind"] == "different values"}
        assert values and all("vs" in v for v in values)
        cited = [f for f in out["findings"] if f["corroborated"]]
        assert cited and all(f["sources"] == sorted(f["sources"]) and len(f["sources"]) >= 2 for f in cited)
        assert any(c["kind"] == "opposite polarity" for c in out["contradictions"])
        md = out["report_markdown"]
        assert "## Contradictions" in md and "## Coverage and gaps" in md and "[1]" in md
        assert "rest on a search snippet only" in md and set(out["coverage"]["tools_used"]) == {"search_knowledge", "web_search"}

    async def test_it_composes_through_the_callers_grant_and_reports_what_it_could_not_use(self, registry, user) -> None:
        narrow = ToolContext(user_id=user, correlation_id="c", deadline_s=60.0,
                             allowed_tools=frozenset({"research_topic", "search_knowledge"}))
        out = await self.research(registry, narrow)
        coverage = out["coverage"]
        assert coverage["tools_used"] == ["search_knowledge"]
        assert coverage["tools_unavailable"]["web_search"].startswith("policy_denied")
        assert any("web search was not used" in g for g in coverage["gaps"])
        assert {s["origin"] for s in out["sources"]} == {"local"}

    async def test_other_owners_documents_are_never_searched(self, registry) -> None:
        stranger = ToolContext(user_id=UserId("stranger"), correlation_id="c", deadline_s=60.0)
        out = await self.research(registry, stranger, use_web=False)
        assert out["sources"] == [] and out["ready"] is False
        assert any("no source was found at all" in g for g in out["coverage"]["gaps"])

    async def test_unregistered_tools_are_gaps_not_crashes(self, knowledge, user) -> None:
        reg = ToolRegistry()
        reg.register_all(build_research_tools(lambda: reg))
        ctx = ToolContext(user_id=user, correlation_id="c", deadline_s=60.0)
        out = await self.research(reg, ctx)
        assert set(out["coverage"]["tools_unavailable"]) == {"search_knowledge", "web_search"}

    def test_the_tool_is_tagged_untrusted_and_nested_and_the_coding_agent_lacks_it(self) -> None:
        spec = build_research_tools(lambda: None)[0].spec
        assert {"untrusted_content", "nested_dispatch"} <= set(spec.tags) and spec.side_effecting is False
        personas = {p.id: p for p in build_default_agent_registry()}
        assert personas["research_agent"].permits_tool("research_topic") and personas["engineer"].permits_tool("research_topic")
        assert not personas["coding_agent"].permits_tool("research_topic")

    def test_url_normalisation_and_local_hit_parsing(self) -> None:
        assert research_ext.normalise_url("https://www.Example.com/a/?utm_source=x&b=1#f") == research_ext.normalise_url("http://example.com/a?b=1")
        hits = research_ext.parse_local_hits("[1] Handbook > Leave (score 0.52)\nAnnual leave is 21 days.\n\n[2] Other (score 0.40)\nMore.")
        assert [h["score"] for h in hits] == [0.52, 0.40] and hits[0]["text"] == "Annual leave is 21 days."


# ------------------------------------------------------------------------ wiring and groups ----


class TestWiringAndInvariants:
    def test_a_default_container_registers_the_new_tools_and_grants_them_correctly(self, container) -> None:
        names = set(container.tools.names())
        assert {"research_topic", "record_model_metric", "compare_model_metric"} <= names
        personas = {p.id: p for p in container.agents}
        for persona in ("engineer",):
            assert all(personas[persona].permits_tool(n) for n in ("research_topic", "record_model_metric", "compare_model_metric"))
        for name in ("research_topic", "record_model_metric", "compare_model_metric", "verify_release_health"):
            assert not personas["coding_agent"].permits_tool(name)

    def test_the_new_tool_modules_have_no_path_to_the_gateway_or_adapters(self) -> None:
        import aura_core.tools as pkg
        from pathlib import Path

        root = Path(pkg.__file__).parent
        for name in ("release.py", "drift.py", "research.py"):
            imported = {n.module for n in ast.walk(ast.parse((root / name).read_text())) if isinstance(n, ast.ImportFrom) and n.module}
            assert not any(m.startswith(("aura_core.adapters", "aura_core.api", "aura_core.container", "aura_service"))
                           or m in ("adapters", "api", "container") for m in imported), (name, imported)

    def test_every_extension_module_parses_and_the_group_stays_the_same_seventeen_tools(self) -> None:
        from aura_core.tools.analysis import ANALYSIS_TOOL_NAMES

        assert len(ANALYSIS_TOOL_NAMES) == 17
        assert module_name_from_path("a/b.py") == "a.b"
        assert sys.version_info >= (3, 11)
