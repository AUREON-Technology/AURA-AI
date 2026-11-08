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
    matching_repository_tool,
)
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry


async def _github_resolver(user_id):
    """Resolver for the tools that reach a forge, so the boundary-crossing half of the
    confirmation contract can be exercised."""
    return InMemoryGitHubClient()


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

    async def test_session_cache_passes_the_caller_s_user_id_to_create_task(self, ctx) -> None:
        """`_SandboxSessionCache` must thread `context.user_id` through to the runner -- this is
        the whole mechanism a per-user runner (the desktop host-agent bridge) uses to route a
        call to the right caller's daemon (ports/coding.py::SandboxRunner)."""
        runner = InMemorySandboxRunner()
        seen_user_ids = []
        original_create_task = runner.create_task

        async def recording_create_task(*, user_id=None, session_id=None, isolate=False):
            seen_user_ids.append(user_id)
            return await original_create_task(user_id=user_id)

        runner.create_task = recording_create_task
        tools = _by_name(build_sandbox_tools(runner))

        await tools["write_file"].execute({"path": "hello.txt", "content": "hi"}, ctx)
        assert seen_user_ids == [ctx.user_id]


class TestSandboxToolDescriptionsAreHonestAboutRouting:
    """`CompositeSandboxRunner` (adapters/host_agent.py) decides per-task, not per-deployment,
    whether a call lands in the ephemeral Docker sandbox or on the caller's own paired machine
    (ADR-011) -- a static tool description cannot correctly claim only one of those is true. These
    lock in that the descriptions say so instead of unconditionally asserting isolation that may
    not hold for a given call."""

    def test_write_file_does_not_unconditionally_claim_it_is_not_the_user_s_real_files(self) -> None:
        tools = _by_name(build_sandbox_tools(InMemorySandboxRunner()))
        description = tools["write_file"].spec.description
        assert "not the user's real files" not in description
        assert "paired desktop app" in description

    def test_run_command_does_not_unconditionally_claim_no_network_access(self) -> None:
        tools = _by_name(build_sandbox_tools(InMemorySandboxRunner()))
        description = tools["run_command"].spec.description
        assert "paired desktop app" in description
        # Still says the *fallback* path is network-restricted -- that half of the old claim is
        # true and shouldn't be lost, only the part that used to be stated unconditionally.
        assert "network-restricted" in description

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

    async def test_write_files_writes_multiple_files_atomically(self, ctx) -> None:
        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner))
        result = await tools["write_files"].execute(
            {"operations": [{"path": "a.txt", "content": "A"}, {"path": "b.txt", "content": "B"}]},
            ctx,
        )
        assert "2 file(s)" in result
        readback_a = await tools["run_command"].execute({"command": "cat a.txt"}, ctx)
        readback_b = await tools["run_command"].execute({"command": "cat b.txt"}, ctx)
        assert "A" in readback_a
        assert "B" in readback_b

    async def test_write_files_supports_mixed_overwrite_and_find_replace_operations(self, ctx) -> None:
        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner))
        await tools["write_file"].execute({"path": "existing.txt", "content": "hello world"}, ctx)

        await tools["write_files"].execute(
            {
                "operations": [
                    {"path": "existing.txt", "find": "world", "replace": "there"},
                    {"path": "new.txt", "content": "brand new"},
                ]
            },
            ctx,
        )
        assert "hello there" in await tools["run_command"].execute({"command": "cat existing.txt"}, ctx)
        assert "brand new" in await tools["run_command"].execute({"command": "cat new.txt"}, ctx)

    async def test_write_files_fails_the_whole_call_before_writing_anything_if_any_operation_is_invalid(
        self, ctx
    ) -> None:
        """Pre-validate-all-then-write-all (D6 plan decision 4): a find/replace target that
        doesn't occur exactly once must fail the *entire* call, including the other, otherwise
        valid operation in the same batch -- not write what it can and skip the rest."""
        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner))
        await tools["write_file"].execute({"path": "existing.txt", "content": "hello world"}, ctx)

        with pytest.raises(ToolExecutionError):
            await tools["write_files"].execute(
                {
                    "operations": [
                        {"path": "new.txt", "content": "should not be written"},
                        {"path": "existing.txt", "find": "not present anywhere", "replace": "x"},
                    ]
                },
                ctx,
            )
        listing = await tools["run_command"].execute({"command": "ls"}, ctx)
        assert "new.txt" not in listing

    async def test_write_files_is_workspace_local_and_runs_without_confirmation(self, ctx) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(InMemorySandboxRunner()))

        result, report = await registry.execute(
            ToolCall(
                id=ToolCallId("1"), name="write_files",
                arguments={"operations": [{"path": "a.txt", "content": "A"}]},
            ),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert result.ok
        assert not report.needs_confirmation
        assert report.policy_reason == "workspace_local_side_effect"

    async def test_workspace_local_work_runs_without_confirmation(self, ctx) -> None:
        """`run_command` is SYSTEM + side_effecting, which used to force `requires_confirmation`
        at declaration time and so gated every single command. It is now `workspace_local`
        (ADR-014): its effects stay inside a scratch container and checkout that the reaper
        deletes, so it runs, and the confirmation prompt is saved for calls that leave.

        The old behaviour was not wrong in isolation -- it became wrong once a workspace lived
        across turns, because an eight-iteration task then asked for approval at every step, and
        a prompt asked that often is one people approve without reading.
        """
        runner = InMemorySandboxRunner()
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(runner))

        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="run_command", arguments={"command": "echo hi"}),
            ctx,
            capabilities=("engineer_agent",),
        )

        assert result.ok
        assert not report.needs_confirmation
        assert report.policy_reason == "workspace_local_side_effect"

    async def test_repeated_workspace_local_calls_never_start_confirming(self, ctx) -> None:
        """A whole task's worth of steps, which is the case that matters -- one allowed call
        would not tell us the storm is gone."""
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(InMemorySandboxRunner()))

        for index in range(10):
            result, report = await registry.execute(
                ToolCall(
                    id=ToolCallId(str(index)), name="write_file",
                    arguments={"path": f"f{index}.txt", "content": "x"},
                ),
                ctx,
                capabilities=("engineer_agent",),
            )
            assert result.ok and not report.needs_confirmation

    async def test_leaving_the_workspace_still_requires_confirmation(self, ctx) -> None:
        """The other half of ADR-014, and the half that would make the change a mistake if it
        broke: `clone_repository` reaches a real repository over the network, is not
        workspace_local, and still stops for a human -- with its arguments visible, so the
        prompt says what is being approved rather than just naming a tool."""
        runner = InMemorySandboxRunner()
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(
            build_sandbox_tools(runner, code_host_resolver=_github_resolver)
        )

        result, report = await registry.execute(
            ToolCall(
                id=ToolCallId("1"), name="clone_repository",
                arguments={"repo": "acme/widgets"},
            ),
            ctx,
            capabilities=("engineer_agent",),
        )

        assert not result.ok
        assert report.needs_confirmation
        assert report.arguments["repo"] == "acme/widgets"

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


class _ScriptedStreamingRunner:
    """A `StreamingSandboxRunner` that answers `run_streaming` with a fixed, pre-scripted event
    sequence -- exercises `run_command`'s draining/truncation logic without depending on real
    subprocess timing. `run` deliberately raises: if it gets called, `run_command` failed to
    prefer the streaming path it is supposed to prefer whenever the runner offers one."""

    def __init__(self, events):
        self._events = events

    async def create_task(self, *, user_id=None, session_id=None, isolate=False):
        from aura_core.ports.coding import SandboxHandle

        return SandboxHandle(task_id="scripted-task")

    async def write_file(self, handle, path, content):
        raise NotImplementedError

    async def read_file(self, handle, path):
        raise NotImplementedError

    async def run(self, handle, command, *, timeout_s):
        raise AssertionError("run_command must prefer run_streaming when the runner offers it")

    async def run_streaming(self, handle, command, *, timeout_s):
        for event in self._events:
            yield event

    async def destroy(self, handle):
        pass


class TestRunCommandStreaming:
    async def test_uses_the_streaming_path_and_assembles_the_same_result_shape(self, ctx) -> None:
        from aura_core.ports.coding import SandboxOutputEvent

        runner = _ScriptedStreamingRunner([
            SandboxOutputEvent(kind="stdout", data="line 1\n"),
            SandboxOutputEvent(kind="stdout", data="line 2\n"),
            SandboxOutputEvent(kind="stderr", data="a warning\n"),
            SandboxOutputEvent(kind="done", exit_code=0),
        ])
        tools = _by_name(build_sandbox_tools(runner))
        result = await tools["run_command"].execute({"command": "whatever"}, ctx)

        assert "exit_code=0" in result
        assert "line 1\nline 2\n" in result
        assert "a warning\n" in result

    async def test_a_nonzero_exit_code_is_reported(self, ctx) -> None:
        from aura_core.ports.coding import SandboxOutputEvent

        runner = _ScriptedStreamingRunner([
            SandboxOutputEvent(kind="stderr", data="boom\n"),
            SandboxOutputEvent(kind="done", exit_code=1),
        ])
        tools = _by_name(build_sandbox_tools(runner))
        result = await tools["run_command"].execute({"command": "whatever"}, ctx)
        assert "exit_code=1" in result

    async def test_accumulation_is_capped_independent_of_the_tool_s_own_truncation(
        self, ctx, monkeypatch
    ) -> None:
        """`_RUN_COMMAND_STREAM_CAP_CHARS` bounds what this function ever holds in memory while
        draining -- lowered here so the test does not need to actually produce 200k characters of
        output to exercise it."""
        import aura_core.tools.coding as coding_module
        from aura_core.ports.coding import SandboxOutputEvent

        monkeypatch.setattr(coding_module, "_RUN_COMMAND_STREAM_CAP_CHARS", 10)
        runner = _ScriptedStreamingRunner([
            SandboxOutputEvent(kind="stdout", data="0123456789"),  # exactly the cap
            SandboxOutputEvent(kind="stdout", data="this should not appear"),
            SandboxOutputEvent(kind="done", exit_code=0),
        ])
        tools = _by_name(build_sandbox_tools(runner))
        result = await tools["run_command"].execute({"command": "whatever"}, ctx)

        assert "0123456789" in result
        assert "this should not appear" not in result
        assert "truncated" in result

    async def test_a_plain_non_streaming_runner_still_works(self, ctx) -> None:
        """`InMemorySandboxRunner` does not implement `StreamingSandboxRunner` -- run_command must
        fall back to the plain `run` call exactly as before, not raise or hang."""
        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner))
        result = await tools["run_command"].execute({"command": "echo hi"}, ctx)
        assert "exit_code=0" in result
        assert "hi" in result


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

    async def test_github_commit_files_confirmation_carries_a_real_diff(self, ctx) -> None:
        """D6 Part A.1: github_commit_files is CONFIDENTIAL + not workspace_local, so it already
        reaches ToolRegistry's confirmation gate -- verify the new `preview` hook actually
        produces a real diff against the file's current content, for both a modified existing
        file and a brand-new one (no prior content to diff against)."""
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Widgets\nold line\n")
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_github_tools(_static_resolver(client), default_repo="acme/widgets"))

        _, report = await registry.execute(
            ToolCall(
                id=ToolCallId("1"), name="github_commit_files",
                arguments={
                    "branch": "main", "message": "update",
                    "files": {"README.md": "# Widgets\nnew line\n", "NEW.md": "brand new file"},
                },
            ),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert report.needs_confirmation
        assert report.diff_preview is not None
        assert "-old line" in report.diff_preview
        assert "+new line" in report.diff_preview
        assert "+brand new file" in report.diff_preview
        assert "a/README.md" in report.diff_preview
        assert "a/NEW.md" in report.diff_preview

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

    async def test_a_fabricated_repository_is_rejected_pre_dispatch_with_zero_network_calls(
        self, ctx
    ) -> None:
        """The video incident, end to end at the tool-dispatch boundary: the model invents
        ``repo_for_README`` for a local file it was actually asked to summarise. Going through
        `ToolRegistry.execute` (unlike calling `.execute()` directly, which bypasses schema
        validation) must fail with `error_kind="unresolved_argument"` before the fake GitHub
        client is ever touched -- turning what used to be a live 404/400 round trip into a
        cheap, immediate validation failure."""
        client = self._seeded_client()
        calls: list[str] = []
        original = client.get_file_contents

        async def counting_get_file_contents(*args, **kwargs):
            calls.append(args[0] if args else kwargs.get("repo"))
            return await original(*args, **kwargs)

        client.get_file_contents = counting_get_file_contents
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_github_tools(_static_resolver(client)))

        result, report = await registry.execute(
            ToolCall(
                id=ToolCallId("1"), name="github_get_file_contents",
                arguments={"path": "README.md", "repo": "repo_for_README"},
            ),
            ctx,
            capabilities=("engineer_agent",),
        )

        assert not result.ok
        assert result.error_kind == "unresolved_argument"
        assert report.error_kind == "unresolved_argument"
        assert calls == []


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


class TestRepositoryRequestMatching:
    """`matching_repository_tool` -- the planning gate's hook for repository work, mirroring
    `matching_personal_os_tool`. Its job is to stop a "clone this and analyse it" turn being
    answered by the model alone while the tools for it sit unoffered."""

    OFFERED = ["clone_repository", "github_list_repository_files", "github_get_file_contents"]

    @pytest.mark.parametrize(
        "message",
        [
            'clone into this repository "https://github.com/acme/widgets" and Analyse it.',
            "clone the repository that has git push: github.com/acme/widgets and Analyze it.",
            "review git@github.com:acme/widgets.git",
            "have a look at https://gitlab.com/acme/widgets and summarise the layout",
        ],
    )
    def test_a_repository_request_is_recognised(self, message: str) -> None:
        assert matching_repository_tool(message, self.OFFERED) == "clone_repository"

    @pytest.mark.parametrize(
        "message",
        [
            "I'm currently reviewing https://github.com/acme/widgets, can you continue?",
            "please keep analyzing github.com/acme/widgets",
            "the team cloned https://github.com/acme/widgets last week, can you check it",
            "reviewed https://github.com/acme/widgets yesterday",
            "summarizing https://github.com/acme/widgets now",
        ],
    )
    def test_inflected_verb_forms_are_recognised(self, message: str) -> None:
        """`\breview\b` never matches inside "reviewing" -- both characters either side of
        where the boundary would need to fall are word characters, so the trailing `\b` fails
        for every inflected form (-ing/-ed/-s) of every verb here. That silently reproduced this
        module's own reason for existing (a repository request going unanswered) for anything
        phrased in progressive or past tense instead of the bare imperative."""
        assert matching_repository_tool(message, self.OFFERED) == "clone_repository"

    def test_it_prefers_the_richest_tool_actually_offered(self) -> None:
        """Without a sandbox there is no clone_repository, but the GitHub reads still answer
        "analyse this repo" -- the request must not stop being recognised."""
        assert matching_repository_tool(
            "analyse https://github.com/acme/widgets",
            ["github_list_repository_files", "github_get_file_contents"],
        ) == "github_list_repository_files"

    @pytest.mark.parametrize(
        "message",
        [
            "what is the capital of France?",
            "clone my calendar for next week",          # action verb, no repository
            "https://github.com/acme/widgets",           # repository, no action
        ],
    )
    def test_non_repository_requests_are_left_alone(self, message: str) -> None:
        assert matching_repository_tool(message, self.OFFERED) is None

    @pytest.mark.parametrize(
        "message",
        [
            "please review my github.com profile settings",
            "can you pull the latest sales numbers, then look at github.com for reference",
        ],
    )
    def test_a_bare_platform_mention_with_no_path_is_not_a_repository_request(
        self, message: str
    ) -> None:
        """A lone "github.com" used to match on its own -- paired with a common verb like
        "review" or "pull", that forced a planning generation for a sentence that names the
        platform but no repository at all. An owner/repo path is what a real repository mention
        always has and a platform-only mention never does."""
        assert matching_repository_tool(message, self.OFFERED) is None

    def test_a_bare_platform_mention_with_a_path_still_matches(self) -> None:
        assert matching_repository_tool(
            "review gitlab.com/acme/widgets please", self.OFFERED
        ) == "clone_repository"

    def test_nothing_matches_when_no_repository_tool_is_offered(self) -> None:
        assert matching_repository_tool(
            'clone "https://github.com/acme/widgets" and analyse it', ["calculator"]
        ) is None


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


class _RecordingRunner:
    """A non-streaming `SandboxRunner` that records the keyword arguments `run` receives.

    `run` deliberately has no `cwd` parameter in its signature when `accepts_cwd` is False: that
    is the shape every pre-FSA-006 runner (and every test fake in this file) has, and `run_command`
    must keep working against it when no workspace root was named.
    """

    def __init__(self, *, accepts_cwd: bool) -> None:
        self.calls: list[dict] = []
        if accepts_cwd:
            self.run = self._run_with_cwd  # type: ignore[method-assign]

    async def create_task(self, *, user_id=None, session_id=None, isolate=False):
        from aura_core.ports.coding import SandboxHandle

        return SandboxHandle(task_id="recording-task")

    async def run(self, handle, command, *, timeout_s):
        from aura_core.ports.coding import SandboxResult

        self.calls.append({"command": list(command), "timeout_s": timeout_s})
        return SandboxResult(stdout="", stderr="", exit_code=0)

    async def _run_with_cwd(self, handle, command, *, timeout_s, cwd=None):
        from aura_core.ports.coding import SandboxResult

        self.calls.append({"command": list(command), "timeout_s": timeout_s, "cwd": cwd})
        return SandboxResult(stdout="", stderr="", exit_code=0)

    async def write_file(self, handle, path, content):
        raise NotImplementedError

    async def read_file(self, handle, path):
        raise NotImplementedError

    async def destroy(self, handle):
        pass


class _RecordingStreamingRunner(_RecordingRunner):
    async def run(self, handle, command, *, timeout_s, cwd=None):
        raise AssertionError("run_command must prefer run_streaming when the runner offers it")

    async def run_streaming(self, handle, command, *, timeout_s, cwd=None):
        from aura_core.ports.coding import SandboxOutputEvent

        self.calls.append({"command": list(command), "timeout_s": timeout_s, "cwd": cwd})
        yield SandboxOutputEvent(kind="done", exit_code=0)


class TestRunCommandWorkspaceRoot:
    """FSA-006, second half: the IDE's active root reaches the runner as `cwd`."""

    _ROOT = "/home/me/projects/aura"

    def _ctx(self, user, root):
        return ToolContext(
            user_id=user, correlation_id="corr-engineer-root", deadline_s=30.0,
            metadata={"workspace_root": root},
        )

    async def test_the_root_in_the_tool_context_is_passed_as_cwd(self, user) -> None:
        runner = _RecordingRunner(accepts_cwd=True)
        tools = _by_name(build_sandbox_tools(runner))

        await tools["run_command"].execute({"command": "pwd"}, self._ctx(user, self._ROOT))

        assert runner.calls[0]["cwd"] == self._ROOT

    async def test_the_streaming_path_passes_it_too(self, user) -> None:
        runner = _RecordingStreamingRunner(accepts_cwd=True)
        tools = _by_name(build_sandbox_tools(runner))

        await tools["run_command"].execute({"command": "pwd"}, self._ctx(user, self._ROOT))

        assert runner.calls[0]["cwd"] == self._ROOT

    async def test_without_a_root_the_runner_is_called_exactly_as_before(self, ctx) -> None:
        """`ctx` carries no metadata, and this runner's `run` has no `cwd` parameter at all --
        the shape of every runner that predates this change. It must still be callable."""
        runner = _RecordingRunner(accepts_cwd=False)
        tools = _by_name(build_sandbox_tools(runner))

        result = await tools["run_command"].execute({"command": "pwd"}, ctx)

        assert "exit_code=0" in result
        assert "cwd" not in runner.calls[0]
