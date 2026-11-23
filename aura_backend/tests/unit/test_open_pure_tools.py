"""ADR-042: the fourteen pure tools of ``tools/open_pure.py``.

Every tool is called through ``Tool.execute`` (what the kernel does) and, for the registration and
grant claims, through a real ``Container``. The group's invariants -- no capability, no side effect,
no I/O, no process-global state, no untrusted-content tag -- are pinned so a later tool cannot join
it with a different risk profile.
"""

from __future__ import annotations

import ast
import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from aura_core.agents.registry import build_default_agent_registry
from aura_core.config import load_settings
from aura_core.container import Container
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.ports.runtime import ToolContext
from aura_core.tools.builtin import default_tools
from aura_core.tools.open_pure import OPEN_PURE_TOOL_NAMES, build_open_pure_tools

_TOOLS = {t.spec.name: t for t in build_open_pure_tools()}
_HERE = Path(__file__).resolve().parents[2] / "aura_core" / "tools"
_MODULES = [_HERE / "open_pure.py", _HERE / "open_pure_logic.py"]


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=30.0)


async def call(name: str, ctx: ToolContext, **arguments) -> dict:
    return json.loads(await _TOOLS[name].execute(arguments, ctx))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# --------------------------------------------------------------------------------------
# The group
# --------------------------------------------------------------------------------------


class TestGroupInvariants:
    def test_the_group_is_exactly_these_fourteen_tools(self) -> None:
        assert OPEN_PURE_TOOL_NAMES == {
            "check_architecture", "detect_ambiguity", "verify_acceptance_criteria", "check_diff_scope",
            "explain_source", "generate_quiz", "generate_artifact", "collect_verification_evidence",
            "apply_plan_feedback", "summarize_for_mobile", "detect_file_type", "parse_structured_data",
            "analyze_source_structure", "validate_workflow",
        }

    @pytest.mark.parametrize("name", sorted(OPEN_PURE_TOOL_NAMES))
    def test_a_pure_tool_has_no_capability_no_side_effect_and_no_taint(self, name: str) -> None:
        spec = _TOOLS[name].spec
        assert spec.side_effecting is False
        assert spec.required_capability is None
        assert spec.requires_confirmation is False
        assert "untrusted_content" not in spec.tags
        assert spec.workspace_local is False

    @pytest.mark.parametrize("name", sorted(OPEN_PURE_TOOL_NAMES))
    def test_every_string_parameter_is_bounded(self, name: str) -> None:
        for p in _TOOLS[name].spec.parameters:
            if p.type == "string" and p.enum is None:
                assert p.max_length is not None, f"{name}.{p.name} has no max_length"

    def test_no_name_collides_with_an_existing_tool(self) -> None:
        others = {t.spec.name for t in default_tools(enable_analysis_tools=True, enable_open_exec_tools=True)}
        assert not OPEN_PURE_TOOL_NAMES & others

    def test_the_group_is_opt_in_at_assembly_and_on_by_default_in_a_deployment(self) -> None:
        assert not OPEN_PURE_TOOL_NAMES & {t.spec.name for t in default_tools()}
        assert OPEN_PURE_TOOL_NAMES <= {t.spec.name for t in default_tools(enable_open_pure_tools=True)}
        assert load_settings(refresh=True, validate=False).open_tools.pure_enabled is True

    def test_the_setting_turns_it_off(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_OPEN_PURE_TOOLS_ENABLED", "false")
        try:
            settings = load_settings(refresh=True, validate=False)
            assert settings.open_tools.pure_enabled is False
            names = {s.name for s in Container(settings).tools.available(capabilities=("engineer_agent",))}
            assert not OPEN_PURE_TOOL_NAMES & names
        finally:
            monkeypatch.delenv("AURA_OPEN_PURE_TOOLS_ENABLED")
            load_settings(refresh=True, validate=False)

    @pytest.mark.parametrize("path", _MODULES, ids=lambda p: p.name)
    def test_the_modules_cannot_reach_the_gateway_a_process_or_the_network(self, path: Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = ("aura_core.api", "aura_core.container", "aura_core.adapters", "aura_core.config", "aura_core.auth",
                     "aura_core.kernel", "aura_service", "fastapi", "starlette", "subprocess", "socket", "urllib",
                     "http", "requests", "httpx", "aiohttp", "os", "shutil", "pathlib", "importlib", "ctypes")
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")]
                if node.level == 2:  # from ..x import: only the error and tool-spec types
                    assert (node.module or "") in ("domain.errors", "domain.tools", "ports.runtime"), names
            for name in names:
                root = name.lstrip(".")
                assert not any(root == f or root.startswith(f + ".") for f in forbidden), f"{path.name} imports {name}"
        source = path.read_text(encoding="utf-8")
        for needle in ("open(", "eval(", "exec(", "__import__", "os.system", "Popen"):
            assert needle not in source.replace("reopen(", ""), f"{path.name} contains {needle!r}"

    @pytest.mark.parametrize("path", _MODULES, ids=lambda p: p.name)
    def test_no_module_level_mutable_state(self, path: Path) -> None:
        """A list/dict/set at module level must be an UPPER_CASE or underscore-private constant that is
        never mutated -- there is nowhere to keep one user's data for another to read."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        assert isinstance(t, ast.Name) and t.id.lstrip("_").isupper() or t.id == "__all__", (path.name, ast.dump(t))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.Global), f"{path.name} uses 'global'"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                # a module constant mutated from inside a function would be shared state
                assert not (node.func.value.id.isupper() and node.func.attr in ("append", "extend", "update", "add", "pop", "clear", "setdefault")), ast.dump(node)


# --------------------------------------------------------------------------------------
# Registration, grants and the real registry
# --------------------------------------------------------------------------------------


class TestThroughTheRealContainer:
    async def test_the_default_container_registers_all_fourteen_and_the_engineer_can_call_them(self) -> None:
        container = Container(load_settings(refresh=True, validate=False))
        await container.startup()
        registered = {s.name for s in container.tools.available()}
        assert OPEN_PURE_TOOL_NAMES <= registered  # no capability needed to see them
        engineer = container.agents.get("engineer")
        assert OPEN_PURE_TOOL_NAMES <= engineer.allowed_tools
        context = ToolContext(user_id=UserId("u-1"), correlation_id="c", deadline_s=30.0, agent_id="engineer",
                              allowed_tools=engineer.allowed_tools, allowed_tool_prefixes=engineer.allowed_tool_prefixes)
        result, report = await container.tools.execute(
            ToolCall(id=ToolCallId("t1"), name="validate_workflow",
                     arguments={"tasks": [{"id": "a"}, {"id": "b", "depends_on": ["a"]}]}),
            context, capabilities=("engineer_agent",),
        )
        assert result.ok and report.needs_confirmation is False
        assert json.loads(result.content)["order"] == ["a", "b"]

    def test_every_granted_name_is_registered_and_coding_agent_holds_no_untrusted_tool(self) -> None:
        specs = {t.spec.name: t.spec for t in default_tools(enable_open_pure_tools=True)}
        agents = build_default_agent_registry()
        for agent_id in ("engineer", "coding_agent", "task_planner", "research_agent"):
            granted = agents.get(agent_id).allowed_tools & OPEN_PURE_TOOL_NAMES
            assert granted, agent_id
            assert granted <= set(specs)
            assert not [n for n in granted if "untrusted_content" in specs[n].tags]
        assert not agents.get("decision_agent").allowed_tools & OPEN_PURE_TOOL_NAMES
        assert not agents.get("finance_advisor").allowed_tools & OPEN_PURE_TOOL_NAMES


# --------------------------------------------------------------------------------------
# Row 7 -- check_architecture
# --------------------------------------------------------------------------------------

_ARCH_FILES = [
    {"path": "aura_core/domain/order.py", "source_code": "from aura_core.adapters.pg import Repo\n\nclass order:\n    pass\n"},
    {"path": "aura_core/adapters/pg.py", "source_code": "from aura_core.domain.order import order\nfrom . import helpers\n"},
    {"path": "aura_core/adapters/helpers.py", "source_code": "from aura_core.adapters.pg import Repo\n"},
]


class TestCheckArchitecture:
    async def test_it_reads_imports_itself_and_finds_layer_violations_and_cycles(self, ctx) -> None:
        r = await call("check_architecture", ctx, files=_ARCH_FILES)
        assert r["is_consistent"] is False
        assert any("cannot import 'aura_core.adapters.pg'" in v for v in r["boundary_violations"])
        assert r["layer_violations"][0]["source_tier"] == 0 and r["layer_violations"][0]["target_tier"] == 3
        flat = [" ".join(c) for c in r["cycles"]]
        assert any("aura_core.domain.order" in c and "aura_core.adapters.pg" in c for c in flat)
        # the sibling cycle only exists through `from . import helpers`
        assert any("aura_core.adapters.helpers" in c for c in flat)

    async def test_conventions_are_reported_with_lines(self, ctx) -> None:
        r = await call("check_architecture", ctx, files=_ARCH_FILES)
        rules = {f["rule"] for f in r["convention_findings"]}
        assert "class-naming" in rules
        off = await call("check_architecture", ctx, files=_ARCH_FILES, check_conventions=False)
        assert "convention_findings" not in off

    async def test_a_clean_layering_is_consistent(self, ctx) -> None:
        files = [
            {"path": "aura_core/domain/order.py", "source_code": "from dataclasses import dataclass\n"},
            {"path": "aura_core/adapters/pg.py", "source_code": "from aura_core.domain.order import Order\n"},
        ]
        r = await call("check_architecture", ctx, files=files)
        assert r["is_consistent"] is True and r["cycles"] == [] and r["layer_violations"] == []

    async def test_custom_rules_and_tiers_apply_to_another_project(self, ctx) -> None:
        files = [{"path": "shop/models/item.py", "source_code": "import shop.views.page\n"},
                 {"path": "shop/views/page.py", "source_code": "import shop.models.item\n"}]
        default = await call("check_architecture", ctx, files=files)
        assert default["boundary_violations"] == []
        custom = await call("check_architecture", ctx, files=files, forbidden_rules={"models": ["views"]},
                            layer_tiers={"models": 0, "views": 1})
        assert custom["boundary_violations"] and custom["layer_violations"]

    async def test_javascript_imports_are_resolved_to_modules(self, ctx) -> None:
        files = [{"path": "web/ui/button.ts", "source_code": "import { x } from '../api/client';\n"},
                 {"path": "web/api/client.ts", "source_code": "import { b } from '../ui/button';\n"}]
        r = await call("check_architecture", ctx, files=files)
        assert r["cycles"], r
        assert r["languages"] == {"javascript/typescript": 2}

    async def test_an_import_map_alone_works_and_nothing_at_all_is_an_error(self, ctx) -> None:
        r = await call("check_architecture", ctx, import_map={"x.domain.a": ["x.api.b"]})
        assert r["layer_violations"]
        with pytest.raises(ToolExecutionError):
            await call("check_architecture", ctx)

    async def test_a_syntax_error_is_reported_not_raised(self, ctx) -> None:
        r = await call("check_architecture", ctx, files=[{"path": "a/b.py", "source_code": "def (:\n"}])
        assert r["parse_errors"] and r["parse_errors"][0]["file"] == "a/b.py"


# --------------------------------------------------------------------------------------
# Row 40 -- detect_ambiguity
# --------------------------------------------------------------------------------------


class TestDetectAmbiguity:
    async def test_faster_improve_and_ui_are_flagged_each_with_its_own_question(self, ctx) -> None:
        r = await call("detect_ambiguity", ctx, text="Make the app faster and improve the UI somehow, etc.")
        kinds = {f["kind"] for f in r["findings"]}
        assert {"unmeasured_comparative", "subjective_quality", "hedged_requirement", "open_ended_list"} <= kinds
        faster = next(f for f in r["findings"] if f["evidence"] == "faster")
        assert "baseline" in faster["question"] and faster["sentence"].startswith("Make the app")
        assert r["readiness_status"] == "NEEDS_CLARIFICATION"
        assert len({q["question"] for q in r["clarification_questions"]}) == len(r["clarification_questions"])

    async def test_a_measured_requirement_is_not_flagged_for_being_comparative(self, ctx) -> None:
        r = await call("detect_ambiguity", ctx, text="Make the search faster: p95 under 200 ms, down from 450 ms. Errors return 503. "
                       "Input is the query string; output is JSON. Requires a bearer token. Results are stored in postgres.")
        assert not [f for f in r["findings"] if f["kind"] == "unmeasured_comparative"]

    async def test_conflicts_between_requirements_become_questions(self, ctx) -> None:
        r = await call("detect_ambiguity", ctx, text="Spec.", requirements=[
            "The service must log every request body.", "The service must not log request bodies.",
            "API latency must be under 100 ms.", "API latency must be over 300 ms for the api."])
        types = {c["conflict_type"] for c in r["conflicts"]}
        assert {"Opposite polarity on the same action", "Incompatible numeric bounds"} <= types
        assert any(q["kind"] == "conflict" for q in r["clarification_questions"])

    async def test_missing_aspects_get_multiple_choice_questions(self, ctx) -> None:
        r = await call("detect_ambiguity", ctx, text="Build a small service that stores notes.")
        with_options = [q for q in r["clarification_questions"] if q.get("options")]
        assert with_options and all(len(q["options"]) >= 3 for q in with_options)
        settled = await call("detect_ambiguity", ctx, text="Build a small service that stores notes.",
                             known_context="Notes persist in postgres. Callers need a jwt token. Errors return 400. "
                                     "Target latency is 200 ms. The api endpoint is POST /notes.")
        assert len(settled["clarification_questions"]) < len(r["clarification_questions"])

    async def test_arabic_hedges_are_found(self, ctx) -> None:
        r = await call("detect_ambiguity", ctx, text="نريد نظاما أسرع وربما أفضل وغيرها")
        assert {f["kind"] for f in r["findings"]} >= {"unmeasured_comparative", "hedged_requirement"}

    async def test_the_result_says_it_is_heuristic(self, ctx) -> None:
        assert (await call("detect_ambiguity", ctx, text="x"))["heuristic"] is True


# --------------------------------------------------------------------------------------
# Row 41 -- verify_acceptance_criteria
# --------------------------------------------------------------------------------------


class TestVerifyAcceptanceCriteria:
    _SPEC = "## Acceptance criteria\n- [ ] Users can log in with email\n- [ ] Failed login returns 401\n- [ ] Tests pass\n"

    async def test_only_affirmative_evidence_passes_and_the_rest_is_named(self, ctx) -> None:
        r = await call("verify_acceptance_criteria", ctx, requirements_text=self._SPEC,
                       evidence={"Users can log in with email": True, "Failed login returns 401": "failed"})
        assert r["verified_count"] == 1 and r["total_criteria"] == 3
        assert r["contradicted_criteria"] == ["Failed login returns 401"]
        assert r["unverified_criteria"] == ["Tests pass"]
        assert r["can_mark_complete"] is False and r["status"] == "REJECTED_CRITERIA_PENDING"
        assert any("Failed login returns 401" in a for a in r["next_actions"])
        assert "PENDING" in r["badge_report_markdown"]

    async def test_everything_verified_means_complete(self, ctx) -> None:
        r = await call("verify_acceptance_criteria", ctx, requirements_text=self._SPEC,
                       evidence={"Users can log in with email": True, "Failed login returns 401": True},
                       test_output="=== 12 passed in 1.1s ===")
        assert r["all_satisfied"] and r["can_mark_complete"] and r["status"] == "ACCEPTED"

    async def test_failing_test_output_contradicts_a_tests_criterion(self, ctx) -> None:
        r = await call("verify_acceptance_criteria", ctx, criteria=["Tests pass"], test_output="3 failed, 9 passed in 2s")
        assert r["contradicted_criteria"] == ["Tests pass"] and r["can_mark_complete"] is False

    async def test_free_text_evidence_lines_and_coverage(self, ctx) -> None:
        r = await call("verify_acceptance_criteria", ctx, criteria=["Users can log in", "Coverage is at least 80%"],
                       evidence_text="PASS: Users can log in\n", test_summary={"passed": 5, "failed": 0, "coverage_percentage": 91})
        assert r["all_satisfied"] is True
        low = await call("verify_acceptance_criteria", ctx, criteria=["Coverage is at least 80%"],
                         test_summary={"passed": 5, "failed": 0, "coverage_percentage": 40})
        assert low["all_satisfied"] is False

    async def test_no_criteria_is_an_error_not_a_pass(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await call("verify_acceptance_criteria", ctx, evidence={"a": True})

    async def test_a_long_checklist_is_bounded_and_its_failures_are_still_named(self, ctx) -> None:
        criteria = [f"criterion number {i} must hold" for i in range(150)]
        evidence = {c: True for c in criteria[:-2]}
        r = await call("verify_acceptance_criteria", ctx, criteria=criteria, evidence=evidence)
        assert r["total_criteria"] == 150 and r["verified_count"] == 148 and r["breakdown_truncated"] is True
        assert r["unverified_criteria"] == criteria[-2:] and "truncated" not in r
        with pytest.raises(ToolExecutionError, match="at most 200 criteria"):
            await call("verify_acceptance_criteria", ctx, criteria=[f"c{i}" for i in range(201)])


# --------------------------------------------------------------------------------------
# Row 45 -- check_diff_scope
# --------------------------------------------------------------------------------------

_DIFF = """diff --git a/src/auth/login.py b/src/auth/login.py
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -1,3 +1,5 @@
 def login(user):
-    return None
+    if not user:
+        raise ValueError('no user')
+    return user
diff --git a/src/billing/invoice.py b/src/billing/invoice.py
--- a/src/billing/invoice.py
+++ b/src/billing/invoice.py
@@ -1,4 +1,4 @@
-a = 1
-b = 2
-c = 3
-d = 4
+a  =  1
+b  =  2
+c  =  3
+d  =  4
diff --git a/poetry.lock b/poetry.lock
--- a/poetry.lock
+++ b/poetry.lock
@@ -1 +1 @@
-x
+y
"""


class TestCheckDiffScope:
    async def test_files_the_task_does_not_account_for_are_flagged(self, ctx) -> None:
        r = await call("check_diff_scope", ctx, diff_text=_DIFF, task="Make login in src/auth/login.py reject an empty user")
        align = r["task_alignment"]
        assert align["alignment"] == "REVIEW_UNRELATED_OR_EXCESSIVE_CHANGES" and align["heuristic"] is True
        assert "src/billing/invoice.py" in align["unrelated_candidates"]
        assert "src/auth/login.py" not in align["unrelated_candidates"]
        reasons = {e["file"]: e["reason"] for e in align["excessive_changes"]}
        assert "reformat" in reasons["src/billing/invoice.py"] and "lock" in reasons["poetry.lock"]
        assert r["verdict"] == "REVIEW_UNRELATED_CHANGES" and r["needs_human_review"] is True

    async def test_a_change_that_matches_the_task_is_within_scope(self, ctx) -> None:
        diff = _DIFF.split("diff --git a/src/billing")[0]
        r = await call("check_diff_scope", ctx, diff_text=diff, task="Fix login in src/auth/login.py")
        assert r["verdict"] == "WITHIN_SCOPE" and r["within_scope"] is True

    async def test_a_test_for_a_related_file_is_related(self, ctx) -> None:
        diff = ("diff --git a/src/auth/login.py b/src/auth/login.py\n--- a/src/auth/login.py\n+++ b/src/auth/login.py\n@@ -1 +1 @@\n-x\n+y\n"
                "diff --git a/tests/test_login.py b/tests/test_login.py\n--- a/tests/test_login.py\n+++ b/tests/test_login.py\n@@ -1 +1 @@\n-x\n+y\n")
        r = await call("check_diff_scope", ctx, diff_text=diff, task="Fix the login flow in src/auth/login.py")
        assert r["task_alignment"]["alignment"] == "ALIGNED"

    async def test_forbidden_files_secrets_allowed_paths_and_size(self, ctx) -> None:
        diff = ('diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -0,0 +1 @@\n+API_KEY="sk-abcdefghijklmnopqrstuvwxyz0123"\n'
                "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-a\n+b\n")
        r = await call("check_diff_scope", ctx, diff_text=diff, allowed_paths=["src"], max_lines_changed=2)
        assert r["verdict"] == "SCOPE_VIOLATIONS_DETECTED" and r["within_scope"] is False
        joined = " ".join(r["violations"])
        assert "Forbidden file" in joined and "possible secret" in joined and "Out-of-scope" in joined and "exceeds" in joined
        assert r["secret_findings"] and "abcdef" not in json.dumps(r["secret_findings"])

    async def test_without_a_task_only_scope_is_checked_and_a_non_diff_is_reported(self, ctx) -> None:
        r = await call("check_diff_scope", ctx, diff_text=_DIFF)
        assert r["task_alignment"]["alignment"] == "NO_TASK_GIVEN"
        bad = await call("check_diff_scope", ctx, diff_text="hello world")
        assert bad["verdict"] == "UNPARSEABLE_DIFF"


# --------------------------------------------------------------------------------------
# Row 47 -- explain_source
# --------------------------------------------------------------------------------------

_BILLING = '''"""Billing."""
import json

class Invoice:
    """An invoice."""
    def total(self, items, tax):
        """Sum the items and add tax."""
        return sum(items) * (1 + tax)

def helper(x):
    """Round a number."""
    return round(x)

def load(path):
    """Load an invoice from disk."""
    return helper(json.load(open(path)))
'''


class TestExplainSource:
    async def test_study_guide_has_symbols_reading_order_and_review_questions(self, ctx) -> None:
        r = await call("explain_source", ctx, source_code=_BILLING, file_name="billing.py", operation="study_guide")
        names = [s["name"] for s in r["symbols"]]
        assert {"Invoice", "total", "helper", "load"} <= set(names)
        assert r["reading_order"].index("helper") < r["reading_order"].index("load")  # dependencies first
        load = next(s for s in r["symbols"] if s["name"] == "load")
        assert load["calls_in_file"] == ["helper"] and "helper" in load["review_question"]
        assert "## Symbol walkthrough" in r["study_guide_markdown"] and r["review_questions"]

    async def test_other_operations_pass_through(self, ctx) -> None:
        fn = await call("explain_source", ctx, source_code=_BILLING, operation="function", function_name="total")
        assert fn["parameters"] == ["items", "tax"]  # the class drops self
        docs = await call("explain_source", ctx, source_code=_BILLING, operation="docstrings")
        assert docs["catalog_count"] == 3
        arch = await call("explain_source", ctx, source_code=_BILLING, operation="architecture")
        assert "json" in arch["external_dependencies"]
        with pytest.raises(ToolExecutionError):
            await call("explain_source", ctx, source_code=_BILLING, operation="function")

    async def test_a_go_file_is_explained_from_its_declarations_and_markdown_from_headings(self, ctx) -> None:
        go = "package main\nimport \"fmt\"\ntype T struct{}\nfunc (t *T) Hello() { fmt.Println(1) }\n"
        r = await call("explain_source", ctx, source_code=go, file_name="main.go", operation="explain")
        assert {s["name"] for s in r["symbols"]} == {"T", "Hello"} and r["imports"] == ["fmt"]
        md = "# Title\nIntro text here.\n\n## Setup\nInstall it first.\n"
        guide = await call("explain_source", ctx, source_code=md, file_name="README.md", operation="study_guide")
        assert [o["heading"] for o in guide["outline"]] == ["Title", "Setup"] and "Section summaries" in guide["study_guide_markdown"]


# --------------------------------------------------------------------------------------
# Row 48 -- generate_quiz
# --------------------------------------------------------------------------------------

_QUIZ_SRC = '''import json

class Base:
    pass

class Store(Base):
    def put(self, key, value):
        """Write a value under a key in the store."""
        return None

class Cache:
    def get(self, key):
        """Read a value back from the cache by its key."""
        return None

async def fetch(url: str, retries: int = 3) -> str:
    """Download the page body from the given url."""
    if url:
        return url
    return ""

def parse(text: str) -> dict:
    """Parse the text into a dictionary of fields."""
    for line in text.splitlines():
        if line:
            pass
    return {}
'''


class TestGenerateQuiz:
    async def test_questions_are_varied_and_grounded_in_the_source(self, ctx) -> None:
        r = await call("generate_quiz", ctx, topic="storage", source_text=_QUIZ_SRC, num_questions=8)
        assert r["questions_count"] == 8 and r["basis"] == "python_ast"
        assert len(set(r["question_types"])) >= 4  # not one template
        assert len({q["question"] for q in r["questions"]}) == 8
        assert all(q["id"] for q in r["questions"]) and all(q.get("source_line") for q in r["questions"])
        letters = {v for k, v in r["answer_key"].items() if v in "ABCD"}
        assert len(letters) >= 2  # the right answer is not always the same slot
        assert "answer_key" not in json.dumps(r["questions"])  # answers are a separate field

    async def test_the_answer_key_is_correct_against_the_source(self, ctx) -> None:
        r = await call("generate_quiz", ctx, topic="s", source_text=_QUIZ_SRC, num_questions=20)
        by_id = {q["id"]: q for q in r["questions"]}
        for q in r["questions"]:
            key = r["answer_key"][str(q["id"])]
            if q["type"] == "which_class" and "`put`" in q["question"]:
                assert next(o for o in q["options"] if o.startswith(key + ")")) == f"{key}) Store"
            if q["type"] == "return_type" and "`fetch`" in q["question"]:
                assert next(o for o in q["options"] if o.startswith(key + ")")).endswith("str")
            if q["type"] == "true_false" and "`fetch`" in q["question"]:
                assert key == "True"
        assert by_id

    async def test_grading_round_trips(self, ctx) -> None:
        r = await call("generate_quiz", ctx, topic="s", source_text=_QUIZ_SRC, num_questions=6)
        perfect = await call("generate_quiz", ctx, operation="grade", answer_key=r["answer_key"], user_answers=r["answer_key"])
        assert perfect["score_percentage"] == 100.0 and perfect["passed"] is True
        wrong = {k: ("False" if v == "True" else "True" if v == "False" else ("A" if v != "A" else "B")) for k, v in r["answer_key"].items()}
        zero = await call("generate_quiz", ctx, operation="grade", answer_key=r["answer_key"], user_answers=wrong)
        assert zero["score_percentage"] == 0.0 and zero["review"]

    async def test_prose_gets_cloze_and_true_false_questions(self, ctx) -> None:
        text = ("The retry scheduler doubles the delay after every failed attempt until the ceiling is reached. "
                "The circuit breaker opens after five consecutive failures and rejects calls for thirty seconds. "
                "The scheduler persists pending jobs in postgres so a restart does not lose any work.")
        r = await call("generate_quiz", ctx, topic="resilience", source_text=text, num_questions=4)
        assert r["basis"] == "text" and {"cloze", "true_false"} <= set(r["question_types"])
        cloze = next(q for q in r["questions"] if q["type"] == "cloze")
        assert "_____" in cloze["question"] and cloze["source_excerpt"]

    async def test_comprehension_and_coding_challenge(self, ctx) -> None:
        comp = await call("generate_quiz", ctx, operation="comprehension", topic="s", source_text=_QUIZ_SRC, num_questions=4)
        assert comp["rubric"]["passing_score"] == 75.0 and set(comp["question_types"]) == {"true_false"}
        ch = await call("generate_quiz", ctx, operation="coding_challenge", topic="download", source_text=_QUIZ_SRC, function_name="fetch")
        assert ch["function_signature"] == "async def fetch(url: str, retries: int=3) -> str:"
        assert ch["requirements"] == ["Download the page body from the given url."]
        assert "asyncio.run(fetch(" in ch["test_template"] and ch["derived_from"]["reference_solution_withheld"] is True
        ast.parse(ch["starter_code"])

    async def test_nothing_to_quiz_on_is_an_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await call("generate_quiz", ctx, source_text="")
        with pytest.raises(ToolExecutionError):
            await call("generate_quiz", ctx, source_text="Short.")
        with pytest.raises(ToolExecutionError, match="does not parse"):
            await call("generate_quiz", ctx, source_text="def f(:\n    pass\nclass A:\n")
        with pytest.raises(ToolExecutionError):
            await call("generate_quiz", ctx, operation="grade", user_answers={"1": "A"})


# --------------------------------------------------------------------------------------
# Row 49 -- generate_artifact
# --------------------------------------------------------------------------------------


class TestGenerateArtifact:
    async def test_a_task_key_is_not_rendered_as_unnamed(self, ctx) -> None:
        r = await call("generate_artifact", ctx, artifact_type="task_list", title="Release",
                       items=[{"task": "Cut branch", "done": True}, {"title": "Tag", "completed": "false"}], item_names=["Announce"])
        assert "Unnamed" not in r["markdown_content"]
        assert r["total_tasks"] == 3 and r["completed_tasks"] == 1 and r["saved"] is False

    async def test_a_walkthrough_is_verified_only_when_every_step_records_passed(self, ctx) -> None:
        steps = [{"action": "Run tests", "command": "pytest", "expected": "all pass", "passed": True}]
        ok = await call("generate_artifact", ctx, artifact_type="walkthrough", title="t", changes_summary="x", verification_steps=steps)
        assert ok["verified"] is True
        partial = await call("generate_artifact", ctx, artifact_type="walkthrough", title="t", changes_summary="x",
                             verification_steps=[*steps, {"action": "Manual login"}])
        assert partial["verified"] is False and partial["verification_unrecorded"] == 1

    async def test_plan_and_progress_documents(self, ctx) -> None:
        plan = await call("generate_artifact", ctx, artifact_type="implementation_plan", title="p",
                          items=[{"name": "Schema", "description": "Add table", "dependencies": ["none"], "risks": "Medium"}])
        assert "### 1. Schema" in plan["markdown_content"]
        prog = await call("generate_artifact", ctx, artifact_type="progress_report", title="p", completed_items=["a"], pending_items=["b"], blockers=["c"])
        assert prog["completion_rate"] == 50.0 and prog["has_blockers"] is True

    async def test_a_document_with_nothing_to_render_is_an_error(self, ctx) -> None:
        for kind in ("task_list", "implementation_plan", "progress_report", "walkthrough"):
            with pytest.raises(ToolExecutionError):
                await call("generate_artifact", ctx, artifact_type=kind, title="t")


# --------------------------------------------------------------------------------------
# Row 50 -- collect_verification_evidence
# --------------------------------------------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (320).to_bytes(4, "big") + (200).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"


class TestCollectVerificationEvidence:
    async def _collect(self, ctx, task_id="t-1"):
        return await call("collect_verification_evidence", ctx, operation="collect", task_id=task_id, test_outputs=["8 passed in 1s"],
                          command_results=[{"command": "pytest", "exit_code": 0, "stdout": "8 passed API_KEY=sk-" + "a" * 24}])

    async def test_collect_add_verify_and_certificate_round_trip_with_the_dossier_passed_back(self, ctx) -> None:
        first = await self._collect(ctx)
        assert first["verdict"] == "PASSED" and "sk-aaaa" not in json.dumps(first)
        with_cmd = await call("collect_verification_evidence", ctx, operation="add_command", dossier=first["dossier"],
                              command="ruff check .", exit_code=0, stdout="ok")
        with_shot = await call("collect_verification_evidence", ctx, operation="add_content", dossier=with_cmd["dossier"],
                               label="login.png", content_base64=b64(_PNG))
        assert with_shot["recorded"]["width"] == 320 and with_shot["recorded"]["mime_type"] == "image/png"
        ok = await call("collect_verification_evidence", ctx, operation="verify", dossier=with_shot["dossier"],
                        expected_digest=with_shot["evidence_digest"])
        assert ok["status"] == "VERIFIED_UNTOUCHED"
        cert = await call("collect_verification_evidence", ctx, operation="certificate", dossier=with_shot["dossier"])
        assert "login.png" in cert["certificate_markdown"] and cert["verdict"] == "PASSED"

    async def test_an_edited_dossier_fails_integrity(self, ctx) -> None:
        first = await self._collect(ctx)
        tampered = {**first["dossier"], "task_id": "someone-else"}
        r = await call("collect_verification_evidence", ctx, operation="verify", dossier=tampered, expected_digest=first["evidence_digest"])
        assert r["valid"] is False and r["status"] == "INTEGRITY_COMPROMISED"

    async def test_a_failing_command_fails_the_verdict_and_no_evidence_is_inconclusive(self, ctx) -> None:
        first = await self._collect(ctx)
        failed = await call("collect_verification_evidence", ctx, operation="add_command", dossier=first["dossier"],
                            command="mypy .", exit_code=1, stderr="error: x")
        assert failed["verdict"] == "FAILED"
        empty = await call("collect_verification_evidence", ctx, operation="collect")
        assert empty["verdict"] == "INCONCLUSIVE"

    async def test_a_test_log_can_be_recorded_as_test_output_and_secrets_are_scrubbed(self, ctx) -> None:
        first = await call("collect_verification_evidence", ctx, operation="collect", command_results=[{"command": "true", "exit_code": 0}])
        r = await call("collect_verification_evidence", ctx, operation="add_content", dossier=first["dossier"], kind="test_output",
                       content="12 passed in 3s\nTOKEN=abcdefghijk123456")
        assert "abcdefghijk123456" not in json.dumps(r) and "12 passed" in json.dumps(r["dossier"]["test_outputs"])

    async def test_bad_input_is_a_clear_error(self, ctx) -> None:
        for args in ({"operation": "verify"}, {"operation": "add_command", "dossier": {"a": 1}}, {"operation": "certificate"},
                     {"operation": "add_content", "dossier": {"a": 1}, "content_base64": "!!!"}):
            with pytest.raises(ToolExecutionError):
                await call("collect_verification_evidence", ctx, **args)

    async def test_one_users_dossier_cannot_be_reached_or_forged_by_another(self, ctx) -> None:
        """No state lives here: B can only hold A's dossier if A hands it over, and cannot make B's own
        dossier verify against A's digest (the IDOR check for ADR-042's two stateful-looking tools)."""
        a_ctx = ToolContext(user_id=UserId("user-a"), correlation_id="a", deadline_s=30.0)
        b_ctx = ToolContext(user_id=UserId("user-b"), correlation_id="b", deadline_s=30.0)
        a = await self._collect(a_ctx, task_id="shared-task-id")
        b = await call("collect_verification_evidence", b_ctx, operation="collect", task_id="shared-task-id",
                       test_outputs=["1 failed"])
        assert b["verdict"] == "FAILED" and b["dossier"]["test_outputs"] == ["1 failed"]  # A's content is nowhere in B's result
        assert "8 passed" not in json.dumps(b)
        forged = await call("collect_verification_evidence", b_ctx, operation="verify", dossier=b["dossier"],
                            expected_digest=a["evidence_digest"])
        assert forged["valid"] is False
        # certificate/verify take everything from the caller: asking with no dossier reveals nothing
        with pytest.raises(ToolExecutionError):
            await call("collect_verification_evidence", b_ctx, operation="certificate")

    async def test_plan_feedback_is_stateless_across_users_too(self, ctx) -> None:
        a_ctx = ToolContext(user_id=UserId("user-a"), correlation_id="a", deadline_s=30.0)
        b_ctx = ToolContext(user_id=UserId("user-b"), correlation_id="b", deadline_s=30.0)
        await call("apply_plan_feedback", a_ctx, plan=["secret step of A", "other"], feedback="remove step 1")
        b = await call("apply_plan_feedback", b_ctx, plan=["B1", "B2"], feedback="remove step 1")
        assert b["revised_plan"] == ["B2"] and "secret" not in json.dumps(b)


# --------------------------------------------------------------------------------------
# Row 51 -- apply_plan_feedback
# --------------------------------------------------------------------------------------

_PLAN = ["Design schema", "Write migration", "Add tests", "Update docs"]


class TestApplyPlanFeedback:
    async def test_numbered_edits_in_one_comment_refer_to_the_plan_as_sent(self, ctx) -> None:
        r = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="Remove step 3 and add a rollback step after step 2. Swap steps 1 and 4.")
        assert r["revised_plan"] == ["Update docs", "Write migration", "Rollback step", "Design schema"]
        assert r["changed"] is True and r["revision"] == 1 and r["needs_replan"] is False

    async def test_move_change_and_remove_several(self, ctx) -> None:
        moved = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="move step 4 before step 2")
        assert moved["revised_plan"] == ["Design schema", "Update docs", "Write migration", "Add tests"]
        changed = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="change step 2 to run the migration in a transaction")
        assert changed["revised_plan"][1] == "run the migration in a transaction"
        several = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="remove steps 2 and 4")
        assert several["revised_plan"] == ["Design schema", "Add tests"]
        by_words = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="remove the docs step")
        assert "Update docs" not in by_words["revised_plan"]

    async def test_a_rejection_is_not_applied_but_asks_for_a_replan(self, ctx) -> None:
        r = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="I don't approve, please rethink the migration approach")
        assert r["intent"] == "REJECT" and r["changed"] is False and r["needs_replan"] is True
        assert "Design schema" in r["replan_instruction"] and "rethink" in r["replan_instruction"]

    async def test_approval_changes_nothing(self, ctx) -> None:
        r = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="looks good, approve")
        assert r["approved"] is True and r["revised_plan"] == _PLAN

    async def test_a_missing_step_number_is_reported_not_guessed(self, ctx) -> None:
        r = await call("apply_plan_feedback", ctx, plan=_PLAN, feedback="remove step 9")
        assert r["revised_plan"] == _PLAN and "does not exist" in r["actions"][0]

    async def test_a_batch_applies_in_order_and_dedupes(self, ctx) -> None:
        r = await call("apply_plan_feedback", ctx, plan=_PLAN, feedbacks=["remove step 1", "add a rollback step", "add a rollback step"])
        assert r["revised_plan"] == ["Write migration", "Add tests", "Update docs", "Rollback step"] and len(r["history"]) == 3

    async def test_markdown_patch_and_diff(self, ctx) -> None:
        md = "# Plan\n## Steps\n1. a\n## Risks\nnone\n"
        patched = await call("apply_plan_feedback", ctx, operation="patch_markdown", markdown_text=md, section="Risks", new_content="data loss")
        assert patched["patched"] and "data loss" in patched["content"] and "none" not in patched["content"]
        diff = await call("apply_plan_feedback", ctx, operation="diff", markdown_text=md, revised_text=patched["content"])
        assert diff["has_changes"] and diff["lines_added"] == 1
        assert (await call("apply_plan_feedback", ctx, operation="classify", feedback="remove step 2"))["intent"] == "REMOVE_STEP"

    async def test_an_oversized_plan_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="limit is 300"):
            await call("apply_plan_feedback", ctx, plan=[f"s{i}" for i in range(301)], feedback="remove step 1")

    async def test_missing_inputs_are_errors(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await call("apply_plan_feedback", ctx, feedback="remove step 1")
        with pytest.raises(ToolExecutionError):
            await call("apply_plan_feedback", ctx, plan=_PLAN)


# --------------------------------------------------------------------------------------
# Row 69 -- summarize_for_mobile
# --------------------------------------------------------------------------------------


class TestSummarizeForMobile:
    async def test_diff_tests_and_risk_flags_fit_a_phone_summary(self, ctx) -> None:
        diff = ("diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-A=1\n+A=2\n"
                "diff --git a/alembic/versions/0020_x.py b/alembic/versions/0020_x.py\n--- a/alembic/versions/0020_x.py\n+++ b/alembic/versions/0020_x.py\n@@ -0,0 +1 @@\n+x\n")
        r = await call("summarize_for_mobile", ctx, task="Add the audit table", diff_text=diff,
                       test_output="2 failed, 18 passed in 4.2s", plan_steps=["Migrate", "Test"], max_chars=300)
        assert r["headline"] == "FAILED" and len(r["summary_text"]) <= 300
        flags = " ".join(r["risk_flags"])
        assert ".env" in flags and "migration" in flags and "2 test(s) failing" in flags
        assert len(r["notification"]) <= 140 and r["card"]["type"] == "mobile_card"
        assert {a["action_id"] for a in r["card"]["actions"]} == {"approve", "reject"}

    async def test_a_green_run_is_ready_and_unknown_test_text_is_not_a_pass(self, ctx) -> None:
        ok = await call("summarize_for_mobile", ctx, task="t", test_results={"passed": 5, "failed": 0, "duration": "1s"})
        assert ok["headline"] == "READY"
        unknown = await call("summarize_for_mobile", ctx, task="t", test_output="completely unrelated text")
        assert unknown["headline"] != "READY" and "test results not recognised" in unknown["risk_flags"]

    async def test_no_input_is_an_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await call("summarize_for_mobile", ctx)


# --------------------------------------------------------------------------------------
# Row 73 -- detect_file_type
# --------------------------------------------------------------------------------------


def _zip(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


class TestDetectFileType:
    async def test_office_files_are_found_by_their_zip_contents_whatever_they_are_called(self, ctx) -> None:
        docx = await call("detect_file_type", ctx, content_base64=b64(_zip("[Content_Types].xml", "word/document.xml")), filename="a.dat")
        assert docx["format"] == "docx" and docx["recommended_tool"] == "parse_document"
        xlsx = await call("detect_file_type", ctx, content_base64=b64(_zip("[Content_Types].xml", "xl/workbook.xml")), filename="b.bin")
        assert xlsx["format"] == "xlsx" and xlsx["recommended_tool"] == "inspect_spreadsheet"
        plain = await call("detect_file_type", ctx, content_base64=b64(_zip("a.txt")), filename="c.zip")
        assert plain["format"] == "zip" and plain["recommended_tool"] is None and "run_command" in plain["how"]

    async def test_content_beats_the_extension_and_a_mismatch_is_reported(self, ctx) -> None:
        r = await call("detect_file_type", ctx, content_base64=b64(_PNG), filename="notes.pdf")
        assert r["mime_type"] == "image/png" and r["width"] == 320 and r["recommended_tool"] == "ocr_image"
        assert r["extension_mismatch"]["content_says"] == "image/png"

    async def test_text_formats_route_to_the_tool_that_reads_them(self, ctx) -> None:
        cases = [
            ('{"a": [1, 2]}', "json", "parse_structured_data"),
            ("name,qty\nbolt,3\nnut,9\n", "csv", "inspect_spreadsheet"),
            ("def f(x):\n    return x\n", "python", "analyze_python_ast"),
            ("SELECT id FROM users WHERE id = 1;\n", "sql", "inspect_sql"),
            ("<?xml version='1.0'?><a><b/></a>", "xml", "parse_structured_data"),
            ("[server]\nport = 1\n", "toml", "parse_structured_data"),
            ("2026-01-01 10:00:00 ERROR boom\n2026-01-01 10:00:01 INFO ok\n", "log", "analyze_logs"),
        ]
        for text, fmt, tool_name in cases:
            r = await call("detect_file_type", ctx, text=text)
            assert (r["format"], r["recommended_tool"]) == (fmt, tool_name), (text, r)

    async def test_a_pdf_header_and_a_text_that_starts_like_a_magic_number(self, ctx) -> None:
        pdf = await call("detect_file_type", ctx, content_base64=b64(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"), filename="r.txt")
        assert pdf["mime_type"] == "application/pdf" and pdf["is_text"] is False
        prose = await call("detect_file_type", ctx, text="BMW makes cars and ID3 is a tag format.")
        assert prose["mime_type"] == "text/plain"

    async def test_bad_arguments(self, ctx) -> None:
        for args in ({}, {"text": "a", "content_base64": "YQ=="}, {"content_base64": "***"}):
            with pytest.raises(ToolExecutionError):
                await call("detect_file_type", ctx, **args)


# --------------------------------------------------------------------------------------
# Row 80 -- parse_structured_data
# --------------------------------------------------------------------------------------

_SCHEMA = {
    "type": "object", "required": ["name", "port", "owner"],
    "properties": {
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "db": {"type": "object", "properties": {"host": {"type": "string", "pattern": "^[a-z]+$"}}, "additionalProperties": False},
        "mode": {"enum": ["a", "b"]},
    },
}


class TestParseStructuredData:
    async def test_nested_schema_errors_carry_paths_and_lines(self, ctx) -> None:
        text = '{\n  "name": "svc",\n  "port": "80",\n  "tags": ["a", 2],\n  "db": {"host": "X", "extra": 1},\n  "mode": "c"\n}'
        r = await call("parse_structured_data", ctx, content=text, schema=_SCHEMA)
        errors = {e["path"]: e for e in r["schema_errors"]}
        assert r["valid"] is True and r["schema_valid"] is False
        assert errors["$.owner"]["keyword"] == "required"
        assert errors["$.port"]["line"] == 3 and errors["$.tags[1]"]["line"] == 4
        assert errors["$.db.host"]["keyword"] == "pattern" and errors["$.db.extra"]["keyword"] == "additionalProperties"
        assert errors["$.mode"]["keyword"] == "enum"

    async def test_yaml_lines_and_a_valid_document(self, ctx) -> None:
        yml = "name: svc\nport: 8080\nowner: me\ndb:\n  host: abc\n"
        r = await call("parse_structured_data", ctx, content=yml, schema=_SCHEMA)
        assert r["format"] == "yaml" and r["schema_valid"] is True and r["schema_errors"] == []
        bad = await call("parse_structured_data", ctx, content="name: svc\nport: -1\nowner: me\n", schema=_SCHEMA)
        assert bad["schema_errors"][0]["line"] == 2

    async def test_parse_errors_have_line_column_and_the_offending_line(self, ctx) -> None:
        r = await call("parse_structured_data", ctx, content='{\n "a": 1,\n "b": }', data_format="json")
        assert (r["valid"], r["line"], r["column"]) == (False, 3, 7) and r["offending_line"].strip() == '"b": }'
        toml = await call("parse_structured_data", ctx, content="[t", data_format="toml")
        assert toml["valid"] is False and toml["line"] == 1
        xml = await call("parse_structured_data", ctx, content="<a><b></a>", data_format="xml")
        assert xml["valid"] is False and xml["line"] == 1

    async def test_toml_auto_detect_flatten_and_convert(self, ctx) -> None:
        r = await call("parse_structured_data", ctx, content="[server]\nport = 8080\nhost = 'x'\n", flatten=True, convert_to="yaml")
        assert r["format"] == "toml" and r["flattened"] == {"server.port": 8080, "server.host": "x"}
        assert "port: 8080" in r["converted_text"]
        xml = await call("parse_structured_data", ctx, content="<root a='1'><x>t</x></root>")
        assert xml["format"] == "xml" and xml["parsed_data"]["tag"] == "root"

    async def test_combinators_refs_and_unsupported_keywords(self, ctx) -> None:
        schema = {"definitions": {"id": {"type": "string", "format": "uuid"}},
                  "type": "object", "properties": {"id": {"$ref": "#/definitions/id"}, "v": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                                                    "n": {"type": "number", "multipleOf": 0.5, "exclusiveMinimum": 0}},
                  "x-note": "ignored", "propertyNames": {"pattern": "^a"}}
        good = await call("parse_structured_data", ctx, content='{"id": "123e4567-e89b-12d3-a456-426614174000", "v": 3, "n": 1.5}', schema=schema)
        assert [e["keyword"] for e in good["schema_errors"]] == ["unsupported_keyword"] and good["schema_valid"] is True
        bad = await call("parse_structured_data", ctx, content='{"id": "nope", "v": 1.5, "n": 0}', schema={k: v for k, v in schema.items() if k != "propertyNames"})
        assert {e["path"] for e in bad["schema_errors"]} == {"$.id", "$.v", "$.n"}

    async def test_legacy_contract_still_works(self, ctx) -> None:
        r = await call("parse_structured_data", ctx, content='{"a": 1}', required_keys=["a", "b"], type_contracts={"a": "str"})
        assert r["contract_valid"] is False and len(r["contract_errors"]) == 2

    async def test_hostile_input_is_refused(self, ctx) -> None:
        bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(f"{c}: &{c} [*{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}]\n"
                                                                   for p, c in zip("abcdefg", "bcdefgh"))
        r = await call("parse_structured_data", ctx, content=bomb + "z: [*h, *h, *h, *h, *h, *h, *h, *h, *h, *h, *h]\n"
                       .replace("*h", "*h"), data_format="yaml")
        assert r["valid"] is False
        entity = await call("parse_structured_data", ctx, content='<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>', data_format="xml")
        assert entity["valid"] is False
        redos = await call("parse_structured_data", ctx, content='{"s": "' + "a" * 50 + '!"}',
                           schema={"properties": {"s": {"pattern": "^(a+)+$"}}})
        assert "refused" in redos["schema_errors"][0]["message"]

    async def test_an_unusable_schema_is_an_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await call("parse_structured_data", ctx, content="{}", schema={"type": "bogus"})
        with pytest.raises(ToolExecutionError):
            await call("parse_structured_data", ctx, content="{}", schema={"$ref": "http://x/y"})


# --------------------------------------------------------------------------------------
# Row 81 -- analyze_source_structure
# --------------------------------------------------------------------------------------

_TS = '''import { a } from "./util";
import fs from 'fs';
// class Ghost {}
export class Foo extends Bar {
  constructor(x) { super(x); }
  async run(n: number): Promise<void> {
    helper(n);
    this.other();
  }
}
export interface Shape { area(): number; }
export function helper(n) { return n + 1; }
const arrow = (a) => { return a; };
'''
_GO = '''package main

import (
\t"fmt"
\tstr "strings"
)

type T struct{}

func (t *T) Hello(a int) string {
\tfmt.Println("x")
\treturn str.ToUpper("a")
}

func Main() {
\tx := T{}
\tx.Hello(1)
}
'''
_RUST = '''use std::io;

pub struct P { x: i32 }

impl P {
    pub fn add(&self, a: i32) -> i32 {
        helper(a)
    }
}

fn helper(a: i32) -> i32 {
    a
}
'''
_JAVA = '''package demo;

import java.util.List;

public class Svc {
    public static int count(List<String> xs) {
        return xs.size();
    }
}
'''


class TestAnalyzeSourceStructure:
    async def test_typescript_methods_classes_imports_and_calls(self, ctx) -> None:
        r = await call("analyze_source_structure", ctx, source_code=_TS, language="typescript")
        symbols = {(s["kind"], s["name"], s["parent"]) for s in r["symbols"]}
        assert {("class", "Foo", None), ("method", "run", "Foo"), ("function", "helper", None), ("interface", "Shape", None),
                ("function", "arrow", None)} <= symbols
        assert "Ghost" not in json.dumps(r["symbols"])  # a commented-out class is not a class
        assert [i["module"] for i in r["imports"]] == ["./util", "fs"]
        assert r["call_graph"]["Foo.run"] == ["helper", "other"]
        run = next(s for s in r["symbols"] if s["name"] == "run")
        assert (run["line"], run["end_line"]) == (6, 9)

    async def test_go_receivers_rust_impls_and_java_methods(self, ctx) -> None:
        go = await call("analyze_source_structure", ctx, source_code=_GO, filename="main.go")
        assert {(s["kind"], s["name"], s["parent"]) for s in go["symbols"]} >= {("struct", "T", None), ("method", "Hello", "T"), ("function", "Main", None)}
        assert [i["module"] for i in go["imports"]] == ["fmt", "strings"] and go["package"] == "main"
        rs = await call("analyze_source_structure", ctx, source_code=_RUST, language="rust")
        assert {(s["kind"], s["name"], s["parent"]) for s in rs["symbols"]} >= {("struct", "P", None), ("method", "add", "P"), ("function", "helper", None)}
        assert rs["call_graph"]["P.add"] == ["helper"] and rs["imports"][0]["module"] == "std::io"
        java = await call("analyze_source_structure", ctx, source_code=_JAVA, filename="Svc.java")
        assert {(s["kind"], s["name"]) for s in java["symbols"]} >= {("class", "Svc"), ("method", "count")}
        assert java["imports"][0]["module"] == "java.util.List"

    async def test_python_uses_the_ast_with_signatures_and_dotted_calls(self, ctx) -> None:
        r = await call("analyze_source_structure", ctx, source_code=_BILLING, filename="billing.py")
        load = next(s for s in r["symbols"] if s["name"] == "load")
        assert load["signature"] == "def load(path)" and "json.load" in load["calls"] and "helper" in load["calls"]
        assert next(s for s in r["symbols"] if s["name"] == "total")["parent"] == "Invoice"

    async def test_a_file_set_gives_a_cross_language_index_and_skips_the_rest(self, ctx) -> None:
        r = await call("analyze_source_structure", ctx, files={"a.ts": _TS, "b.go": _GO, "notes.md": "# hi"})
        assert r["files_analyzed"] == 2 and {s["language"] for s in r["symbols"]} == {"typescript", "go"}
        assert r["skipped_files"][0]["file"] == "notes.md"

    async def test_calls_can_be_left_out_and_undetectable_language_is_an_error(self, ctx) -> None:
        r = await call("analyze_source_structure", ctx, source_code=_TS, language="typescript", include_calls=False)
        assert "call_graph" not in r and all("calls" not in s for s in r["symbols"])
        with pytest.raises(ToolExecutionError):
            await call("analyze_source_structure", ctx, source_code="???")
        with pytest.raises(ToolExecutionError):
            await call("analyze_source_structure", ctx)

    async def test_a_minified_line_is_counted_not_scanned(self, ctx) -> None:
        r = await call("analyze_source_structure", ctx, source_code="function a(){" + "x();" * 400 + "}\nfunction b() { return 1; }\n", language="javascript")
        assert r["skipped_long_lines"] == 1 and [s["name"] for s in r["symbols"]] == ["b"]


# --------------------------------------------------------------------------------------
# Row 105 -- validate_workflow
# --------------------------------------------------------------------------------------


class TestValidateWorkflow:
    async def test_depends_on_is_honoured_and_order_and_tiers_follow_it(self, ctx) -> None:
        r = await call("validate_workflow", ctx, tasks=[{"id": "test", "depends_on": ["build"]}, {"id": "build"},
                                                         {"id": "lint"}, {"id": "ship", "needs": ["test", "lint"]}])
        assert r["valid"] is True
        assert r["order"].index("build") < r["order"].index("test") < r["order"].index("ship")
        assert r["parallel_tiers"][0] == ["build", "lint"] and r["max_concurrency"] == 2

    async def test_cycles_undefined_and_duplicate_ids_and_self_dependencies(self, ctx) -> None:
        cyc = await call("validate_workflow", ctx, tasks=[{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}])
        assert cyc["valid"] is False and "a -> b -> a" in cyc["errors"][0] or "b -> a -> b" in cyc["errors"][0]
        bad = await call("validate_workflow", ctx, tasks=[{"id": "a", "depends_on": ["ghost", "a"]}, {"id": "a"}, {"name": ""}])
        text = " ".join(bad["errors"])
        assert "undefined task 'ghost'" in text and "duplicate task id 'a'" in text and "depends on itself" in text and "no 'id'" in text

    async def test_deadlines_are_checked_against_the_critical_path(self, ctx) -> None:
        tasks = [{"id": "build", "duration_s": 60}, {"id": "test", "depends_on": ["build"], "duration_s": 120},
                 {"id": "deploy", "depends_on": ["test"], "duration_s": 30, "deadline_s": 150}]
        r = await call("validate_workflow", ctx, tasks=tasks, deadline_s=400)
        assert r["critical_path"] == ["build", "test", "deploy"] and r["critical_duration_s"] == 210.0
        assert r["deadlines"]["met"] is False and r["deadlines"]["misses"][0]["task"] == "deploy" and r["valid"] is False
        ok = await call("validate_workflow", ctx, tasks=[{**t, "deadline_s": 999} for t in tasks], deadline_s=400)
        assert ok["valid"] is True and ok["deadlines"]["met"] is True
        assert (await call("validate_workflow", ctx, tasks=[{"id": "a"}]))["deadlines_checked"] is False

    async def test_failure_propagation_and_recovery(self, ctx) -> None:
        tasks = [{"id": "build"}, {"id": "test", "depends_on": ["build"], "on_failure": "recover", "recovery": "rollback"},
                 {"id": "deploy", "depends_on": ["test"]}, {"id": "rollback", "depends_on": ["build"]},
                 {"id": "notify", "depends_on": ["deploy"], "on_failure": "continue"}]
        r = await call("validate_workflow", ctx, tasks=tasks, simulate_failures=["build"])
        assert r["failure_impact"]["build"] == {"blocks": 3, "first": ["deploy", "notify", "test"]}
        assert "test" not in r["failure_impact"] and "notify" not in r["failure_impact"]  # they tolerate their own failure
        assert r["recovery_tasks"] == ["rollback"] and "rollback" not in r["order"]
        assert r["simulation"]["blocked"] == ["deploy", "notify", "test"]
        sim = await call("validate_workflow", ctx, tasks=tasks, simulate_failures=["test"])
        assert sim["simulation"]["recovered_by"] == ["test -> rollback"] and "deploy" in sim["simulation"]["still_runs"]

    async def test_recovery_must_exist_and_must_be_able_to_run(self, ctx) -> None:
        missing = await call("validate_workflow", ctx, tasks=[{"id": "a", "on_failure": "recover", "recovery": "ghost"}])
        assert any("undefined recovery task 'ghost'" in e for e in missing["errors"])
        none = await call("validate_workflow", ctx, tasks=[{"id": "a", "on_failure": "recover"}])
        assert any("no 'recovery' task" in e for e in none["errors"])
        stuck = await call("validate_workflow", ctx, tasks=[{"id": "a", "on_failure": "recover", "recovery": "b"}, {"id": "b", "depends_on": ["a"]}])
        assert any("cannot run after 'a' fails" in e for e in stuck["errors"])

    async def test_invalid_durations_and_empty_or_oversized_input(self, ctx) -> None:
        r = await call("validate_workflow", ctx, tasks=[{"id": "a", "duration_s": -3}])
        assert r["valid"] is False and "duration" in r["errors"][0]
        with pytest.raises(ToolExecutionError):
            await call("validate_workflow", ctx, tasks=[])

    async def test_a_long_chain_does_not_recurse(self, ctx) -> None:
        tasks = [{"id": f"t{i}", "depends_on": [f"t{i - 1}"] if i else []} for i in range(1500)]
        r = await call("validate_workflow", ctx, tasks=tasks)
        assert r["valid"] is True and r["order_length"] == 1500 and r["critical_duration_s"] == 1500.0
        assert r["tier_count"] == 1500 and r["failure_impact"]["t0"]["blocks"] == 1499 and r["truncated"]
