"""Simulated end-to-end proof: the Engineer's filesystem, command, git, workspace and GitHub tools.

Rows covered: 1, 3, 11 (server-side half), 62, 67, 113-125, 128-132 of the incomplete-tools sheet.

Every call goes through the container's real ``ToolRegistry.execute`` as the Engineer persona
(``capabilities=("engineer_agent",)``), so argument validation, ``ToolPolicy``, the capability gate
and the result guard are production's. What is SIMULATED, and nothing else:

* the sandbox: ``InMemorySandboxRunner`` (real subprocesses in a real temp dir, no isolation) or,
  for the Docker leg, the real ``DockerSandboxRunner`` over ``FakeDockerClient`` (no daemon);
* GitHub's git remote: a directory of bare repos behind ``LocalRemoteGit`` (only ``remote_url`` is
  replaced; clone/push are the real ``HostGitService`` running real ``git``);
* GitHub's REST API: ``InMemoryGitHubClient``;
* the planner that picks a tool in the kernel-turn tests (``ScriptedPlanner``).

Docker's kernel-level isolation, a real GitHub and a paired desktop are NOT exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemorySandboxRunner
from aura_core.adapters.docker_sandbox import DockerSandboxRunner
from aura_core.adapters.memory_stores import InMemoryWorkspaceStore
from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest
from aura_core.tools.builtin import build_file_tools
from aura_core.tools.coding import build_github_tools, build_sandbox_tools
from aura_core.tools.workspaces import WorkspaceManager
from tests.e2e.sim_support import (
    ALICE, ENGINEER, FakeDockerClient, LocalRemoteGit, call, context, git, ok_json,
)
from tests.unit.test_autonomy_profile import ScriptedPlanner

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------------------- rigs


class Rig:
    def __init__(self, registry, runner) -> None:
        self.registry = registry
        self.runner = runner

    async def call(self, name, **kw):
        return await call(self.registry, name, **kw)

    async def sh(self, command: str) -> str:
        result, _ = await self.call("run_command", command=command)
        assert result.ok, result.content
        return result.content

    @property
    def root(self):
        dirs = list(self.runner._scratch_dirs.values())
        if not dirs:  # refused before any sandbox was created: nothing can exist
            return Path("/nonexistent-sandbox-root")
        (root,) = dirs
        return root


@pytest.fixture
async def rig(container):
    """The container's real registry with the Engineer tools over the in-memory (real-process) runner."""
    await container.startup()
    runner = InMemorySandboxRunner()
    for tool in build_sandbox_tools(runner):
        container.tools.register(tool, overwrite=True)
    return Rig(container.tools, runner)


@pytest.fixture
async def durable(container, tmp_path):
    """Durable workspaces + a real git service whose remote is local + an in-memory GitHub REST client."""
    await container.startup()
    runner = InMemorySandboxRunner()
    manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
    host_git = LocalRemoteGit(tmp_path / "remotes", timeout_s=60.0)
    host_git.make_remote("acme/widgets", {"README.md": "# Widgets\n", "src/app.py": "print('v1')\n"})
    rest = InMemoryGitHubClient()
    rest.set_default_branch("acme/widgets", "main")

    async def resolver(_user):
        return rest

    holder: dict = {"token": None}

    async def token_resolver(_user):
        return holder["token"]

    def build(token):
        holder["token"] = token

    for tool in build_sandbox_tools(
        runner, code_host_resolver=resolver, workspace_manager=manager, host_git=host_git,
        token_resolver=token_resolver,
    ):
        container.tools.register(tool, overwrite=True)

    build(None)
    rig = Rig(container.tools, runner)
    rig.manager, rig.host_git, rig.rest, rig.build = manager, host_git, rest, build
    return rig


# ------------------------------------------------------------- row 1: mcp_filesystem_tool


async def test_row1_one_filesystem_surface_write_patch_read_list_and_github_read(rig, container, tmp_path) -> None:
    """write/patch via the engineer tools, read/list via the read-only root tools, GitHub read via REST."""
    written, _ = await rig.call("write_file", path="pkg/mod.py", content="def f():\n    return 1\n")
    assert written.ok and (rig.root / "pkg" / "mod.py").read_text() == "def f():\n    return 1\n"
    patched, _ = await rig.call("edit_file", path="pkg/mod.py", find="return 1", replace="return 2")
    assert patched.ok and (rig.root / "pkg" / "mod.py").read_text().endswith("return 2\n")

    escape, _ = await rig.call("write_file", path="../escape.txt", content="x")
    assert not escape.ok and not (rig.root.parent / "escape.txt").exists()
    zero, _ = await rig.call("edit_file", path="pkg/mod.py", find="absent", replace="z")
    assert not zero.ok and "found 0" in zero.content

    bad, _ = await rig.call("write_files", operations=[
        {"path": "b.py", "content": "b = 1\n"}, {"path": "pkg/mod.py", "find": "nope", "replace": "x"}])
    assert not bad.ok and not (rig.root / "b.py").exists(), "all-or-nothing: the valid op was not written"
    good, _ = await rig.call("write_files", operations=[
        {"path": "b.py", "content": "b = 1\n"}, {"path": "pkg/mod.py", "find": "return 2", "replace": "return 3"}])
    assert good.ok and (rig.root / "b.py").read_text() == "b = 1\n"
    assert "return 3" in await rig.sh("cat pkg/mod.py")

    # The read-only root tools (off by default; an operator sets AURA_TOOLS_FILES_ROOT).
    base = tmp_path / "files_root"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "guide.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (base / "big.txt").write_text("line\n" * 20_000, encoding="utf-8")
    container.tools.register_all(build_file_tools(base))
    listing, _ = await call(container.tools, "list_files", subdirectory="docs")
    assert listing.ok and "guide.txt" in listing.content
    read, _ = await call(container.tools, "read_file", path="docs/guide.txt")
    assert read.ok and "alpha" in read.content and "beta" in read.content
    out, _ = await call(container.tools, "read_file", path="../outside.txt")
    assert not out.ok
    window, _ = await call(container.tools, "read_file", path="big.txt")
    assert window.ok and "offset" in window.content.lower(), "a long file is windowed with a continue-at offset"

    # GitHub read through the REST client.
    github = InMemoryGitHubClient()
    github.seed_file("acme/widgets", "main", "README.md", "# Widgets\nhello\n")

    async def resolver(_user):
        return github

    container.tools.register_all(build_github_tools(resolver))
    fetched, _ = await call(container.tools, "github_get_file_contents", repo="acme/widgets", path="README.md")
    assert fetched.ok and "# Widgets" in fetched.content and "hello" in fetched.content


# ----------------------------------------------- rows 3, 116: secure_code_executor / run_command


async def test_row3_116_run_command_reports_exit_code_stderr_timeout_and_keeps_head_and_tail(rig) -> None:
    ok, _ = await rig.call("run_command", command="echo out; echo err 1>&2; exit 3")
    assert ok.ok, "a non-zero exit is a result the model reads, not a tool failure"
    assert "exit_code=3" in ok.content and "out" in ok.content and "err" in ok.content

    slow, _ = await rig.call("run_command", command="sleep 20", timeout_s=1)
    assert "124" in slow.content, slow.content

    big, _ = await rig.call("run_command", command="seq 1 200000")
    assert big.ok and "\n1\n" in "\n" + big.content.replace("\r", "") and "200000" in big.content
    assert len(big.content) < 200_000, "long output is capped, keeping head and tail"


async def test_row3_11_docker_leg_one_hardened_throwaway_container_per_command_never_exec(container, tmp_path) -> None:
    """SIMULATED: ``FakeDockerClient`` instead of a daemon. The real ``DockerSandboxRunner`` builds the
    launch arguments; the "container" runs the command as a plain subprocess in the bind-mounted dir."""
    await container.startup()
    runner = DockerSandboxRunner(image="aura-sandbox:test", network="sandbox_egress", host_root=str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir()
    fake = FakeDockerClient()
    runner._client = fake
    for tool in build_sandbox_tools(runner):
        container.tools.register(tool, overwrite=True)

    written, _ = await call(container.tools, "write_file", path="hello.py", content="print('hi from the sandbox')\n")
    assert written.ok, written.content
    ran, _ = await call(container.tools, "run_command", command="python3 hello.py || python hello.py")
    assert ran.ok and "hi from the sandbox" in ran.content and "exit_code=0" in ran.content

    (launch,) = fake.launches
    assert launch["cap_drop"] == ["ALL"] and "no-new-privileges" in launch["security_opt"]
    assert launch["read_only"] is True and launch["user"] == "10001:10001"
    assert launch["pids_limit"] == 512 and launch["mem_limit"] == launch["memswap_limit"]
    assert launch["image"] == "aura-sandbox:test"
    assert launch["tmpfs"]["/tmp"].count("noexec") == 1
    assert fake.containers_made[0].removed, "the throwaway container is removed after its one command"

    slow, _ = await call(container.tools, "run_command", command="sleep 20", timeout_s=1)
    assert "124" in slow.content and fake.containers_made[-1].killed, "an overrunning command is killed"


# ------------------------------------------------------------- rows 113-116: write/edit/run


async def test_row113_write_file_is_text_only_and_atomic(rig) -> None:
    done, _ = await rig.call("write_file", path="a/b/c.txt", content="héllo ✓\n")
    assert done.ok and (rig.root / "a" / "b" / "c.txt").read_bytes() == "héllo ✓\n".encode()
    pdf, _ = await rig.call("write_file", path="x.pdf", content="%PDF-1.4")
    assert not pdf.ok and not (rig.root / "x.pdf").exists(), "binary formats are refused"


async def test_row114_write_files_validates_all_then_writes_all_and_caps_operations(rig) -> None:
    dup, _ = await rig.call("write_files", operations=[{"path": "d.txt", "content": "1"}, {"path": "d.txt", "content": "2"}])
    assert not dup.ok and not (rig.root / "d.txt").exists(), "a duplicate path is refused before anything is written"
    many, _ = await rig.call("write_files", operations=[{"path": f"f{i}.txt", "content": "x"} for i in range(101)])
    assert not many.ok
    fine, _ = await rig.call("write_files", operations=[{"path": f"ok{i}.txt", "content": str(i)} for i in range(5)])
    assert fine.ok and (rig.root / "ok4.txt").read_text() == "4"


async def test_row115_edit_file_exactly_one_occurrence_crlf_aware(rig) -> None:
    await rig.call("write_file", path="m.py", content="a = 1\r\nb = 2\r\n")
    edited, _ = await rig.call("edit_file", path="m.py", find="b = 2", replace="b = 20")
    assert edited.ok and (rig.root / "m.py").read_bytes() == b"a = 1\r\nb = 20\r\n"
    empty, _ = await rig.call("edit_file", path="m.py", find="", replace="x")
    assert not empty.ok
    same, _ = await rig.call("edit_file", path="m.py", find="a = 1", replace="a = 1")
    assert not same.ok and "identical" in same.content


# ------------------------------------------------- rows 117-120: git tools, plain and durable


async def test_row117_to_120_git_tools_in_a_plain_workspace(rig) -> None:
    await rig.sh("git init -q -b main")
    await rig.call("write_file", path="a.txt", content="one\n")
    status, _ = await rig.call("git_status")
    assert status.ok and "## No commits yet on main" in status.content and "a.txt" in status.content

    commit, _ = await rig.call("git_commit", message="first commit")
    assert commit.ok and "first commit" in commit.content
    clean, _ = await rig.call("git_status")
    assert clean.ok and "## main" in clean.content and "a.txt" not in clean.content

    await rig.call("edit_file", path="a.txt", find="one", replace="two")
    await rig.call("write_file", path="new.txt", content="n\n")
    diff, _ = await rig.call("git_diff")
    assert diff.ok and "-one" in diff.content and "+two" in diff.content and "new.txt" in diff.content

    branch, _ = await rig.call("git_checkout_branch", branch="feature/x")
    assert branch.ok and "feature/x" in await rig.sh("git rev-parse --abbrev-ref HEAD")
    for bad in ("-bad", "a b", "x..y"):
        refused, _ = await rig.call("git_checkout_branch", branch=bad)
        assert not refused.ok, bad
    back, _ = await rig.call("git_checkout_branch", branch="main")
    assert back.ok and (await rig.sh("git log --oneline")).count("first commit") == 1
    empty, _ = await rig.call("git_commit", message="   ")
    assert not empty.ok


async def test_row117_to_121_git_tools_see_the_checkout_after_a_durable_clone(durable) -> None:
    """Durable workspace: the clone lands in ``<workspace>/widgets/``. The git tools must work there."""
    cloned, report = await durable.call("clone_repository", repo="acme/widgets")
    assert not cloned.ok and report.needs_confirmation, "clone is CONFIDENTIAL + side-effecting: it asks first"
    cloned, _ = await durable.call("clone_repository", repo="acme/widgets", confirmed=True)
    assert cloned.ok, cloned.content
    assert (durable.root / "widgets" / ".git").is_dir(), "host git made a real checkout"
    assert (durable.root / "widgets" / "src" / "app.py").read_text() == "print('v1')\n"

    await durable.call("edit_file", path="widgets/src/app.py", find="v1", replace="v2")
    status, _ = await durable.call("git_status")
    assert status.ok, status.content
    assert "src/app.py" in status.content
    diff, _ = await durable.call("git_diff")
    assert diff.ok and "-print('v1')" in diff.content and "+print('v2')" in diff.content
    branch, _ = await durable.call("git_checkout_branch", branch="fix/v2")
    assert branch.ok, branch.content
    commit, _ = await durable.call("git_commit", message="bump to v2")
    assert commit.ok, commit.content
    log = await durable.sh("git -C widgets log --oneline")
    assert "bump to v2" in log and "initial" in log
    assert "fix/v2" in await durable.sh("git -C widgets rev-parse --abbrev-ref HEAD")


# ------------------------------------------------- rows 121-125: clone, push, workspaces


async def test_row121_clone_rest_copy_path_budgets_and_confirmation(rig) -> None:
    """The REST copy path (no host git): binary and oversized files are skipped *and said so*."""
    github = InMemoryGitHubClient()
    github.seed_file("acme/widgets", "main", "README.md", "# W")
    github.seed_file("acme/widgets", "main", "src/a.py", "x = 1\n")
    github.seed_file("acme/widgets", "main", "logo.png", "\x89PNG")

    async def resolver(_user):
        return github

    for tool in build_sandbox_tools(rig.runner, code_host_resolver=resolver):
        rig.registry.register(tool, overwrite=True)
    asked, report = await rig.call("clone_repository", repo="https://github.com/acme/widgets")
    assert not asked.ok and report.needs_confirmation
    done, _ = await rig.call("clone_repository", repo="https://github.com/acme/widgets", confirmed=True)
    assert done.ok and "Copied 2 file(s)" in done.content and "1 binary file(s) skipped" in done.content
    assert (rig.root / "widgets" / "src" / "a.py").read_text() == "x = 1\n"
    assert not (rig.root / "widgets" / "logo.png").exists()


async def test_row122_git_push_confirms_refuses_default_branch_and_refspecs_then_pushes_a_feature_branch(durable) -> None:
    await durable.call("clone_repository", repo="acme/widgets", confirmed=True)
    await durable.call("git_checkout_branch", branch="feature/login")
    await durable.call("write_file", path="widgets/login.py", content="login = True\n")
    await durable.call("git_commit", message="add login")

    asked, report = await durable.call("git_push", branch="feature/login")
    assert not asked.ok and report.needs_confirmation, "a push always asks"
    for refused_branch in ("main", "HEAD:main", "--mirror", "+x:main", ":main"):
        refused, _ = await durable.call("git_push", branch=refused_branch, confirmed=True)
        assert not refused.ok, refused_branch
    unconnected, _ = await durable.call("git_push", branch="feature/login", confirmed=True)
    assert not unconnected.ok and "not connected" in unconnected.content, "no credential -> no push"

    durable.build("ghp_" + "x" * 8)  # a fake credential built at runtime
    pushed, _ = await durable.call("git_push", branch="feature/login", confirmed=True)
    assert pushed.ok and "Pushed feature/login to acme/widgets" in pushed.content, pushed.content
    remote = durable.host_git._remotes / "acme__widgets.git"
    assert "add login" in git(remote, "log", "--oneline", "feature/login")
    assert "add login" not in git(remote, "log", "--oneline", "main"), "the default branch is untouched"


async def test_row123_124_125_open_list_destroy_workspace(durable) -> None:
    opened, _ = await durable.call("open_workspace", repo="acme/widgets")
    assert opened.ok and "acme/widgets" in opened.content
    await durable.call("write_file", path="keep.txt", content="persisted\n")
    other_turn = context("a-later-message")
    again, _ = await call(durable.registry, "run_command", command="cat keep.txt", ctx=other_turn)
    assert again.ok and "persisted" in again.content, "the same repo resolves to the same files in a later turn"

    listed, _ = await durable.call("list_workspaces")
    assert listed.ok and "acme/widgets" in listed.content
    asked, report = await durable.call("destroy_workspace", repo="acme/widgets")
    assert not asked.ok and report.needs_confirmation
    gone, _ = await durable.call("destroy_workspace", repo="acme/widgets", confirmed=True)
    assert gone.ok and "Deleted the workspace for acme/widgets" in gone.content
    empty, _ = await durable.call("list_workspaces")
    assert "no workspaces" in empty.content.lower()
    again, _ = await durable.call("destroy_workspace", repo="acme/widgets", confirmed=True)
    assert again.ok and "No workspace open" in again.content


async def test_row123_not_registered_without_a_workspace_manager(rig) -> None:
    result, _ = await rig.call("open_workspace", repo="acme/widgets")
    assert not result.ok, "persistent workspaces are off where no manager is wired"


# ------------------------------------------------------- rows 128-132: GitHub read and write tools


@pytest.fixture
async def github(container):
    await container.startup()
    client = InMemoryGitHubClient()
    client.set_default_branch("acme/widgets", "main")
    client.seed_file("acme/widgets", "main", "README.md", "# Widgets\n")
    client.seed_issue("acme/widgets", 7, title="Crash on empty cart", body="Steps: open cart, press pay.\nTraceback ...")

    async def resolver(_user):
        return client

    container.tools.register_all(build_github_tools(resolver))
    return Rig(container.tools, None), client


async def test_row128_129_issue_and_pull_request_reads(github) -> None:
    rig, client = github
    issue, _ = await rig.call("github_get_issue", repo="acme/widgets", number=7)
    assert issue.ok and "#7 Crash on empty cart (open)" in issue.content and "press pay" in issue.content
    missing, _ = await rig.call("github_get_issue", repo="acme/widgets", number=99)
    assert not missing.ok and "99" in missing.content

    await client.create_draft_pull_request("acme/widgets", branch="fix/cart", base="main", title="Fix cart", body="Handles empty carts.")
    pr, _ = await rig.call("github_get_pull_request", repo="acme/widgets", number=1)
    assert pr.ok and "#1 Fix cart (open) fix/cart -> main" in pr.content and "Handles empty carts." in pr.content


async def test_row130_131_132_github_writes_confirm_validate_and_land(github) -> None:
    rig, client = github
    asked, report = await rig.call("github_create_branch", repo="acme/widgets", branch="fix/cart", from_ref="main")
    assert not asked.ok and report.needs_confirmation
    bad, _ = await rig.call("github_create_branch", repo="acme/widgets", branch="bad name", from_ref="main", confirmed=True)
    assert not bad.ok
    made, _ = await rig.call("github_create_branch", repo="acme/widgets", branch="fix/cart", from_ref="main", confirmed=True)
    assert made.ok and "fix/cart" in made.content
    assert await client.get_file_contents("acme/widgets", "README.md", ref="fix/cart") == "# Widgets\n"

    refused, _ = await rig.call("github_commit_files", repo="acme/widgets", branch="main", message="m",
                                files={"a.txt": "x"}, confirmed=True)
    assert not refused.ok, "the default branch is refused (asked from GitHub, not from the model)"
    preview, report = await rig.call("github_commit_files", repo="acme/widgets", branch="fix/cart", message="Handle empty cart",
                                     files={"cart.py": "def pay(c):\n    return bool(c)\n"})
    assert not preview.ok and report.needs_confirmation and report.diff_preview and "cart.py" in report.diff_preview
    committed, _ = await rig.call("github_commit_files", repo="acme/widgets", branch="fix/cart", message="Handle empty cart",
                                  files={"cart.py": "def pay(c):\n    return bool(c)\n"}, confirmed=True)
    assert committed.ok and "Committed 1 file(s) to acme/widgets@fix/cart" in committed.content
    assert (await client.get_file_contents("acme/widgets", "cart.py", ref="fix/cart")).startswith("def pay")

    same, _ = await rig.call("github_create_draft_pull_request", repo="acme/widgets", branch="main", base="main",
                             title="t", body="b", confirmed=True)
    assert not same.ok, "head == base is refused"
    pr, _ = await rig.call("github_create_draft_pull_request", repo="acme/widgets", branch="fix/cart", base="main",
                           title="Fix empty cart", body="Closes #7", confirmed=True)
    assert pr.ok and "Opened draft PR #1" in pr.content
    read, _ = await rig.call("github_get_pull_request", repo="acme/widgets", number=1)
    assert "Fix empty cart" in read.content and "fix/cart -> main" in read.content


# --------------------------------------- rows 62, 67: review policy and the mobile approval gate


async def test_row62_strict_profile_holds_a_workspace_write_and_denies_network_tools(rig) -> None:
    strict = context("strict", autonomy="strict")
    held, report = await call(rig.registry, "write_file", path="n.txt", content="x", ctx=strict)
    assert not held.ok and report.needs_confirmation, "strict asks before a workspace-local write that standard runs"
    assert not any((root / "n.txt").exists() for root in rig.runner._scratch_dirs.values())
    standard, _ = await call(rig.registry, "write_file", path="n.txt", content="x", ctx=context("std"))
    assert standard.ok
    _read, read_report = await call(rig.registry, "git_status", ctx=strict)
    assert not read_report.needs_confirmation, "strict leaves reads alone"


async def test_row62_a_stored_strict_profile_reaches_a_real_kernel_turn(container) -> None:
    await container.startup()
    runner = InMemorySandboxRunner()
    for tool in build_sandbox_tools(runner):
        container.tools.register(tool, overwrite=True)
    user = UserId("strict-sim")
    await container.autonomy.put(user, "strict", at="2026-11-23T00:00:00+00:00")
    container.kernel._deps.planner = ScriptedPlanner("write_file", {"path": "note.txt", "content": "hello"})
    result = await container.kernel.run(TurnRequest(message="write a note", user_id=user, capabilities=ENGINEER))
    assert result.needs_confirmation == ("write_file",) and result.pending_tool_calls
    assert not any((root / "note.txt").exists() for root in runner._scratch_dirs.values())


async def test_row67_a_remote_phone_origin_side_effect_on_a_user_machine_needs_approval(container) -> None:
    """SIMULATED: a fake paired desktop (``host_routed`` says every workspace write lands on the
    user's machine). The policy then demands approval for a call that came from a phone."""
    from aura_core.tools.policy import ToolPolicy
    from aura_core.tools.registry import ToolRegistry

    runner = InMemorySandboxRunner()
    async def paired_desktop(_ctx) -> bool:
        return True

    registry = ToolRegistry(policy=ToolPolicy(), host_routed=paired_desktop)
    registry.register_all(build_sandbox_tools(runner))

    local, _ = await call(registry, "write_file", path="a.txt", content="x", ctx=context("desk"))
    assert local.ok, "from the desktop itself the same write is prompt-free (the daemon has its own approval)"
    remote_ctx = context("phone")
    remote_ctx.metadata["origin"] = "remote"
    phone, report = await call(registry, "write_file", path="b.txt", content="x", ctx=remote_ctx)
    assert not phone.ok and report.needs_confirmation
    assert "side_effect_on_user_machine_from_remote_device" in (report.policy_reason or phone.content)
    approved, _ = await call(registry, "write_file", path="b.txt", content="x", ctx=remote_ctx, confirmed=True)
    assert approved.ok, approved.content
