"""Regression tests for bugs found by running every agent tool against realistic wrong input.

Each class pins one finding from the tool-inventory verification pass. They all failed on the code
as it stood: a malformed argument was either swallowed (an empty diagram, ``<unknown>``, a package
called ``!!``) or surfaced as an opaque error, and one secret-redaction gap wrote a raw key to the log.
"""

from __future__ import annotations

import json
import logging

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.observability.logging import ContentRedactingFilter
from aura_core.ports.runtime import ToolContext
from aura_core.tools.analysis import build_analysis_tools
from aura_core.tools.open.dependency_manager_validator import DependencyManagerValidator
from aura_core.tools.open.peer_code_reviewer_agent import PeerCodeReviewerAgent
from aura_core.tools.open.symbol_and_import_verifier import SymbolAndImportVerifier
from aura_core.tools.open.workflow_dependency_validator import WorkflowDependencyValidator

_TOOLS = {t.spec.name: t for t in build_analysis_tools()}


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=30.0)


async def call(name: str, ctx: ToolContext, **arguments) -> dict:
    return json.loads(await _TOOLS[name].execute(arguments, ctx))


# --------------------------------------------------------------------------------------
# 1. verify_python_symbols: malformed expected_arities
# --------------------------------------------------------------------------------------


class TestExpectedAritiesShape:
    @pytest.mark.parametrize(
        "bad", [{"f": 3}, {"f": "two"}, {"f": {"min_args": "1"}}, {"f": {"max": 2}}, {"f": [1, 2]}]
    )
    async def test_a_malformed_spec_is_a_clear_tool_error(self, ctx, bad) -> None:
        with pytest.raises(ToolExecutionError) as info:
            await _TOOLS["verify_python_symbols"].execute(
                {"source_code": "f(1)\n", "expected_arities": bad}, ctx
            )
        message = str(info.value)
        assert "expected_arities" in message and "min_args" in message
        assert "has no attribute" not in message

    async def test_a_valid_spec_still_works(self, ctx) -> None:
        out = await call(
            "verify_python_symbols", ctx,
            source_code="def f(a, b): ...\nf(1)\n", expected_arities={"f": {"min_args": 2, "max_args": 2}},
        )
        assert out["arity_check"]["valid"] is False
        assert out["arity_check"]["mismatches"][0]["function"] == "f"

    def test_the_class_returns_an_error_result_instead_of_crashing(self) -> None:
        out = SymbolAndImportVerifier.verify_call_argument_counts("f(1)\n", {"f": 3})  # type: ignore[arg-type]
        assert out["valid"] is False
        assert "expected_arities" in out["error"]
        assert out["mismatches"] == []


# --------------------------------------------------------------------------------------
# 2. generate_diagram: wrong keys no longer produce a silently empty diagram
# --------------------------------------------------------------------------------------


class TestDiagramSpecKeys:
    @pytest.mark.parametrize(
        ("diagram_type", "spec", "fragment"),
        [
            ("class", {"class_details": [{"title": "A"}]}, "class_details[0]"),
            ("class", {"relations": [{"source": "A", "target": "B"}, {"from": "A", "to": "B"}]},
             "relations[1]"),
            ("flowchart", {"nodes": [{"shape": "round"}]}, "nodes[0]"),
            ("flowchart", {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "b"}]},
             "edges[0]"),
            ("flowchart", {"nodes": [{"id": "a"}], "edges": [{"from": "a", "to": "a"}, {"from": "a"}]},
             "edges[1]"),
            ("sequence", {"participants": ["A", "B"], "messages": [{"source": "A", "target": "B"}]},
             "messages[0]"),
        ],
    )
    async def test_a_missing_key_names_the_key_and_the_index(self, ctx, diagram_type, spec, fragment) -> None:
        with pytest.raises(ToolExecutionError) as info:
            await _TOOLS["generate_diagram"].execute({"diagram_type": diagram_type, "spec": spec}, ctx)
        assert fragment in str(info.value)
        assert "missing" in str(info.value) or "needs one of" in str(info.value)

    async def test_valid_specs_are_unchanged(self, ctx) -> None:
        flow = await call(
            "generate_diagram", ctx, diagram_type="flowchart",
            spec={"nodes": [{"id": "a", "label": "Start"}, {"label": "End"}],
                  "edges": [{"from": "a", "to": "End"}]},
        )
        assert flow["mermaid"].startswith("flowchart TD") and "-->" in flow["mermaid"]
        klass = await call(
            "generate_diagram", ctx, diagram_type="class",
            spec={"class_details": [{"name": "A", "methods": ["run()"]}],
                  "relations": [{"source": "A", "target": "B"}]},
        )
        assert "class A" in klass["mermaid"] and "A" in klass["mermaid"].split("\n")[-1]
        seq = await call(
            "generate_diagram", ctx, diagram_type="sequence",
            spec={"participants": ["A", "B"], "messages": [{"from": "A", "to": "B", "text": "hi"}]},
        )
        assert "A->>B: hi" in seq["mermaid"]


# --------------------------------------------------------------------------------------
# 3. scan_dependency_licenses: `name` alias, and no silent <unknown>
# --------------------------------------------------------------------------------------


class TestLicenseItemKeys:
    async def test_name_is_accepted_for_package(self, ctx) -> None:
        out = await call(
            "scan_dependency_licenses", ctx, packages=[{"name": "left-pad", "license": "GPL-3.0"}]
        )
        assert "<unknown>" not in json.dumps(out)
        assert out["violations"][0]["package"] == "left-pad"

    async def test_package_still_wins(self, ctx) -> None:
        out = await call(
            "scan_dependency_licenses", ctx, packages=[{"package": "requests", "license": "GPL-3.0"}]
        )
        assert out["violations"][0]["package"] == "requests"

    async def test_an_item_with_neither_is_a_clear_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match=r"packages\[1\].*'package'"):
            await _TOOLS["scan_dependency_licenses"].execute(
                {"packages": [{"package": "a", "license": "MIT"}, {"pkg": "b", "license": "MIT"}]}, ctx
            )


# --------------------------------------------------------------------------------------
# 4. check_requirements: garbage lines are invalid, not packages
# --------------------------------------------------------------------------------------


class TestRequirementGarbageLines:
    def test_a_garbage_line_is_not_a_package(self) -> None:
        parsed = DependencyManagerValidator._parse("requests==2.0\nbadline!!\n")
        assert [p["name"] for p in parsed["packages"]] == ["requests"]
        assert [i["raw"] for i in parsed["invalid_lines"]] == ["badline!!"]
        assert parsed["invalid_lines"][0]["line"] == 2

    async def test_the_tool_reports_it_and_no_longer_calls_the_file_a_valid_lock(self, ctx) -> None:
        out = await call("check_requirements", ctx, requirements_text="requests==2.0\nbadline!!\n")
        pinning = out["requirements"]["pinning"]
        assert [i["raw"] for i in pinning["invalid_lines"]] == ["badline!!"]
        assert pinning["is_valid_lock"] is False
        assert pinning["exact_pinned_count"] == 1
        assert out["requirements"]["valid"] is False

    @pytest.mark.parametrize(
        "line", ["requests>=2.0,<3", "requests[security]==2.0", "pkg ; python_version < '3.9'", "a.b_c-d~=1.0", "flask"]
    )
    def test_ordinary_lines_still_parse(self, line) -> None:
        parsed = DependencyManagerValidator._parse(line + "\n")
        assert parsed["invalid_lines"] == []
        assert len(parsed["packages"]) == 1


# --------------------------------------------------------------------------------------
# 5. workflow validator: depends_on / deps aliases
# --------------------------------------------------------------------------------------


class TestWorkflowDependencyAliases:
    @pytest.mark.parametrize("key", ["dependencies", "prerequisites", "depends_on", "deps"])
    def test_every_spelling_declares_a_dependency(self, key) -> None:
        spec = {"tasks": [{"id": "build"}, {"id": "test", key: ["build"]}]}
        out = WorkflowDependencyValidator.validate_workflow_spec(spec)
        assert out["valid"] is True
        assert out["parallel_tiers"] == [["build"], ["test"]]

    def test_a_dangling_depends_on_is_reported(self) -> None:
        out = WorkflowDependencyValidator.validate_workflow_spec(
            {"tasks": [{"id": "test", "depends_on": ["ghost"]}]}
        )
        assert out["valid"] is False
        assert "ghost" in out["errors"][0]

    def test_a_cycle_through_depends_on_is_detected(self) -> None:
        out = WorkflowDependencyValidator.validate_workflow_spec(
            {"tasks": [{"id": "a", "depends_on": "b"}, {"id": "b", "depends_on": ["a"]}]}
        )
        assert out["valid"] is False and "Cyclic" in out["errors"][0]


# --------------------------------------------------------------------------------------
# 6. review_code_diff: source mode applies the pattern rules
# --------------------------------------------------------------------------------------

_RISKY = (
    '"""Module."""\n'
    "import requests\n"
    "\n"
    "\n"
    "def load(expr, url):\n"
    '    """Load."""\n'
    "    value = eval(expr)\n"
    "    return requests.get(url, verify=False), value\n"
)


class TestSourceModeReview:
    async def test_eval_and_disabled_tls_are_flagged_and_not_approved(self, ctx) -> None:
        out = await call("review_code_diff", ctx, source_code=_RISKY)
        by_code = {f["code"]: f for f in out["findings"]}
        assert by_code["REV-INSECURE-EVAL"]["line"] == 7
        assert by_code["REV-DISABLED-SSL"]["line"] == 8
        assert out["critical_issues"] == 2
        assert out["approved"] is False

    def test_comments_are_not_flagged_but_task_markers_are(self) -> None:
        source = '"""M."""\n# eval(x) was removed\n# TODO: tidy\n'
        codes = [f["code"] for f in PeerCodeReviewerAgent.full_review(source_code=source)["findings"]]
        assert codes == ["REV-TODO-MARKER"]

    def test_clean_source_is_still_approved(self) -> None:
        out = PeerCodeReviewerAgent.full_review(source_code='"""M."""\n\n\ndef f():\n    """F."""\n    return 1\n')
        assert out["approved"] is True and out["findings"] == []

    def test_diff_mode_is_unchanged(self) -> None:
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n a = 1\n+b = eval(a)\n"
        out = PeerCodeReviewerAgent.full_review(diff_text=diff)
        assert [(f["code"], f["line"]) for f in out["findings"]] == [("REV-INSECURE-EVAL", 2)]
        assert out["diff_review"]["approved"] is False


# --------------------------------------------------------------------------------------
# 7. ContentRedactingFilter: args and AWS keys
# --------------------------------------------------------------------------------------

_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
_OPENAI_KEY = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"


def _record(msg: str, *args, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord("t", level, __file__, 1, msg, args or None, None)


class TestLogArgsRedaction:
    def test_a_secret_passed_as_a_positional_arg_is_scrubbed(self) -> None:
        record = _record("key=%s user=%s", _OPENAI_KEY, "bob")
        assert ContentRedactingFilter().filter(record) is True
        text = record.getMessage()
        assert _OPENAI_KEY not in text
        assert text == "key=[redacted:api-key] user=bob"

    def test_a_secret_in_a_mapping_arg_is_scrubbed(self) -> None:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "key=%(k)s n=%(n)d", ({"k": _OPENAI_KEY, "n": 3},), None)
        ContentRedactingFilter().filter(record)
        assert record.getMessage() == "key=[redacted:api-key] n=3"

    def test_aws_access_keys_are_scrubbed_in_messages_and_args(self) -> None:
        in_msg = _record(f"creds {_AWS_KEY}")
        in_arg = _record("creds %s", _AWS_KEY)
        for record in (in_msg, in_arg):
            ContentRedactingFilter().filter(record)
            assert _AWS_KEY not in record.getMessage()
            assert "[redacted:aws-access-key]" in record.getMessage()

    def test_non_secret_formatting_is_identical(self) -> None:
        record = _record("%d items in %.1fs for %s", 3, 1.25, "bob")
        before = record.getMessage()
        ContentRedactingFilter().filter(record)
        assert record.getMessage() == before == "3 items in 1.2s for bob"
        assert record.args == (3, 1.25, "bob")

    def test_a_record_without_args_and_exc_info_is_untouched(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc = sys.exc_info()
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed %s", ("x",), exc)
        ContentRedactingFilter().filter(record)
        assert record.exc_info is exc
        assert record.getMessage() == "failed x"

    def test_end_to_end_through_a_handler_and_a_disabled_level(self, caplog) -> None:
        logger = logging.getLogger("aura.test_inventory_redaction")
        logger.setLevel(logging.INFO)
        stream: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                stream.append(record.getMessage())

        handler = Capture()
        handler.addFilter(ContentRedactingFilter())
        logger.addHandler(handler)
        try:
            logger.info("token %s", _AWS_KEY)
            logger.debug("never formatted %s", object())  # level disabled: nothing runs, nothing breaks
        finally:
            logger.removeHandler(handler)
        assert stream == ["token [redacted:aws-access-key]"]
