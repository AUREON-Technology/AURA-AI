"""The git tools clone the workspace's own repository when the workspace has no checkout yet.

A durable workspace is opened *for* a repository. If nobody ran ``clone_repository`` (a new turn, a
workspace opened without one, a checkout that was deleted), ``git_status`` / ``git_diff`` /
``git_commit`` / ``git_checkout_branch`` used to fail with "not a git repository". Now they clone
the repository the workspace is bound to -- never a model-chosen one -- and say so in the result.

REAL here: ``git``, the clone, the filesystem, the gateway's host-git service and the workspace
manager. SIMULATED: GitHub itself (a directory of bare repositories on disk behind ``LocalRemoteGit``)
and the GitHub REST client (in memory). See ``test_incomplete_tools_coding_simulated.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from aura_core.tools.coding import build_sandbox_tools
from tests.e2e.test_incomplete_tools_coding_simulated import Rig, durable, rig  # noqa: F401  (fixtures)

pytestmark = pytest.mark.asyncio


async def _open(durable_rig) -> None:
    opened, _ = await durable_rig.call("open_workspace", repo="acme/widgets")
    assert opened.ok, opened.content


async def test_git_status_clones_the_bound_repository_first_and_says_so(durable) -> None:
    await _open(durable)
    assert not (durable.root / "widgets").exists(), "nothing was cloned yet"

    status, _ = await durable.call("git_status")
    assert status.ok, status.content
    assert (durable.root / "widgets" / ".git").is_dir(), "a real checkout now exists"
    assert (durable.root / "widgets" / "src" / "app.py").read_text() == "print('v1')\n"
    assert "no checkout of acme/widgets" in status.content and "cloned into widgets/" in status.content
    assert "## main" in status.content

    again, _ = await durable.call("git_status")
    assert again.ok and "no checkout" not in again.content, "the note is given once, and nothing is cloned twice"


async def test_every_git_tool_triggers_it(durable) -> None:
    for tool, args in (("git_diff", {}), ("git_checkout_branch", {"branch": "fix/x"})):
        fresh = await _fresh(durable)
        result, _ = await fresh.call(tool, **args)
        assert result.ok, (tool, result.content)
        assert (fresh.root / "widgets" / ".git").is_dir(), tool


async def _fresh(durable_rig):
    """Remove the checkout so the next git tool has to clone again."""
    import shutil

    await _open(durable_rig)
    shutil.rmtree(durable_rig.root / "widgets", ignore_errors=True)
    return durable_rig


async def test_git_diff_after_the_automatic_clone_is_clean_then_shows_an_edit(durable) -> None:
    await _open(durable)
    clean, _ = await durable.call("git_diff")
    assert clean.ok and "(no changes)" in clean.content and "cloned into widgets/" in clean.content
    edited, _ = await durable.call("edit_file", path="widgets/src/app.py", find="v1", replace="v2")
    assert edited.ok, edited.content
    diff, _ = await durable.call("git_diff")
    assert "+print('v2')" in diff.content


async def test_a_commit_after_the_automatic_clone_still_needs_a_branch_off_main(durable) -> None:
    await _open(durable)
    assert (await durable.call("git_status"))[0].ok  # clones it
    edited, _ = await durable.call("edit_file", path="widgets/src/app.py", find="v1", replace="v2")
    assert edited.ok, edited.content
    refused, _ = await durable.call("git_commit", message="straight to main")
    assert not refused.ok and "default branch" in refused.content
    await durable.call("git_checkout_branch", branch="fix/v2")
    committed, _ = await durable.call("git_commit", message="bump to v2")
    assert committed.ok, committed.content
    assert "bump to v2" in await durable.sh("git -C widgets log --oneline -1")


async def test_two_git_tools_at_once_clone_it_once(durable) -> None:
    await _open(durable)
    first, second = await asyncio.gather(durable.call("git_status"), durable.call("git_diff"))
    assert first[0].ok and second[0].ok, (first[0].content, second[0].content)
    assert (durable.root / "widgets" / ".git").is_dir()
    assert not list(durable.root.glob("widgets*")) or [p.name for p in durable.root.glob("widgets*")] == ["widgets"]


async def test_an_existing_checkout_under_another_name_is_used_not_cloned_again(durable) -> None:
    await _open(durable)
    cloned, _ = await durable.call("clone_repository", repo="acme/widgets", destination="vendor/widgets-copy",
                                    confirmed=True)
    assert cloned.ok, cloned.content
    status, _ = await durable.call("git_status")
    assert status.ok and "no checkout" not in status.content
    assert not (durable.root / "widgets").exists(), "no second clone appeared at the default location"


async def test_without_a_bound_repository_nothing_is_cloned(rig) -> None:
    result, _ = await rig.call("git_status")
    assert not result.ok and "not a git repository" in result.content
    assert not list(rig.root.glob("*")) if rig.runner._scratch_dirs else True


async def test_an_explicit_repo_path_that_is_missing_is_an_error_not_a_clone(durable) -> None:
    await _open(durable)
    result, _ = await durable.call("git_status", repo_path="widgets")
    assert not result.ok and "not a git repository" in result.content
    assert not (durable.root / "widgets").exists()


async def test_without_a_connected_account_it_says_so_instead_of_failing_obscurely(durable, container) -> None:
    await _open(durable)

    async def no_account(_user):
        return None

    for tool in build_sandbox_tools(
        durable.runner, code_host_resolver=no_account, workspace_manager=durable.manager,
        host_git=durable.host_git, token_resolver=None,
    ):
        container.tools.register(tool, overwrite=True)
    result, _ = await durable.call("git_status")
    assert not result.ok and "not connected" in result.content and "cloned" in result.content
    assert not (durable.root / "widgets").exists()
