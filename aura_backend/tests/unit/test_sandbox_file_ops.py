"""``delete_file`` / ``rename_file``: the server-side runners' rules, the tools on top of them, and
the desktop bridge's wire calls.

Both server-side runners run every case (``DockerSandboxRunner`` needs no Docker for file operations:
they act on its scratch directory directly), because the in-memory one is what the rest of the suite
trusts and the two must not drift. What is asserted is what is left on disk, not which helper ran.
The path-escape and symlink cases are in ``tests/security/test_workspace_boundaries.py``.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.adapters.docker_sandbox import DockerSandboxRunner
from aura_core.adapters.host_agent import (
    CompositeSandboxRunner,
    HostAgentRegistry,
    HostAgentSandboxRunner,
    HostAgentSession,
)
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.ports.coding import TOOL_CALL_CONFIRMED
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools


@pytest.fixture(params=["docker", "in_memory"])
def runner(request, tmp_path):
    if request.param == "docker":
        return DockerSandboxRunner(image="python:3.12-slim", network="", host_root=str(tmp_path / "sandboxes"))
    return InMemorySandboxRunner()


@pytest.fixture
async def workspace(runner):
    handle = await runner.create_task()
    return handle, runner._scratch(handle).resolve()


def _put(scratch, relative: str, content: str = "x"):
    target = scratch / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


class TestDeleteFile:
    async def test_removes_the_named_file_and_nothing_else(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "a.txt", "a")
        _put(scratch, "b.txt", "b")
        _put(scratch, "sub/c.txt", "c")

        await runner.delete_file(handle, "a.txt")

        assert not (scratch / "a.txt").exists()
        assert (scratch / "b.txt").read_text() == "b" and (scratch / "sub/c.txt").read_text() == "c"

    async def test_accepts_a_nested_path_and_a_leading_slash(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "sub/deep/c.txt")
        _put(scratch, "d.txt")

        await runner.delete_file(handle, "sub/deep/c.txt")
        await runner.delete_file(handle, "/d.txt")

        assert not (scratch / "sub/deep/c.txt").exists() and not (scratch / "d.txt").exists()

    async def test_refuses_a_directory_and_keeps_what_is_in_it(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "dir/keep.txt", "keep")

        with pytest.raises(Exception, match="is a directory"):
            await runner.delete_file(handle, "dir")

        assert (scratch / "dir/keep.txt").read_text() == "keep"

    async def test_refuses_a_null_byte_with_a_message_the_agent_can_act_on(self, runner, workspace) -> None:
        handle, scratch = workspace
        victim = _put(scratch, "a.txt", "keep")
        with pytest.raises(Exception, match="null byte"):
            await runner.delete_file(handle, "a.txt\x00b")
        with pytest.raises(Exception, match="null byte"):
            await runner.rename_file(handle, "a.txt", "x\x00y")
        assert victim.read_text() == "keep"

    async def test_refuses_a_missing_file_and_the_workspace_root(self, runner, workspace) -> None:
        handle, scratch = workspace
        with pytest.raises(Exception, match="does not exist"):
            await runner.delete_file(handle, "nope.txt")
        for root_like in (".", "", "sub/.."):
            with pytest.raises(Exception):
                await runner.delete_file(handle, root_like)
        assert scratch.exists()

    @pytest.mark.parametrize("path", [".git/index", ".git/hooks/pre-commit", "sub/.git/HEAD", ".GIT/config"])
    async def test_refuses_anything_inside_a_git_directory(self, runner, workspace, path: str) -> None:
        handle, scratch = workspace
        target = _put(scratch, path, "keep")

        with pytest.raises(Exception, match=r"\.git"):
            await runner.delete_file(handle, path)

        assert target.read_text() == "keep"

    async def test_names_that_only_start_with_dot_git_are_not_refused(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, ".gitignore")
        _put(scratch, ".github/ci.yml")

        await runner.delete_file(handle, ".gitignore")
        await runner.delete_file(handle, ".github/ci.yml")

        assert not (scratch / ".gitignore").exists() and not (scratch / ".github/ci.yml").exists()


class TestRenameFile:
    async def test_moves_a_file_keeping_its_content(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "old.txt", "content")

        await runner.rename_file(handle, "old.txt", "new.txt")

        assert not (scratch / "old.txt").exists()
        assert (scratch / "new.txt").read_text() == "content"

    async def test_creates_the_destination_directories(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "old.txt", "content")

        await runner.rename_file(handle, "old.txt", "a/b/c/new.txt")

        assert (scratch / "a/b/c/new.txt").read_text() == "content"

    async def test_moves_a_directory_with_everything_in_it(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "src/one.txt", "1")
        _put(scratch, "src/inner/two.txt", "2")

        await runner.rename_file(handle, "src", "lib")

        assert not (scratch / "src").exists()
        assert (scratch / "lib/one.txt").read_text() == "1" and (scratch / "lib/inner/two.txt").read_text() == "2"

    async def test_never_replaces_an_existing_entry(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "a.txt", "a")
        _put(scratch, "b.txt", "b")

        with pytest.raises(Exception, match="already exists"):
            await runner.rename_file(handle, "a.txt", "b.txt")
        with pytest.raises(Exception, match="already exists"):
            await runner.rename_file(handle, "a.txt", "a.txt")

        assert (scratch / "a.txt").read_text() == "a" and (scratch / "b.txt").read_text() == "b"

    async def test_refuses_a_missing_source_and_a_directory_moved_into_itself(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, "dir/f.txt", "f")

        with pytest.raises(Exception, match="does not exist"):
            await runner.rename_file(handle, "nope.txt", "x.txt")
        with pytest.raises(Exception, match="into itself"):
            await runner.rename_file(handle, "dir", "dir/inner")

        assert (scratch / "dir/f.txt").read_text() == "f"

    @pytest.mark.parametrize("destination", [".git/hooks/pre-commit", ".git/config", ".git/anything", "sub/.git"])
    async def test_refuses_to_move_into_a_git_directory(self, runner, workspace, destination: str) -> None:
        handle, scratch = workspace
        _put(scratch, "a.txt", "a")

        with pytest.raises(Exception, match=r"\.git"):
            await runner.rename_file(handle, "a.txt", destination)

        assert (scratch / "a.txt").read_text() == "a" and not (scratch / destination).exists()

    async def test_refuses_to_move_a_git_directory_or_anything_in_it(self, runner, workspace) -> None:
        handle, scratch = workspace
        _put(scratch, ".git/index", "idx")

        for source in (".git/index", ".git"):
            with pytest.raises(Exception, match=r"\.git"):
                await runner.rename_file(handle, source, "elsewhere")

        assert (scratch / ".git/index").read_text() == "idx" and not (scratch / "elsewhere").exists()


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-file-ops-1", deadline_s=30.0)


class TestTools:
    """The two tools the Engineer agent calls, on the shared per-turn sandbox: a file written, then
    renamed, then deleted is visible to ``run_command`` at every step."""

    async def test_write_rename_delete_are_seen_by_run_command(self, ctx) -> None:
        tools = {t.spec.name: t for t in build_sandbox_tools(InMemorySandboxRunner())}

        await tools["write_file"].execute({"path": "draft.txt", "content": "hello"}, ctx)
        assert await tools["rename_file"].execute({"path": "draft.txt", "new_path": "docs/final.txt"}, ctx) == (
            "Renamed draft.txt to docs/final.txt"
        )
        listing = await tools["run_command"].execute({"command": "ls draft.txt docs/final.txt; cat docs/final.txt"}, ctx)
        assert "hello" in listing and "docs/final.txt" in listing

        assert await tools["delete_file"].execute({"path": "docs/final.txt"}, ctx) == "Deleted docs/final.txt"
        after = await tools["run_command"].execute({"command": "ls docs"}, ctx)
        assert "final.txt" not in after

    async def test_a_refused_operation_is_a_tool_error_the_agent_can_read(self, ctx) -> None:
        tools = {t.spec.name: t for t in build_sandbox_tools(InMemorySandboxRunner())}
        await tools["write_file"].execute({"path": "keep.txt", "content": "keep"}, ctx)

        with pytest.raises(ToolExecutionError, match="does not exist"):
            await tools["delete_file"].execute({"path": "missing.txt"}, ctx)
        with pytest.raises(ToolExecutionError, match="already exists"):
            await tools["rename_file"].execute({"path": "keep.txt", "new_path": "keep.txt"}, ctx)
        with pytest.raises(ToolExecutionError):
            await tools["delete_file"].execute({"path": "../outside.txt"}, ctx)

    async def test_they_are_engineer_tools_that_carry_the_workspace_local_exemption(self) -> None:
        tools = {t.spec.name: t.spec for t in build_sandbox_tools(InMemorySandboxRunner())}
        for name in ("delete_file", "rename_file"):
            spec = tools[name]
            assert spec.required_capability == "engineer_agent", name
            assert spec.side_effecting and spec.workspace_local and not spec.requires_confirmation, name
        # Long enough for a person to answer the desktop's approval prompt (the runner waits 330 s).
        assert tools["delete_file"].timeout_s >= 330 and tools["rename_file"].timeout_s >= 330


class _FakeDaemon:
    """A stand-in desktop daemon that records every call and answers each with an empty result."""

    def __init__(self, user: str, task_id: str = "task-1") -> None:
        self.calls: list[tuple[str, dict]] = []
        self._task_id = task_id
        self.session = HostAgentSession(UserId(user), self._send)

    async def _send(self, frame) -> None:
        self.calls.append((frame["method"], dict(frame["params"])))
        result = {"task_id": self._task_id} if frame["method"] == "create_task" else {}
        self.session.resolve({"type": "result", "id": frame["id"], "result": result})


class TestDesktopBridge:
    async def test_delete_and_rename_are_one_rpc_each_carrying_the_task_and_paths(self) -> None:
        registry = HostAgentRegistry()
        daemon = _FakeDaemon("u1")
        await registry.register(daemon.session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        await runner.delete_file(handle, "old.txt")
        await runner.rename_file(handle, "a.txt", "b/c.txt")

        assert daemon.calls[1:] == [
            ("delete_file", {"task_id": "task-1", "path": "old.txt"}),
            ("rename_file", {"task_id": "task-1", "path": "a.txt", "new_path": "b/c.txt"}),
        ]

    async def test_the_daemon_s_refusal_reaches_the_caller(self) -> None:
        registry = HostAgentRegistry()
        daemon = _FakeDaemon("u1")

        async def refuse(frame) -> None:
            if frame["method"] == "create_task":
                daemon.session.resolve({"type": "result", "id": frame["id"], "result": {"task_id": "t"}})
            else:
                daemon.session.resolve({"type": "error", "id": frame["id"], "message": "write was rejected by the user: old.txt"})

        daemon.session = HostAgentSession(UserId("u1"), refuse)
        await registry.register(daemon.session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        with pytest.raises(Exception, match="rejected by the user"):
            await runner.delete_file(handle, "old.txt")

    async def test_confirmation_is_forwarded_only_for_a_call_the_person_approved(self) -> None:
        registry = HostAgentRegistry()
        daemon = _FakeDaemon("u1")
        await registry.register(daemon.session)
        runner = HostAgentSandboxRunner(registry)
        handle = await runner.create_task(user_id=UserId("u1"))

        await runner.delete_file(handle, "plain.txt")
        token = TOOL_CALL_CONFIRMED.set(True)
        try:
            await runner.delete_file(handle, "approved.txt")
            await runner.rename_file(handle, "approved-a.txt", "approved-b.txt")
        finally:
            TOOL_CALL_CONFIRMED.reset(token)

        by_path = {params["path"]: params for method, params in daemon.calls if method != "create_task"}
        assert "confirmed" not in by_path["plain.txt"]
        assert by_path["approved.txt"]["confirmed"] is True
        assert by_path["approved-a.txt"]["confirmed"] is True

    async def test_the_composite_sends_each_call_to_the_runner_that_owns_the_handle(self, tmp_path) -> None:
        registry = HostAgentRegistry()
        daemon = _FakeDaemon("u1")
        await registry.register(daemon.session)
        fallback = InMemorySandboxRunner()
        composite = CompositeSandboxRunner(host_runner=HostAgentSandboxRunner(registry), fallback=fallback)

        on_desktop = await composite.create_task(user_id=UserId("u1"))
        await composite.delete_file(on_desktop, "desktop.txt")
        assert [m for m, _ in daemon.calls] == ["create_task", "delete_file"]

        on_server = await composite.create_task(user_id=UserId("nobody-paired"))
        scratch = fallback._scratch(on_server)
        (scratch / "server.txt").write_text("x")
        await composite.rename_file(on_server, "server.txt", "moved.txt")
        await composite.delete_file(on_server, "moved.txt")

        assert list(scratch.iterdir()) == []
        assert [m for m, _ in daemon.calls] == ["create_task", "delete_file"], "the server call reached the desktop"
