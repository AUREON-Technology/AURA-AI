"""Fitness: tools that fetch arbitrary external content are tagged so the
registry can taint the turn on their output (M4). See
tests/security/test_prompt_injection.py::TestUntrustedContentTaint for the
behavioural test of what the tag actually does.

There is no structural signal on a ToolSpec that says "this tool's output is
attacker-influenceable external content" other than the tag itself, so this
is deliberately a pinned allowlist, not a general heuristic: adding a new
tool that fetches arbitrary content (a Gmail/GitHub/Drive content-read tool,
another web-fetch variant) must add it here too, or this test won't catch
the gap on its own. What it does catch is the tag being silently dropped
from an existing entry.
"""

from __future__ import annotations

from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemorySandboxRunner
from aura_core.tools.builtin import default_tools
from aura_core.tools.coding import build_mcp_bridge_tools
from aura_core.ports.coding import McpToolDescriptor

_EXPECTED_UNTRUSTED_CONTENT_TOOLS = {
    "fetch_url",
    # run_command's stdout/stderr can echo file contents, comments, or anything else an attacker
    # placed in the sandbox workspace (a cloned repo, a fetched issue) -- same reasoning as
    # fetch_url's page content.
    "run_command",
    # These read attacker-influenceable GitHub content (issue/PR bodies, file contents) exactly
    # like fetch_url reads attacker-influenceable page content.
    "github_get_file_contents",
    "github_get_issue",
    "github_get_pull_request",
}


async def _github_client_resolver(user_id):
    return InMemoryGitHubClient()


def test_known_content_fetching_tools_are_tagged_untrusted_content() -> None:
    tools = default_tools(
        allowed_http_hosts=("example.com",),
        sandbox_runner=InMemorySandboxRunner(),
        github_client_resolver=_github_client_resolver,
        github_default_repo="acme/widgets",
    )
    by_name = {t.spec.name: t.spec for t in tools}

    for name in _EXPECTED_UNTRUSTED_CONTENT_TOOLS:
        assert name in by_name, f"expected tool {name!r} to be registered"
        assert "untrusted_content" in by_name[name].tags, (
            f"{name!r} fetches arbitrary external content but lost its "
            "untrusted_content tag -- registry.execute() will no longer "
            "taint the turn on its output (M4)"
        )


def test_low_risk_tools_are_not_tagged() -> None:
    """Sanity check the tag isn't applied indiscriminately -- tools that
    can't return attacker-controlled text should stay untagged."""
    tools = default_tools(
        sandbox_runner=InMemorySandboxRunner(), github_client_resolver=_github_client_resolver,
        github_default_repo="acme/widgets",
    )
    by_name = {t.spec.name: t.spec for t in tools}

    assert "calculator" in by_name
    assert "untrusted_content" not in by_name["calculator"].tags
    # write_file/edit_file/git_* operate on the engineer agent's own sandbox workspace; their
    # inputs are model-supplied arguments (already handled as untrusted at the schema-validation
    # layer), not externally-fetched content, so they are not tagged the same way run_command is.
    for name in ("write_file", "edit_file", "git_diff", "git_commit", "git_checkout_branch"):
        assert name in by_name
        assert "untrusted_content" not in by_name[name].tags


def test_delegate_to_agent_is_tagged_untrusted_content() -> None:
    """`delegate_to_agent` (aura_core/tools/delegation.py) isn't assembled by `default_tools()` --
    it needs a live kernel resolver, wired separately in `Container.__init__` after the kernel
    itself exists -- so it's built directly here rather than through the fixture the other tests
    in this file share. A delegated sub-turn's answer can reflect tainted retrieval/tool output
    the sub-persona itself pulled in, same reasoning as fetch_url's own tag."""
    from aura_core.agents.registry import build_default_agent_registry
    from aura_core.tools.delegation import build_delegation_tools

    tools = build_delegation_tools(agents=build_default_agent_registry(), kernel=lambda: None)
    by_name = {t.spec.name: t.spec for t in tools}
    assert "delegate_to_agent" in by_name
    assert "untrusted_content" in by_name["delegate_to_agent"].tags


async def test_mcp_bridged_tools_default_to_tagged_untrusted_content() -> None:
    """A discovered MCP tool is untrusted by default (M4's same default-deny reasoning as any
    other externally-sourced content) unless an operator explicitly reviewed and allowlisted it
    (`AURA_MCP_SAFE_TOOLS`)."""

    class _Client:
        async def list_tools(self):
            return [McpToolDescriptor(server="fs", name="read_file", description="Read a file.")]

        async def call_tool(self, server, name, arguments):  # pragma: no cover - not exercised
            return ""

    tools = await build_mcp_bridge_tools(_Client())
    by_name = {t.spec.name: t.spec for t in tools}
    assert "mcp_fs_read_file" in by_name
    assert "untrusted_content" in by_name["mcp_fs_read_file"].tags


class _PairedDesktop:
    async def resolve(self, user_id):
        return object()


def test_adr_036_tools_that_run_code_are_tagged_untrusted_content() -> None:
    """pytest output and `docker build` output echo whatever the project prints, so a turn that
    called one is tainted exactly as one that called `run_command` (ADR-036 groups B and C). The
    pure analysis group (group A) takes inline text and returns findings about it: not tagged."""
    from aura_core.tools.analysis import ANALYSIS_TOOL_NAMES
    from aura_core.tools.desktop_ops import DESKTOP_OPS_TOOL_NAMES
    from aura_core.tools.open_exec import OPEN_EXEC_TOOL_NAMES

    tools = default_tools(
        sandbox_runner=InMemorySandboxRunner(),
        host_agent_registry=_PairedDesktop(),
        enable_analysis_tools=True,
        enable_open_exec_tools=True,
        enable_desktop_ops_tools=True,
    )
    by_name = {t.spec.name: t.spec for t in tools}

    for name in OPEN_EXEC_TOOL_NAMES | DESKTOP_OPS_TOOL_NAMES:
        assert name in by_name, f"{name!r} was not built, so this pin is vacuous"
        assert "untrusted_content" in by_name[name].tags
    for name in ANALYSIS_TOOL_NAMES:
        assert "untrusted_content" not in by_name[name].tags


def test_adr_040_readers_and_search_are_tagged_untrusted_content() -> None:
    """File readers return whatever the file's author wrote and search returns other people's page
    titles: a turn that called one is tainted exactly as one that called fetch_url (ADR-042)."""
    from aura_core.tools.media import MEDIA_TOOL_NAMES, build_media_tools
    from aura_core.tools.websearch import build_web_search_tool

    class _Ocr:
        name = "ocr"

        async def image_text(self, image, *, page):
            return ""

        async def image_data(self, image, *, page):
            raise NotImplementedError

    class _Stt:
        name = "stt"

        async def transcribe(self, audio, *, language=None):
            return ""

        async def transcribe_segments(self, audio, *, language=None, word_timestamps=True):
            raise NotImplementedError

    tools = build_media_tools("/tmp", ocr_engine=_Ocr(), stt_engine=_Stt())
    tools += build_web_search_tool(base_url="http://searxng:8080")
    by_name = {t.spec.name: t.spec for t in tools}
    assert MEDIA_TOOL_NAMES | {"web_search"} == set(by_name)
    for name, spec in by_name.items():
        assert "untrusted_content" in spec.tags, name
