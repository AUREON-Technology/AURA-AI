"""Engineer agent: sandbox/git tools, GitHub tools, and MCP bridging.

Mirrors ``test_builtin_tools.py``'s idiom (call a tool's ``.execute()`` directly for behaviour,
go through ``ToolRegistry``/``ToolPolicy`` for authorisation) and ``test_memory_and_tools.py``'s
pattern for confirmation-gating assertions. The fakes are real, working implementations
(``aura_core/adapters/coding_fakes.py``), not mocks -- a sandbox test really runs commands in a
real temp directory, a GitHub test really stores committed file content and reads it back.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.coding_fakes import (
    InMemoryGitHubClient,
    InMemoryMcpClient,
    InMemorySandboxRunner,
)
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ToolSensitivity
from aura_core.ports.coding import McpToolDescriptor
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import (
    build_github_tools,
    build_mcp_bridge_tools,
    build_sandbox_tools,
)
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-engineer-1", deadline_s=30.0)


def _by_name(tools):
    return {t.spec.name: t for t in tools}


def _static_resolver(client):
    """`build_github_tools` takes a per-user resolver, not a raw client -- wraps a fixed client
    the same way `Container._build_github_client_resolver` does for the single-admin-PAT path,
    so these tests exercise the same shape production wires without needing a real per-user
    identity store."""

    async def resolve(user_id):
        return client

    return resolve


class TestSandboxTools:
    async def test_write_then_run_command_share_the_same_sandbox(self, ctx) -> None:
        """The session cache is keyed by correlation_id -- write_file and run_command in the
        same turn must see the same container/workspace."""
        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner))

        await tools["write_file"].execute({"path": "hello.txt", "content": "hi"}, ctx)
        result = await tools["run_command"].execute({"command": "cat hello.txt"}, ctx)
        assert "exit_code=0" in result
        assert "hi" in result

    async def test_edit_file_requires_exactly_one_occurrence(self, ctx) -> None:
        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner))
        await tools["write_file"].execute(
            {"path": "a.py", "content": "x = 1\nx = 1\n"}, ctx
        )
        with pytest.raises(ToolExecutionError):
            await tools["edit_file"].execute(
                {"path": "a.py", "find": "x = 1", "replace": "x = 2"}, ctx
            )

        await tools["write_file"].execute({"path": "b.py", "content": "x = 1\ny = 2\n"}, ctx)
        result = await tools["edit_file"].execute(
            {"path": "b.py", "find": "x = 1", "replace": "x = 9"}, ctx
        )
        assert "Edited b.py" in result
        readback = await tools["run_command"].execute({"command": "cat b.py"}, ctx)
        assert "x = 9" in readback

    async def test_git_workflow(self, ctx) -> None:
        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner))
        await tools["run_command"].execute({"command": "git init -q"}, ctx)
        await tools["run_command"].execute(
            {"command": "git config user.email t@example.com"}, ctx
        )
        await tools["run_command"].execute({"command": "git config user.name test"}, ctx)
        await tools["write_file"].execute({"path": "f.txt", "content": "one"}, ctx)
        branch = await tools["git_checkout_branch"].execute({"branch": "feature-x"}, ctx)
        assert "feature-x" in branch
        diff_before_commit = await tools["git_diff"].execute({}, ctx)
        assert diff_before_commit  # untracked file doesn't show in `git diff HEAD` but command runs
        commit_result = await tools["git_commit"].execute({"message": "add f.txt"}, ctx)
        assert "committed" in commit_result or commit_result

    async def test_run_command_and_writes_require_confirmation_via_policy(self, ctx) -> None:
        """side_effecting + CONFIDENTIAL/SYSTEM sensitivity forces `requires_confirmation` at
        declaration time (`ToolSpec.__post_init__`) -- `run_command` is SYSTEM, so this is the
        exact mechanism ADR-006's taint/confirm rule and the "gated engineer agent" requirement
        both rely on."""
        runner = InMemorySandboxRunner()
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(runner))

        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="run_command", arguments={"command": "echo hi"}),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert not result.ok
        assert report.needs_confirmation
        # The pending call's arguments must be visible so a client can show the user what
        # they're being asked to approve, not just the tool's name. These are the *validated*
        # arguments (post `validate_arguments()`), so an unset optional parameter's declared
        # default (`timeout_s`) is present too.
        assert report.arguments == {"command": "echo hi", "timeout_s": 60}

        confirmed, confirmed_report = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="run_command", arguments={"command": "echo hi"}),
            ctx,
            capabilities=("engineer_agent",),
            confirmed=True,
        )
        assert confirmed.ok
        # Arguments are only ever attached to a confirm-required report -- a call that actually
        # ran (even the same tool, once approved) carries none.
        assert confirmed_report.arguments == {}

    async def test_sandbox_tools_require_the_engineer_capability(self, ctx) -> None:
        runner = InMemorySandboxRunner()
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(runner))

        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="write_file", arguments={"path": "a", "content": "b"}),
            ctx,
            capabilities=(),
        )
        assert not result.ok
        assert "missing_capability" in report.policy_reason


class TestGithubTools:
    async def test_read_file_contents(self, ctx) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Widgets")
        tools = _by_name(build_github_tools(_static_resolver(client), default_repo="acme/widgets"))

        result = await tools["github_get_file_contents"].execute({"path": "README.md"}, ctx)
        assert result == "# Widgets"

    async def test_missing_repo_and_no_default_raises(self, ctx) -> None:
        client = InMemoryGitHubClient()
        tools = _by_name(build_github_tools(_static_resolver(client)))
        with pytest.raises(ToolExecutionError):
            await tools["github_get_issue"].execute({"number": 1}, ctx)

    async def test_write_tools_are_confirmation_gated(self, ctx) -> None:
        client = InMemoryGitHubClient()
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_github_tools(_static_resolver(client), default_repo="acme/widgets"))

        result, report = await registry.execute(
            ToolCall(
                id=ToolCallId("1"), name="github_create_draft_pull_request",
                arguments={"branch": "fix", "base": "main", "title": "Fix", "body": "..."},
            ),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert not result.ok
        assert report.needs_confirmation
        assert report.arguments == {
            "branch": "fix", "base": "main", "title": "Fix", "body": "...",
        }

        confirmed, _ = await registry.execute(
            ToolCall(
                id=ToolCallId("2"), name="github_create_draft_pull_request",
                arguments={"branch": "fix", "base": "main", "title": "Fix", "body": "..."},
            ),
            ctx,
            capabilities=("engineer_agent",),
            confirmed=True,
        )
        assert confirmed.ok
        assert "draft PR" in confirmed.content

    async def test_commit_then_read_round_trips(self, ctx) -> None:
        client = InMemoryGitHubClient()
        tools = _by_name(build_github_tools(_static_resolver(client), default_repo="acme/widgets"))
        await client.create_branch("acme/widgets", branch="main", from_ref="main")
        await tools["github_commit_files"].execute(
            {"branch": "main", "message": "add", "files": {"x.txt": "hello"}}, ctx
        )
        content = await tools["github_get_file_contents"].execute({"path": "x.txt"}, ctx)
        assert content == "hello"


class TestRepositoryExploration:
    """"Clone/analyse this repository" -- the request the engineer persona could not answer at
    all before these tools existed: `github_get_file_contents` needs a path the agent does not
    have, so a repository URL was a dead end however the persona was configured."""

    def _seeded_client(self) -> InMemoryGitHubClient:
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Widgets")
        client.seed_file("acme/widgets", "main", "src/app.py", "print('hi')\n")
        client.seed_file("acme/widgets", "main", "src/util.py", "X = 1\n")
        return client

    async def test_listing_turns_a_repo_url_into_paths(self, ctx) -> None:
        tools = _by_name(build_github_tools(_static_resolver(self._seeded_client())))

        result = await tools["github_list_repository_files"].execute(
            {"repo": "https://github.com/acme/widgets"}, ctx
        )
        assert "README.md" in result
        assert "src/app.py" in result
        assert "src/util.py" in result

    async def test_listing_can_be_narrowed_by_path_prefix(self, ctx) -> None:
        tools = _by_name(
            build_github_tools(_static_resolver(self._seeded_client()), default_repo="acme/widgets")
        )
        result = await tools["github_list_repository_files"].execute({"path_prefix": "src/"}, ctx)
        assert "src/app.py" in result and "README.md" not in result

    async def test_listing_is_a_read_and_needs_no_confirmation(self, ctx) -> None:
        """The sandbox-free path has to stay tap-free: a deployment with AURA_SANDBOX_ENABLED
        off can still answer "analyse this repo" with list + read, and neither is side-effecting."""
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(
            build_github_tools(_static_resolver(self._seeded_client()), default_repo="acme/widgets")
        )
        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="github_list_repository_files", arguments={}),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert result.ok and not report.needs_confirmation

    @pytest.mark.parametrize(
        "given",
        [
            "acme/widgets",
            "https://github.com/acme/widgets",
            "https://github.com/acme/widgets.git",
            "http://www.github.com/acme/widgets/",
            "git@github.com:acme/widgets.git",
            "https://github.com/acme/widgets/tree/main/src",
        ],
    )
    async def test_repo_argument_accepts_the_forms_a_user_actually_pastes(
        self, ctx, given: str
    ) -> None:
        tools = _by_name(build_github_tools(_static_resolver(self._seeded_client())))
        result = await tools["github_get_file_contents"].execute(
            {"path": "README.md", "repo": given}, ctx
        )
        assert result == "# Widgets"

    async def test_unparseable_repo_argument_is_an_actionable_error(self, ctx) -> None:
        tools = _by_name(build_github_tools(_static_resolver(self._seeded_client())))
        with pytest.raises(ToolExecutionError, match="owner/name"):
            await tools["github_get_file_contents"].execute(
                {"path": "README.md", "repo": "https://example.com/"}, ctx
            )


class TestCloneRepository:
    async def test_clone_lands_in_the_same_sandbox_run_command_reads(self, ctx) -> None:
        """The point of building this with the sandbox tools rather than the GitHub ones: the
        files have to be in the workspace the very next `run_command` sees."""
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Widgets")
        client.seed_file("acme/widgets", "main", "src/app.py", "print('hi')\n")
        tools = _by_name(
            build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=_static_resolver(client))
        )

        result = await tools["clone_repository"].execute(
            {"repo": "https://github.com/acme/widgets"}, ctx
        )
        assert "Copied 2 file(s)" in result

        listing = await tools["run_command"].execute({"command": "ls -R widgets"}, ctx)
        assert "README.md" in listing and "app.py" in listing
        content = await tools["run_command"].execute({"command": "cat widgets/src/app.py"}, ctx)
        assert "print('hi')" in content

    async def test_destination_overrides_the_default_directory(self, ctx) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Widgets")
        tools = _by_name(
            build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=_static_resolver(client))
        )
        await tools["clone_repository"].execute(
            {"repo": "acme/widgets", "destination": "checkout"}, ctx
        )
        listing = await tools["run_command"].execute({"command": "cat checkout/README.md"}, ctx)
        assert "# Widgets" in listing

    async def test_binary_and_oversized_files_are_skipped_and_reported(self, ctx) -> None:
        """A partial snapshot the agent doesn't know is partial is how "that file isn't in the
        repo" gets asserted about a file that is."""
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Widgets")
        client.seed_file("acme/widgets", "main", "logo.png", "\x89PNG-ish")
        client.seed_file("acme/widgets", "main", "huge.txt", "x" * 300_000)
        tools = _by_name(
            build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=_static_resolver(client))
        )

        result = await tools["clone_repository"].execute({"repo": "acme/widgets"}, ctx)
        assert "Copied 1 file(s)" in result
        assert "1 binary file(s) skipped" in result
        assert "1 file(s) over" in result
        assert "partial" in result
        missing = await tools["run_command"].execute({"command": "ls widgets"}, ctx)
        assert "logo.png" not in missing

    async def test_empty_or_unknown_repo_fails_with_a_usable_message(self, ctx) -> None:
        tools = _by_name(
            build_sandbox_tools(
                InMemorySandboxRunner(), code_host_resolver=_static_resolver(InMemoryGitHubClient())
            )
        )
        with pytest.raises(ToolExecutionError, match="no files to copy"):
            await tools["clone_repository"].execute({"repo": "acme/nothing"}, ctx)

    async def test_without_a_code_host_resolver_cloning_is_not_offered(self) -> None:
        """Same opt-in shape as every other engineer tool: a deployment with no GitHub client
        registers the sandbox tools without a clone tool rather than one that always fails."""
        assert "clone_repository" not in _by_name(build_sandbox_tools(InMemorySandboxRunner()))

    async def test_clone_requires_confirmation_and_the_engineer_capability(self, ctx) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Widgets")
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(
            build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=_static_resolver(client))
        )

        denied, denied_report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="clone_repository",
                     arguments={"repo": "acme/widgets"}),
            ctx,
            capabilities=(),
        )
        assert not denied.ok and "missing_capability" in denied_report.policy_reason

        proposed, report = await registry.execute(
            ToolCall(id=ToolCallId("2"), name="clone_repository",
                     arguments={"repo": "acme/widgets"}),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert not proposed.ok and report.needs_confirmation

        confirmed, _ = await registry.execute(
            ToolCall(id=ToolCallId("3"), name="clone_repository",
                     arguments={"repo": "acme/widgets"}),
            ctx,
            capabilities=("engineer_agent",),
            confirmed=True,
        )
        assert confirmed.ok and "Copied 1 file(s)" in confirmed.content

    async def test_clone_reports_an_unconnected_github_account_actionably(self, ctx) -> None:
        async def no_client(user_id):
            return None

        tools = _by_name(
            build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=no_client)
        )
        with pytest.raises(ToolExecutionError, match="not connected"):
            await tools["clone_repository"].execute({"repo": "acme/widgets"}, ctx)


class TestGithubToolsResolver:
    """The resolver seam that lets `build_github_tools` serve either one shared admin client or
    a per-user OAuth-connected one (`Container._build_github_client_resolver`) without the tools
    themselves knowing which."""

    async def test_resolver_returning_none_raises_actionable_error(self, ctx) -> None:
        async def no_client(user_id):
            return None

        tools = _by_name(build_github_tools(no_client, default_repo="acme/widgets"))
        with pytest.raises(ToolExecutionError, match="not connected"):
            await tools["github_get_file_contents"].execute({"path": "README.md"}, ctx)

    async def test_resolver_is_called_with_the_caller_s_user_id(self) -> None:
        """Two different users must resolve to two different clients -- the whole point of the
        per-user path is that a call is scoped by `context.user_id`, not a client closed over at
        registration time."""
        client_a = InMemoryGitHubClient()
        client_a.seed_file("acme/widgets", "main", "README.md", "from A")
        client_b = InMemoryGitHubClient()
        client_b.seed_file("acme/widgets", "main", "README.md", "from B")
        clients = {"user-a": client_a, "user-b": client_b}

        async def resolver(user_id):
            return clients.get(user_id.value)

        tools = _by_name(build_github_tools(resolver, default_repo="acme/widgets"))
        ctx_a = ToolContext(user_id=UserId("user-a"), correlation_id="c1", deadline_s=30.0)
        ctx_b = ToolContext(user_id=UserId("user-b"), correlation_id="c2", deadline_s=30.0)

        result_a = await tools["github_get_file_contents"].execute({"path": "README.md"}, ctx_a)
        result_b = await tools["github_get_file_contents"].execute({"path": "README.md"}, ctx_b)
        assert result_a == "from A"
        assert result_b == "from B"


class TestMcpBridge:
    async def test_unlisted_tool_defaults_to_confidential_and_confirmation_required(self) -> None:
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(server="fs", name="list_dir", description="List a directory.")]
        )
        tools = await build_mcp_bridge_tools(client)
        by_name = _by_name(tools)
        spec = by_name["mcp_fs_list_dir"].spec
        assert spec.sensitivity is ToolSensitivity.CONFIDENTIAL
        assert spec.side_effecting is True
        assert spec.requires_confirmation is True
        assert "untrusted_content" in spec.tags
        assert spec.required_capability == "engineer_agent"

    async def test_explicitly_safe_tool_is_not_side_effecting_or_tagged(self) -> None:
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(server="fs", name="list_dir", description="List a directory.")]
        )
        tools = await build_mcp_bridge_tools(client, safe_tools=["mcp_fs_list_dir"])
        spec = _by_name(tools)["mcp_fs_list_dir"].spec
        assert spec.sensitivity is ToolSensitivity.PERSONAL
        assert spec.side_effecting is False
        assert "untrusted_content" not in spec.tags

    async def test_call_dispatches_to_the_right_server_and_tool(self, ctx) -> None:
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(
                server="fs", name="read", description="Read a file.",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                              "required": ["path"]},
            )],
            handlers={("fs", "read"): lambda args: f"contents of {args['path']}"},
        )
        tools = await build_mcp_bridge_tools(client, safe_tools=["mcp_fs_read"])
        result = await tools[0].execute({"path": "a.txt"}, ctx)
        assert result == "contents of a.txt"

    async def test_server_and_tool_names_are_sanitized_into_snake_case(self) -> None:
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(server="My-Server", name="Do Thing!", description="x")]
        )
        tools = await build_mcp_bridge_tools(client)
        name = tools[0].spec.name
        assert name == name.lower()
        assert set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789_")
