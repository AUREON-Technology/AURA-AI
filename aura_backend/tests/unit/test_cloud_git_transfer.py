"""``clone_repository`` and ``git_push`` when the workspace's files are not on this host.

The gateway clones into a folder of its own and uploads it; to push, it takes a git bundle out of
the sandbox into a fresh clone it made itself. Three things must stay true however the sandbox is
set up, because commands in it are chosen by a model working on a repository somebody else wrote:
the gateway never runs git where the sandbox can write, nothing from the sandbox's git
configuration or hooks takes part, and the GitHub token never gets in.

Everything runs for real -- the tool bodies, ``HostGitService``, the ``git`` binary, a local bare
repository standing in for GitHub -- over the in-memory transfer runner and the cloud runner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemoryTransferSandboxRunner
from aura_core.adapters.host_agent import (
    CompositeSandboxRunner,
    HostAgentRegistry,
    HostAgentSandboxRunner,
)
from aura_core.adapters.host_git import GitCommandError, HostGitService
from aura_core.adapters.memory_stores import InMemoryCloudSandboxStore, InMemoryWorkspaceStore
from aura_core.adapters.railway_sandbox import RailwaySandboxRunner
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.workspaces import WorkspaceManager

from ._fake_sandbox_broker import BROKER_TOKEN, BROKER_URL, FakeSandboxBroker

_ALICE = UserId("alice")
#: Shaped so that no credential scanner reads it as a token.
_TOKEN = "ghp_not-a-token-used-only-by-this-test"
_REPO = "acme/widgets"


def _setup_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
         "-c", "core.autocrlf=false", *args],
        cwd=cwd, check=True, capture_output=True, timeout=60,
    )


class _LocalRemoteGit(HostGitService):
    """``HostGitService`` whose "GitHub" is a folder of bare repositories: only ``remote_url``
    is replaced, and every git command is the real service running the real binary."""

    def __init__(self, remotes: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._remotes = remotes

    def remote_url(self, repo: str) -> str:  # type: ignore[override]
        return (self._remotes / (repo.replace("/", "__") + ".git")).as_uri()

    def make_remote(self, repo: str, files: dict[str, str]) -> Path:
        name = repo.replace("/", "__")
        bare, seed = self._remotes / f"{name}.git", self._remotes / f"{name}.seed"
        seed.mkdir(parents=True)
        _setup_git(self._remotes, "init", "-q", "--bare", "-b", "main", bare.name)
        _setup_git(seed, "init", "-q", "-b", "main")
        for path, text in files.items():
            (seed / path).parent.mkdir(parents=True, exist_ok=True)
            (seed / path).write_bytes(text.encode("utf-8"))
        _setup_git(seed, "add", "-A")
        _setup_git(seed, "commit", "-q", "-m", "initial")
        _setup_git(seed, "push", "-q", bare.as_uri(), "main")
        return bare


class _RecordingGit(_LocalRemoteGit):
    """Notes where every gateway-side git command ran."""

    def __init__(self, remotes: Path, **kwargs) -> None:
        super().__init__(remotes, **kwargs)
        self.folders: list[Path] = []

    async def _run(self, args, *, cwd, **kwargs):
        self.folders.append(Path(cwd).resolve())
        return await super()._run(args, cwd=cwd, **kwargs)


def _ctx(turn: str = "t1") -> ToolContext:
    return ToolContext(user_id=_ALICE, correlation_id=turn, deadline_s=120.0)


def _remote_ref(remote: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True, text=True, timeout=60,
    )
    return result.stdout.strip() or None


@pytest.fixture(params=["memory", "cloud"])
async def rig(request, tmp_path):
    broker = None
    if request.param == "memory":
        runner = InMemoryTransferSandboxRunner()
    else:
        broker = FakeSandboxBroker()
        runner = RailwaySandboxRunner(
            broker_url=BROKER_URL, token=BROKER_TOKEN, store=InMemoryCloudSandboxStore(),
            transport=broker,
        )
    composite = CompositeSandboxRunner(
        host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=runner
    )
    manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
    git = _RecordingGit(tmp_path / "remotes", timeout_s=60.0)
    remote = git.make_remote(_REPO, {"README.md": "# Widgets\n", "src/app.py": "print('v1')\n"})
    github = InMemoryGitHubClient()

    async def client(_user):
        return github

    async def token(_user):
        return state.token

    tools = {
        t.spec.name: t for t in build_sandbox_tools(
            composite, code_host_resolver=client, workspace_manager=manager, host_git=git,
            token_resolver=token,
        )
    }

    def sandbox_roots() -> list[Path]:
        if broker is not None:
            return [path.resolve() for path in broker.sandboxes.values()]
        return [path.resolve() for path in runner._scratch_dirs.values()]

    async def sh(script: str) -> str:
        opened = await manager.open(_ALICE, _REPO)
        result = await composite.run(opened.handle, ["sh", "-c", script], timeout_s=60)
        assert result.exit_code == 0, result.stderr
        return result.stdout.strip()

    state = SimpleNamespace(
        runner=runner, manager=manager, git=git, remote=remote, token=_TOKEN, tools=tools,
        sandbox_roots=sandbox_roots, sh=sh, broker=broker, tmp=tmp_path,
    )
    yield state
    for workspace in list(await manager.list_for_user(_ALICE)):
        await manager.destroy(workspace)
    if broker is not None:
        await runner.close()
        broker.cleanup()


async def _cloned(rig) -> None:
    await rig.tools["clone_repository"].execute({"repo": _REPO}, _ctx())


async def _committed(rig, branch: str = "feature/x") -> str:
    """A branch with one new commit, made through the tools; returns its id."""
    await _cloned(rig)
    await rig.tools["git_checkout_branch"].execute({"branch": branch}, _ctx())
    await rig.tools["write_file"].execute(
        {"path": "widgets/src/app.py", "content": "print('v2')\n"}, _ctx()
    )
    await rig.tools["git_commit"].execute({"message": "v2"}, _ctx())
    return await rig.sh("git -C widgets rev-parse HEAD")


class TestClone:
    async def test_the_workspace_gets_a_full_checkout(self, rig) -> None:
        reply = await rig.tools["clone_repository"].execute({"repo": _REPO}, _ctx())

        assert "Cloned acme/widgets into widgets/" in reply and "branch main" in reply
        assert "initial" in reply
        log = await rig.sh("git -C widgets log --format=%s && cat widgets/src/app.py")
        assert log.splitlines() == ["initial", "print('v1')"]

    async def test_the_token_never_enters_the_sandbox(self, rig) -> None:
        await _committed(rig)
        await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        needle = _TOKEN.encode()
        files = [p for root in rig.sandbox_roots() for p in root.rglob("*") if p.is_file()]
        assert files, "the sandbox should hold the checkout"
        assert not [p for p in files if needle in p.read_bytes()]
        if rig.broker is not None:
            assert not [route for route, _, body in rig.broker.requests if needle in body]

    async def test_the_gateway_never_runs_git_where_the_sandbox_writes(self, rig) -> None:
        await _committed(rig)
        await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        roots = rig.sandbox_roots()
        assert rig.git.folders and roots
        for folder in rig.git.folders:
            for root in roots:
                assert folder != root and root not in folder.parents

    async def test_nothing_is_left_on_the_gateway(self, rig) -> None:
        await _committed(rig)
        await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        assert rig.git.folders
        assert not [folder for folder in rig.git.folders if folder.exists()]

    async def test_a_folder_in_use_is_not_overwritten(self, rig) -> None:
        await rig.tools["open_workspace"].execute({"repo": _REPO}, _ctx())
        await rig.tools["write_file"].execute({"path": "widgets/mine.txt", "content": "x"}, _ctx())

        with pytest.raises(Exception, match="not empty"):
            await rig.tools["clone_repository"].execute({"repo": _REPO}, _ctx())

        assert await rig.sh("cat widgets/mine.txt") == "x"

    async def test_git_tools_clone_the_bound_repository_first(self, rig) -> None:
        await rig.tools["open_workspace"].execute({"repo": _REPO}, _ctx())

        reply = await rig.tools["git_status"].execute({}, _ctx())

        assert "cloned into widgets/" in reply and "main" in reply

    async def test_a_second_git_call_does_not_clone_again(self, rig) -> None:
        await rig.tools["open_workspace"].execute({"repo": _REPO}, _ctx())
        await rig.tools["git_status"].execute({}, _ctx())

        reply = await rig.tools["git_status"].execute({}, _ctx())

        assert "cloned into" not in reply


class TestPush:
    async def test_a_new_commit_reaches_the_remote(self, rig) -> None:
        tip = await _committed(rig)

        reply = await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        assert reply == "Pushed feature/x to acme/widgets."
        assert _remote_ref(rig.remote, "refs/heads/feature/x") == tip
        assert await rig.sh("git -C widgets rev-parse origin/feature/x") == tip

    async def test_a_second_push_sends_the_next_commit(self, rig) -> None:
        await _committed(rig)
        await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())
        await rig.tools["write_file"].execute({"path": "widgets/b.txt", "content": "b"}, _ctx())
        await rig.tools["git_commit"].execute({"message": "b"}, _ctx())
        tip = await rig.sh("git -C widgets rev-parse HEAD")

        await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/feature/x") == tip

    async def test_a_branch_with_no_new_commit_is_created(self, rig) -> None:
        await _cloned(rig)
        await rig.tools["git_checkout_branch"].execute({"branch": "feature/empty"}, _ctx())

        await rig.tools["git_push"].execute({"branch": "feature/empty"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/feature/empty") == _remote_ref(
            rig.remote, "refs/heads/main")

    async def test_the_default_branch_is_refused(self, rig) -> None:
        await _cloned(rig)
        before = _remote_ref(rig.remote, "refs/heads/main")

        with pytest.raises(ToolExecutionError, match="default branch"):
            await rig.tools["git_push"].execute({"branch": "main"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/main") == before

    async def test_the_remote_says_which_branch_is_default(self, rig) -> None:
        """GitHub's API here answers ``main``; the fresh clone's own HEAD is refused as well, and
        nothing the sandbox can write is asked."""
        await _cloned(rig)
        for args in (["update-ref", "refs/heads/trunk", "refs/heads/main"],
                     ["symbolic-ref", "HEAD", "refs/heads/trunk"]):
            subprocess.run(
                ["git", "--git-dir", str(rig.remote), *args], check=True, timeout=60)
        await rig.sh("git -C widgets branch trunk HEAD")
        before = _remote_ref(rig.remote, "refs/heads/trunk")

        with pytest.raises(ToolExecutionError, match="default branch"):
            await rig.tools["git_push"].execute({"branch": "trunk"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/trunk") == before

    async def test_no_credential_no_push(self, rig) -> None:
        await _committed(rig)
        rig.token = None

        with pytest.raises(ToolExecutionError, match="not connected"):
            await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/feature/x") is None

    async def test_a_workspace_without_a_checkout_says_so(self, rig) -> None:
        await rig.tools["open_workspace"].execute({"repo": _REPO}, _ctx())

        with pytest.raises(ToolExecutionError, match="not a git checkout"):
            await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

    async def test_an_unknown_branch_is_named(self, rig) -> None:
        await _cloned(rig)

        with pytest.raises(ToolExecutionError, match="no branch"):
            await rig.tools["git_push"].execute({"branch": "feature/nope"}, _ctx())


class TestAHostileSandbox:
    async def test_its_hooks_and_configuration_take_no_part(self, rig) -> None:
        tip = await _committed(rig)
        ran = (rig.tmp / "ran").as_posix()
        decoy = rig.git.make_remote("evil/decoy", {"x": "x"})
        await rig.sh(
            "cd widgets && "
            f"printf '#!/bin/sh\\ntouch \"{ran}\"\\n' > .git/hooks/pre-push && "
            "chmod +x .git/hooks/pre-push && "
            f"git config url.'{decoy.as_uri()}'.insteadOf '{rig.git.remote_url(_REPO)}' && "
            f"git config core.sshCommand 'touch \"{ran}\"' && "
            f"git config credential.helper '!touch \"{ran}\"'"
        )

        await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/feature/x") == tip
        assert _remote_ref(decoy, "refs/heads/feature/x") is None
        assert not Path(ran).exists()

    async def test_a_commit_carrying_a_dot_git_folder_is_refused(self, rig) -> None:
        await _cloned(rig)
        await rig.sh(
            "cd widgets && blob=$(echo x | git hash-object -w --stdin) && "
            "inner=$(printf '100644 blob %s\\tconfig\\n' $blob | git mktree) && "
            "tree=$( (git ls-tree HEAD; printf '040000 tree %s\\t.git\\n' $inner) | git mktree ) && "
            "bad=$(git -c user.name=A -c user.email=a@example.com commit-tree $tree -p HEAD -m bad)"
            " && git update-ref refs/heads/feature/bad $bad"
        )

        with pytest.raises(ToolExecutionError, match="failed the object check"):
            await rig.tools["git_push"].execute({"branch": "feature/bad"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/feature/bad") is None

    async def test_history_the_remote_does_not_have_is_refused(self, rig) -> None:
        await _committed(rig)
        # The sandbox claims the remote already has its commit, then builds on it.
        await rig.sh(
            "cd widgets && git update-ref refs/remotes/origin/pretend HEAD && echo y > y && "
            "git add y && git -c user.name=A -c user.email=a@example.com commit -qm y"
        )

        with pytest.raises(ToolExecutionError, match="does not have"):
            await rig.tools["git_push"].execute({"branch": "feature/x"}, _ctx())

        assert _remote_ref(rig.remote, "refs/heads/feature/x") is None


class TestBundleChecks:
    """``HostGitService.import_bundle`` on bundles the tool flow would never produce itself."""

    @pytest.fixture
    def git(self, tmp_path) -> _LocalRemoteGit:
        service = _LocalRemoteGit(tmp_path / "remotes", timeout_s=60.0)
        service.make_remote(_REPO, {"README.md": "# Widgets\n"})
        return service

    def _bundle(self, git, tmp_path, *refs: str) -> Path:
        work = tmp_path / "work"
        subprocess.run(
            ["git", "clone", "-q", git.remote_url(_REPO), str(work)], check=True, timeout=60)
        script = (
            "git -c user.name=A -c user.email=a@example.com commit -q --allow-empty -m n && "
            "git branch -f other HEAD && git bundle create -q ../out.bundle "
            + " ".join(refs) + " --not --remotes"
        )
        subprocess.run(["sh", "-c", script], cwd=work, check=True, timeout=60)
        return tmp_path / "out.bundle"

    async def _fresh(self, git, tmp_path) -> Path:
        bare = tmp_path / "gateway" / "push.git"
        bare.parent.mkdir()
        await git.clone_for_push(_REPO, dest=bare)
        return bare

    async def test_a_good_bundle_is_imported(self, git, tmp_path) -> None:
        bundle = self._bundle(git, tmp_path, "refs/heads/main")
        bare = await self._fresh(git, tmp_path)

        tip = await git.import_bundle(bare, bundle, branch="main")

        assert await git.has_commit(bare, tip)
        assert await git.default_branch_of(bare) == "main"

    async def test_only_the_asked_branch_may_be_inside(self, git, tmp_path) -> None:
        bundle = self._bundle(git, tmp_path, "refs/heads/main", "refs/heads/other")
        bare = await self._fresh(git, tmp_path)

        with pytest.raises(GitCommandError, match="exactly"):
            await git.import_bundle(bare, bundle, branch="main")

    async def test_another_branch_than_asked_is_refused(self, git, tmp_path) -> None:
        bundle = self._bundle(git, tmp_path, "refs/heads/other")
        bare = await self._fresh(git, tmp_path)

        with pytest.raises(GitCommandError, match="exactly"):
            await git.import_bundle(bare, bundle, branch="main")

    @pytest.mark.parametrize("content", [
        b"", b"not a bundle\n", b"# v2 git bundle\nnonsense\n\nPACK",
        b"# v3 git bundle\n@filter=blob:none\n\nPACK",
        b"# v2 git bundle\n" + b"-" + b"0" * 40,
    ])
    async def test_a_file_that_is_not_a_plain_bundle_is_refused(
        self, git, tmp_path, content
    ) -> None:
        bare = await self._fresh(git, tmp_path)
        bundle = tmp_path / "bad.bundle"
        bundle.write_bytes(content)

        with pytest.raises(GitCommandError, match="bundle"):
            await git.import_bundle(bare, bundle, branch="main")

    async def test_a_damaged_pack_is_refused(self, git, tmp_path) -> None:
        bundle = self._bundle(git, tmp_path, "refs/heads/main")
        data = bytearray(bundle.read_bytes())
        data[-30] ^= 0xFF
        bundle.write_bytes(bytes(data))
        bare = await self._fresh(git, tmp_path)

        with pytest.raises(GitCommandError, match="object check"):
            await git.import_bundle(bare, bundle, branch="main")
