"""Built-in tools.

Three of these are security controls wearing a feature's clothes, and they are tested as such:

* ``calculator`` must never become ``eval`` -- its arguments come from a model that may have been
  influenced by any document retrieval pulled in.
* ``read_file``/``list_files`` must confine every path to one root *after* symlink resolution.
* ``fetch_url`` must refuse anything that could reach the deployment's own network, or the tool
  turns a tool-calling agent into an SSRF proxy.
"""

from __future__ import annotations

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.memory import MemoryQuery
from aura_core.domain.tools import ToolCategory, ToolSensitivity
from aura_core.ports.runtime import ToolContext
from aura_core.tools.builtin import (
    _assert_public_url,
    build_file_tools,
    build_http_tool,
    build_knowledge_tools,
    build_memory_tools,
    build_weather_tool,
    calculator,
    current_datetime,
    default_tools,
)
from aura_core.tools.personal_os import PERSONAL_OS_TOOL_NAMES


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=30.0)


class TestCalculator:
    async def test_arithmetic_and_precedence(self, ctx) -> None:
        assert "= 217" in await calculator.execute({"expression": "(1250 * 0.15) + 30"}, ctx)
        assert "= 7" in await calculator.execute({"expression": "1 + 2 * 3"}, ctx)

    async def test_integral_floats_are_rendered_without_a_decimal_tail(self, ctx) -> None:
        """`21.0 days` reads as a bug to a user; the model repeats what it is given."""
        assert await calculator.execute({"expression": "42 / 2"}, ctx) == "42 / 2 = 21"

    async def test_non_integral_results_keep_precision(self, ctx) -> None:
        assert "3.333333333" in await calculator.execute({"expression": "10 / 3"}, ctx)

    async def test_allowed_functions_and_constants(self, ctx) -> None:
        assert "= 4" in await calculator.execute({"expression": "sqrt(16)"}, ctx)
        assert "= 3" in await calculator.execute({"expression": "max(1, 2, 3)"}, ctx)
        assert "3.14159" in await calculator.execute({"expression": "pi"}, ctx)
        assert "= 120" in await calculator.execute({"expression": "factorial(5)"}, ctx)
        assert "= 5" in await calculator.execute({"expression": "hypot(3, 4)"}, ctx)
        assert "= -3" in await calculator.execute({"expression": "-3"}, ctx)
        assert "= 1" in await calculator.execute({"expression": "7 % 3"}, ctx)
        assert "= 2" in await calculator.execute({"expression": "7 // 3"}, ctx)

    async def test_a_nan_or_infinite_result_is_reported_verbatim(self, ctx) -> None:
        assert "inf" in await calculator.execute({"expression": "inf"}, ctx)

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('id')",
            "open('/etc/passwd').read()",
            "().__class__.__bases__",
            "[x for x in range(10)]",
            "lambda: 1",
            "1 if True else 2",
            "'a' * 3",
            "exec('x=1')",
            "eval('1')",
        ],
    )
    async def test_code_execution_attempts_are_refused(self, ctx, expression: str) -> None:
        """The naive version of this tool is a remote code execution vulnerability."""
        with pytest.raises(ToolExecutionError):
            await calculator.execute({"expression": expression}, ctx)

    async def test_an_unknown_function_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            await calculator.execute({"expression": "system(1)"}, ctx)
        assert "only these functions" in str(excinfo.value)

    async def test_keyword_arguments_are_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await calculator.execute({"expression": "round(1.234, ndigits=2)"}, ctx)

    async def test_an_unknown_name_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            await calculator.execute({"expression": "secret_key"}, ctx)
        assert "unknown name" in str(excinfo.value)

    async def test_a_huge_exponent_is_refused(self, ctx) -> None:
        """`2**10**9` is a trivially reachable memory exhaustion, not a calculation."""
        with pytest.raises(ToolExecutionError) as excinfo:
            await calculator.execute({"expression": "2 ** 100000"}, ctx)
        assert "exponent" in str(excinfo.value)

    async def test_a_huge_base_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await calculator.execute({"expression": "1e13 ** 100"}, ctx)

    async def test_a_huge_factorial_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            await calculator.execute({"expression": "factorial(10000)"}, ctx)
        assert "factorial" in str(excinfo.value)

    async def test_a_syntax_error_is_reported_not_raised_raw(self, ctx) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            await calculator.execute({"expression": "1 +"}, ctx)
        assert "could not parse" in str(excinfo.value)

    async def test_a_bitwise_operator_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            await calculator.execute({"expression": "1 << 62"}, ctx)
        assert "not allowed" in str(excinfo.value)

    async def test_a_unary_invert_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await calculator.execute({"expression": "~5"}, ctx)

    async def test_a_string_literal_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            await calculator.execute({"expression": "'abc'"}, ctx)
        assert "numeric literals" in str(excinfo.value)

    async def test_tuple_arguments_work_for_gcd_style_calls(self, ctx) -> None:
        assert "= 6" in await calculator.execute({"expression": "gcd(12, 18)"}, ctx)

    def test_the_declared_spec_is_safe_to_advertise(self) -> None:
        spec = calculator.spec
        assert spec.category is ToolCategory.UTILITY
        assert spec.side_effecting is False
        assert spec.sensitivity is ToolSensitivity.PUBLIC


class TestCurrentDatetime:
    async def test_defaults_to_utc(self, ctx) -> None:
        result = await current_datetime.execute({}, ctx)
        assert "UTC" in result
        assert "ISO:" in result

    async def test_an_offset_is_labelled_and_applied(self, ctx) -> None:
        result = await current_datetime.execute({"timezone_offset_hours": 2}, ctx)
        assert "UTC+2" in result

    async def test_a_negative_offset_is_labelled(self, ctx) -> None:
        assert "UTC-5" in await current_datetime.execute({"timezone_offset_hours": -5}, ctx)

    async def test_an_out_of_range_offset_is_rejected_by_the_schema(self, ctx) -> None:
        from aura_core.domain.errors import ValidationError

        with pytest.raises((ValidationError, ToolExecutionError)):
            await current_datetime.execute({"timezone_offset_hours": 99}, ctx)


class TestKnowledgeTool:
    async def test_returns_cited_passages(self, knowledge, user, handbook, ctx) -> None:
        from aura_core.domain import DocumentId

        await knowledge.ingest(
            content=handbook, title="Handbook",
            document_id=DocumentId("handbook"), owner_id=user,
        )
        (search,) = build_knowledge_tools(knowledge)
        result = await search.execute({"query": "annual leave"}, ctx)

        assert "[1]" in result
        assert "score" in result
        assert "handbook" in result

    async def test_an_empty_result_says_so_explicitly(self, knowledge, user, ctx) -> None:
        """Returning nothing must read as a genuine 'not found', not as a reason to guess."""
        (search,) = build_knowledge_tools(knowledge)
        result = await search.execute({"query": "the melting point of tungsten"}, ctx)

        assert "No passages above the relevance threshold" in result
        assert "candidate(s) examined" in result

    def test_it_is_marked_personal_and_read_only(self, knowledge) -> None:
        (search,) = build_knowledge_tools(knowledge)
        assert search.spec.sensitivity is ToolSensitivity.PERSONAL
        assert search.spec.side_effecting is False


class TestMemoryTools:
    async def test_recall_reports_confidence_and_kind(self, memory, user, ctx) -> None:
        await memory.remember(owner_id=user, content="The user is vegetarian.", source="turn:1")
        recall, _ = build_memory_tools(memory)
        result = await recall.execute({"query": "the user is vegetarian"}, ctx)

        assert "vegetarian" in result
        assert "confidence" in result

    async def test_recall_with_nothing_stored_is_explicit(self, memory, ctx) -> None:
        recall, _ = build_memory_tools(memory)
        assert "Nothing recorded" in await recall.execute({"query": "diet"}, ctx)

    async def test_remember_writes_with_provenance(self, memory, user, ctx) -> None:
        """Provenance is what makes 'forget everything you learned from X' possible at all."""
        _, remember = build_memory_tools(memory)
        result = await remember.execute({"fact": "The user works in Cairo.", "subject": "work"}, ctx)

        assert "Recorded:" in result
        hits = await memory.recall(MemoryQuery(owner_id=user, text="the user works in Cairo"))
        assert hits[0].record.provenance.source == "tool:remember_fact:corr-1"

    def test_remember_is_declared_side_effecting(self, memory) -> None:
        """This is what makes it refuse to run in a quarantined turn without confirmation."""
        _, remember = build_memory_tools(memory)
        assert remember.spec.side_effecting is True


class TestFileTools:
    @pytest.fixture
    def workspace(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello from the workspace", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.txt").write_text("nested", encoding="utf-8")
        (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
        return tmp_path

    async def test_listing_skips_dotfiles_and_marks_directories(self, workspace, ctx) -> None:
        list_files, _ = build_file_tools(workspace)
        result = await list_files.execute({}, ctx)

        assert "notes.txt" in result
        assert "sub/" in result
        assert ".hidden" not in result

    async def test_listing_a_subdirectory(self, workspace, ctx) -> None:
        list_files, _ = build_file_tools(workspace)
        assert "deep.txt" in await list_files.execute({"subdirectory": "sub"}, ctx)

    async def test_listing_an_empty_directory_says_so(self, workspace, ctx) -> None:
        (workspace / "empty").mkdir()
        list_files, _ = build_file_tools(workspace)
        assert await list_files.execute({"subdirectory": "empty"}, ctx) == "(empty)"

    async def test_listing_a_file_is_an_error(self, workspace, ctx) -> None:
        list_files, _ = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError):
            await list_files.execute({"subdirectory": "notes.txt"}, ctx)

    async def test_reading_a_file(self, workspace, ctx) -> None:
        _, read_file = build_file_tools(workspace)
        assert await read_file.execute({"path": "notes.txt"}, ctx) == "hello from the workspace"

    async def test_reading_a_missing_file_is_an_error(self, workspace, ctx) -> None:
        _, read_file = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError):
            await read_file.execute({"path": "nope.txt"}, ctx)

    @pytest.mark.parametrize("path", ["../../../etc/passwd", "sub/../../outside.txt"])
    async def test_traversal_out_of_the_workspace_is_refused(
        self, workspace, ctx, path: str
    ) -> None:
        _, read_file = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError) as excinfo:
            await read_file.execute({"path": path}, ctx)
        assert "escapes the workspace" in str(excinfo.value)

    async def test_an_absolute_path_is_reinterpreted_as_workspace_relative(
        self, workspace, ctx
    ) -> None:
        """The leading slash is stripped rather than honoured, so `/etc/passwd` resolves to
        `<workspace>/etc/passwd` -- contained, and simply absent."""
        _, read_file = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError) as excinfo:
            await read_file.execute({"path": "/etc/passwd"}, ctx)
        assert "is not a file" in str(excinfo.value)

    async def test_a_symlink_out_of_the_workspace_is_refused(self, workspace, ctx) -> None:
        """The classic bypass: containment must be checked after resolution, not before."""
        outside = workspace.parent / "outside.txt"
        outside.write_text("should never be readable", encoding="utf-8")
        (workspace / "escape.txt").symlink_to(outside)

        _, read_file = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError) as excinfo:
            await read_file.execute({"path": "escape.txt"}, ctx)
        assert "escapes the workspace" in str(excinfo.value)

    async def test_a_null_byte_in_a_path_is_refused(self, workspace, ctx) -> None:
        _, read_file = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError) as excinfo:
            await read_file.execute({"path": "notes\x00.txt"}, ctx)
        assert "null byte" in str(excinfo.value)

    async def test_a_file_over_the_size_limit_is_refused(self, workspace, ctx) -> None:
        (workspace / "big.txt").write_text("x" * 5000, encoding="utf-8")
        _, read_file = build_file_tools(workspace, max_bytes=1000)
        with pytest.raises(ToolExecutionError) as excinfo:
            await read_file.execute({"path": "big.txt"}, ctx)
        assert "exceeding" in str(excinfo.value)

    async def test_a_binary_file_is_refused_with_a_clear_reason(self, workspace, ctx) -> None:
        (workspace / "image.bin").write_bytes(b"\xff\xfe\x00\x01binary")
        _, read_file = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError) as excinfo:
            await read_file.execute({"path": "image.bin"}, ctx)
        assert "UTF-8" in str(excinfo.value)

    async def test_reading_a_directory_is_an_error(self, workspace, ctx) -> None:
        _, read_file = build_file_tools(workspace)
        with pytest.raises(ToolExecutionError):
            await read_file.execute({"path": "sub"}, ctx)

    def test_the_workspace_is_created_if_absent(self, tmp_path) -> None:
        target = tmp_path / "does" / "not" / "exist"
        build_file_tools(target)
        assert target.is_dir()

    def test_writing_is_deliberately_not_offered(self, workspace) -> None:
        names = {t.spec.name for t in build_file_tools(workspace)}
        assert names == {"list_files", "read_file"}


class TestUrlGuard:
    ALLOWED = frozenset({"example.com"})

    def test_an_allowlisted_host_passes(self) -> None:
        assert _assert_public_url("https://example.com/page", self.ALLOWED, "t")

    def test_a_subdomain_of_an_allowlisted_host_passes(self) -> None:
        assert _assert_public_url("https://docs.example.com/page", self.ALLOWED, "t")

    def test_a_lookalike_suffix_is_refused(self) -> None:
        """`notexample.com` ends with `example.com` as a string but is a different domain."""
        with pytest.raises(ToolExecutionError):
            _assert_public_url("https://notexample.com/", self.ALLOWED, "t")

    def test_plain_http_is_refused(self) -> None:
        with pytest.raises(ToolExecutionError) as excinfo:
            _assert_public_url("http://example.com/", self.ALLOWED, "t")
        assert "https" in str(excinfo.value)

    @pytest.mark.parametrize("scheme_url", ["file:///etc/passwd", "gopher://example.com/"])
    def test_other_schemes_are_refused(self, scheme_url: str) -> None:
        with pytest.raises(ToolExecutionError):
            _assert_public_url(scheme_url, self.ALLOWED, "t")

    @pytest.mark.parametrize(
        "url",
        [
            "https://169.254.169.254/latest/meta-data/",  # cloud instance credentials
            "https://127.0.0.1/admin",
            "https://10.0.0.5/internal",
            "https://192.168.1.1/",
            "https://172.16.0.1/",
            "https://localhost/admin",
            "https://metadata.google.internal/",
        ],
    )
    def test_addresses_inside_the_deployment_are_refused(self, url: str) -> None:
        """Without this an agent with an HTTP tool is an SSRF proxy into the VPS's own network."""
        with pytest.raises(ToolExecutionError):
            _assert_public_url(url, frozenset({"example.com", "169.254.169.254", "127.0.0.1",
                                               "10.0.0.5", "192.168.1.1", "172.16.0.1"}), "t")

    def test_a_url_with_no_host_is_refused(self) -> None:
        with pytest.raises(ToolExecutionError):
            _assert_public_url("https:///path", self.ALLOWED, "t")


class TestHttpTool:
    def test_an_empty_allowlist_produces_no_tool(self) -> None:
        """Advertising a capability that refuses every call wastes a generation and misleads."""
        assert build_http_tool(allowed_hosts=[]) == []
        assert build_http_tool(allowed_hosts=["", "  "]) == []

    def test_the_description_names_the_permitted_hosts(self) -> None:
        (fetch,) = build_http_tool(allowed_hosts=["Example.COM", ".docs.internal"])
        assert "example.com" in fetch.spec.description
        assert "docs.internal" in fetch.spec.description

    async def test_a_disallowed_host_is_refused_before_any_request(self, ctx) -> None:
        (fetch,) = build_http_tool(allowed_hosts=["example.com"])
        with pytest.raises(ToolExecutionError):
            await fetch.execute({"url": "https://evil.test/"}, ctx)


class TestWeatherTool:
    def test_it_needs_no_credential(self) -> None:
        """Chosen so a working demo tool stays out of the credential management path."""
        (weather,) = build_weather_tool()
        assert weather.spec.sensitivity is ToolSensitivity.PUBLIC
        assert weather.spec.required_capability is None


class TestAssembly:
    def test_a_default_install_can_reach_nothing(self) -> None:
        names = {t.spec.name for t in default_tools()}
        assert names == {
            "calculator", "current_datetime", "prioritize_tasks", "find_calendar_slots",
            "propose_task", "propose_event", "propose_expense",
            "propose_income", "propose_health_log",
        }

    def test_each_capability_is_opt_in(self, knowledge, memory, tmp_path) -> None:
        names = {
            t.spec.name
            for t in default_tools(
                knowledge_engine=knowledge,
                memory_engine=memory,
                workspace=tmp_path,
                allowed_http_hosts=["example.com"],
                enable_weather=True,
            )
        }
        assert names == {
            "calculator", "current_datetime", "prioritize_tasks", "find_calendar_slots",
            "search_knowledge", "recall_memory",
            "remember_fact", "list_files", "read_file", "fetch_url", "get_weather",
            "propose_task", "propose_event", "propose_expense", "propose_income",
            "propose_health_log",
        }

    def test_only_personal_os_tools_are_side_effecting_by_default(self) -> None:
        """Personal-OS tools propose client writes; they do not touch server state."""
        side = {t.spec.name for t in default_tools() if t.spec.side_effecting}
        assert side == PERSONAL_OS_TOOL_NAMES
