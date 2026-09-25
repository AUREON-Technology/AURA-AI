"""Engineer-workspace boundaries: model-supplied paths stay inside the workspace, and a push
cannot reach the default branch by any spelling.

**Paths.** Both sandbox runners (``DockerSandboxRunner`` -- which needs no daemon for file I/O --
and ``InMemorySandboxRunner``) confine ``write_file``/``read_file``/``write_files`` to the task's
scratch directory, resolving symlinks before the containment check.
``tests/unit/test_docker_sandbox_paths.py`` checks the ``..`` case but tolerates any exception;
this asserts the property that matters -- nothing outside the workspace is read or written --
and adds the symlink and batch cases.

**Push.** ``git_push`` refuses the default branch, but compared the ``branch`` argument to the
default branch's *name* and then passed it to ``git push`` as a refspec. ``HEAD:main``,
``+HEAD:main``, ``:main`` (delete) and option-shaped values such as ``--mirror`` all passed the
comparison and would have written to (or deleted) the default branch -- the exact outcome the
guard exists to prevent. The branch is now validated as a plain branch name and pushed with an
explicit ``refs/heads/X:refs/heads/X`` refspec.

The only thing faked is the network push itself (a ``HostGitService`` that records instead of
contacting github.com); the checkout, the default-branch lookup and the tool are real.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemorySandboxRunner
from aura_core.adapters.docker_sandbox import DockerSandboxRunner
from aura_core.adapters.host_git import HostGitService
from aura_core.adapters.memory_stores import InMemoryWorkspaceStore
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.ports.coding import FileWriteOp
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.workspaces import WorkspaceManager

ESCAPES = ["../escaped.txt", "nested/../../escaped.txt", "a/b/../../../escaped.txt",
           "..", "../"]


@pytest.fixture(params=["docker", "in_memory"])
def runner(request, tmp_path):
    if request.param == "docker":
        return DockerSandboxRunner(image="python:3.12-slim", network="",
                                   host_root=str(tmp_path / "sandboxes"))
    return InMemorySandboxRunner()


async def _scratch(runner, handle) -> Path:
    return runner._scratch(handle).resolve()


class TestPathContainment:
    @pytest.mark.parametrize("path", ESCAPES)
    async def test_a_relative_escape_writes_nothing_outside(self, runner, path: str) -> None:
        handle = await runner.create_task()
        scratch = await _scratch(runner, handle)
        outside = scratch.parent
        before = set(os.listdir(outside))

        with pytest.raises(Exception):
            await runner.write_file(handle, path, "pwned")

        assert set(os.listdir(outside)) == before
        assert not (outside / "escaped.txt").exists()

    async def test_an_absolute_path_is_confined_to_the_workspace(self, runner, tmp_path) -> None:
        handle = await runner.create_task()
        scratch = await _scratch(runner, handle)
        target = tmp_path / "absolute-target.txt"

        await runner.write_file(handle, str(target), "inside")

        assert not target.exists(), "an absolute path wrote outside the workspace"
        assert (scratch / str(target).lstrip("/")).read_text() == "inside"

    async def test_a_symlink_cannot_be_written_through(self, runner, tmp_path) -> None:
        handle = await runner.create_task()
        scratch = await _scratch(runner, handle)
        victim = tmp_path / "victim.txt"
        victim.write_text("original")
        (scratch / "link.txt").symlink_to(victim)
        (scratch / "linkdir").symlink_to(tmp_path, target_is_directory=True)

        for path in ("link.txt", "linkdir/victim.txt"):
            with pytest.raises(Exception):
                await runner.write_file(handle, path, "pwned")
        assert victim.read_text() == "original"

    async def test_a_symlink_cannot_be_read_through(self, runner, tmp_path) -> None:
        handle = await runner.create_task()
        scratch = await _scratch(runner, handle)
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET")
        (scratch / "peek").symlink_to(secret)

        with pytest.raises(Exception):
            await runner.read_file(handle, "peek")

    async def test_a_null_byte_is_refused(self, runner) -> None:
        handle = await runner.create_task()
        with pytest.raises(Exception):
            await runner.write_file(handle, "ok.txt\x00../../etc/passwd", "x")

    async def test_one_escaping_operation_writes_nothing_at_all(self, runner) -> None:
        """``write_files`` validates every operation before writing any: an escape in the last
        operation must not leave the first ones written."""
        handle = await runner.create_task()
        scratch = await _scratch(runner, handle)
        ops = [FileWriteOp(path="fine.txt", content="ok"),
               FileWriteOp(path="../escaped.txt", content="pwned")]

        with pytest.raises(Exception):
            await runner.write_files(handle, ops)

        assert not (scratch / "fine.txt").exists()
        assert not (scratch.parent / "escaped.txt").exists()


# --------------------------------------------------------------------------------------
# git_push refspec injection
# --------------------------------------------------------------------------------------

_ALICE = UserId("alice")


class _NoNetworkGit(HostGitService):
    """Real git for everything local; records the push instead of contacting github.com."""

    def __init__(self) -> None:
        super().__init__(timeout_s=60.0)
        self.pushes: list[dict] = []

    async def push(self, repo_dir, *, branch, token=None, repo=None):
        self.pushes.append({"branch": branch, "repo": repo})
        return None


def _ctx() -> ToolContext:
    return ToolContext(user_id=_ALICE, correlation_id="t1", deadline_s=60.0)


@pytest.fixture
async def push_tool():
    async def resolver(user_id):
        return InMemoryGitHubClient()

    sandbox = InMemorySandboxRunner()
    manager = WorkspaceManager(runner=sandbox, store=InMemoryWorkspaceStore(),
                               min_free_memory_mb=0)
    git = _NoNetworkGit()
    tools = {t.spec.name: t for t in build_sandbox_tools(
        sandbox, code_host_resolver=resolver, workspace_manager=manager, host_git=git)}

    await tools["open_workspace"].execute({"repo": "acme/widgets"}, _ctx())
    opened = await manager.open(_ALICE, "acme/widgets")
    root = Path(await sandbox.workspace_path(opened.handle)) / "widgets"
    root.mkdir(parents=True)
    await git._checked(["init", "-b", "main"], cwd=root)
    (root / "f.txt").write_text("one\n")
    await git.commit(root, message="first")
    return tools["git_push"], git


class TestPushCannotReachTheDefaultBranch:
    @pytest.mark.parametrize("branch", [
        "HEAD:main", "+HEAD:main", "feature:main", "HEAD:refs/heads/main", ":main",
        "refs/heads/main", "main:main", "--mirror", "--force", "-f", "--delete",
        "--all", "feature/x:main", "main ", "HEAD", "@", "feature..x", "feat~1", "a^b",
    ])
    async def test_a_refspec_or_option_spelling_is_refused(self, push_tool, branch: str) -> None:
        tool, git = push_tool
        with pytest.raises(ToolExecutionError):
            await tool.execute({"branch": branch}, _ctx())
        assert git.pushes == [], f"{branch!r} reached git push"

    async def test_the_default_branch_itself_is_still_refused(self, push_tool) -> None:
        tool, git = push_tool
        with pytest.raises(ToolExecutionError, match="refusing to push"):
            await tool.execute({"branch": "main"}, _ctx())
        assert git.pushes == []

    @pytest.mark.parametrize("branch", ["feature/x", "fix-123", "aura/engineer/task_7"])
    async def test_a_plain_feature_branch_is_pushed(self, push_tool, branch: str) -> None:
        tool, git = push_tool
        await tool.execute({"branch": branch}, _ctx())
        assert git.pushes == [{"branch": branch, "repo": "acme/widgets"}]


class TestHostGitPushRefspec:
    async def test_push_names_source_and_destination_explicitly(self, monkeypatch, tmp_path) -> None:
        """Defence in depth under the tool: the adapter never hands git a bare, interpretable
        argument -- the destination is always ``refs/heads/<branch>``."""
        git = HostGitService(timeout_s=10.0)
        seen: list[list[str]] = []

        async def record(args, *, cwd, token=None):
            seen.append(list(args))

        monkeypatch.setattr(git, "_checked", record)
        await git.push(tmp_path, branch="feature/x", repo="acme/widgets")
        (args,) = seen
        assert args[-1] == "refs/heads/feature/x:refs/heads/feature/x"
        assert "--" in args

    async def test_the_explicit_refspec_really_pushes_with_real_git(self, tmp_path) -> None:
        """Real git, local bare origin, no network: the new argument shape is one git accepts,
        and it creates exactly the named branch -- nothing else on the remote moves."""
        git = HostGitService(timeout_s=60.0)
        origin = tmp_path / "origin.git"
        await git._checked(["init", "--bare", "-b", "main", str(origin)], cwd=tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        await git._checked(["init", "-b", "main"], cwd=work)
        await git._checked(["remote", "add", "origin", str(origin)], cwd=work)
        (work / "f.txt").write_text("one\n")
        await git.commit(work, message="first")
        await git._checked(["push", "origin", "main"], cwd=work)
        main_before = (await git._run(["rev-parse", "main"], cwd=origin)).stdout.strip()

        await git.checkout_branch(work, branch="feature/x")
        (work / "f.txt").write_text("two\n")
        await git.commit(work, message="second")
        await git.push(work, branch="feature/x")

        heads = (await git._run(["for-each-ref", "--format=%(refname)", "refs/heads"],
                                cwd=origin)).stdout.split()
        assert sorted(heads) == ["refs/heads/feature/x", "refs/heads/main"]
        assert (await git._run(["rev-parse", "main"], cwd=origin)).stdout.strip() == main_before
