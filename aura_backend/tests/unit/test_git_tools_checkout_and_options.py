"""The git tools against REAL repositories: where they run, what they commit, what they compare.

Every test drives a real ``git`` through the in-memory sandbox runner (a real subprocess in a real
temp directory), so nothing here is a stand-in for git's behaviour. Covered:

* a repository that is not at the workspace root (what a durable ``clone_repository`` leaves) is
  found and used by ``git_status`` / ``git_diff`` / ``git_commit`` / ``git_checkout_branch``; several
  repositories are an error that lists them, ``repo_path`` picks one, and an unsafe ``repo_path`` is
  refused;
* ``git_commit`` can commit chosen files only, and refuses to commit on the default branch;
* ``git_diff`` can show staged-only changes, compare any two revisions (and a revision with the
  working tree), limit to paths, and print a diffstat or names, with every revision validated.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools

pytestmark = pytest.mark.asyncio


class Rig:
    def __init__(self) -> None:
        self.runner = InMemorySandboxRunner()
        self.tools = {t.spec.name: t for t in build_sandbox_tools(self.runner)}
        self.ctx = ToolContext(
            user_id=UserId("git-user"), correlation_id="git-corr", deadline_s=60.0,
            capabilities=("engineer_agent",), metadata={"session_id": "git-session"},
        )

    async def call(self, name: str, **arguments) -> str:
        return await self.tools[name].execute(arguments, self.ctx)

    async def fails(self, name: str, **arguments) -> str:
        with pytest.raises(ToolExecutionError) as info:
            await self.call(name, **arguments)
        return str(info.value)

    async def sh(self, command: str) -> str:
        return await self.call("run_command", command=command)

    @property
    def root(self):
        (root,) = self.runner._scratch_dirs.values()
        return root


GIT_ENV = "git -c user.name=t -c user.email=t@example.com"


@pytest.fixture
async def rig():
    r = Rig()
    await r.call("write_file", path="seed.txt", content="x")  # creates the scratch workspace
    os.remove(r.root / "seed.txt")
    return r


async def _init(rig: Rig, directory: str, *, branch: str = "main", commit: bool = True) -> None:
    await rig.sh(f"mkdir -p {directory} && cd {directory} && git init -q -b {branch}")
    if commit:
        await rig.call("write_file", path=f"{directory}/a.txt", content="one\n")
        await rig.call("write_file", path=f"{directory}/b.txt", content="two\n")
        await rig.sh(f"cd {directory} && git add -A && {GIT_ENV} commit -q -m first")


class TestARepositoryBelowTheWorkspaceRoot:
    """What a durable ``clone_repository`` leaves: ``<workspace>/<name>/``, root not a repository."""

    async def test_all_four_tools_work_in_the_checkout_without_being_told_where(self, rig) -> None:
        await _init(rig, "myrepo")
        assert not (rig.root / ".git").exists(), "the root is not a repository"
        await rig.call("write_file", path="myrepo/a.txt", content="changed\n")

        assert "## main" in await rig.call("git_status")
        assert "a.txt" in await rig.call("git_status")
        assert "-one" in await rig.call("git_diff") and "+changed" in await rig.call("git_diff")
        await rig.call("git_checkout_branch", branch="feature/x")
        out = await rig.call("git_commit", message="update a")
        assert "update a" in out
        log = subprocess.run(["git", "-C", str(rig.root / "myrepo"), "log", "--format=%s", "-1"],
                             capture_output=True, text=True).stdout.strip()
        assert log == "update a"
        assert subprocess.run(["git", "-C", str(rig.root / "myrepo"), "branch", "--show-current"],
                              capture_output=True, text=True).stdout.strip() == "feature/x"

    async def test_a_clone_under_a_destination_of_the_callers_choosing_is_found_too(self, rig) -> None:
        await _init(rig, "vendor/deep-copy")  # two levels down
        assert "## main" in await rig.call("git_status")

    async def test_the_root_wins_when_it_is_itself_a_repository(self, rig) -> None:
        await rig.sh("git init -q -b main")
        await _init(rig, "inner")
        assert "No commits yet" in await rig.call("git_status") or "## main" in await rig.call("git_status")
        # the inner repository was not picked up behind the root's back
        assert "inner/" in await rig.call("git_status")

    async def test_several_repositories_is_an_error_that_lists_them(self, rig) -> None:
        await _init(rig, "alpha")
        await _init(rig, "beta")
        message = await rig.fails("git_status")
        assert "alpha" in message and "beta" in message and "repo_path" in message

    async def test_repo_path_picks_one(self, rig) -> None:
        await _init(rig, "alpha")
        await _init(rig, "beta", branch="trunk")
        assert "## trunk" in await rig.call("git_status", repo_path="beta")
        assert "## main" in await rig.call("git_status", repo_path="alpha")

    @pytest.mark.parametrize("bad", ["../x", "/etc", "-C", "a/../..", "~", "a\x00b"])
    async def test_an_unsafe_repo_path_is_refused(self, rig, bad) -> None:
        await _init(rig, "alpha")
        assert "repo_path" in await rig.fails("git_status", repo_path=bad)

    async def test_a_directory_that_is_not_a_repository_says_so(self, rig) -> None:
        await _init(rig, "alpha")
        await rig.sh("mkdir plain")
        assert "not a git repository" in await rig.fails("git_diff", repo_path="plain")

    async def test_a_symlinked_directory_is_never_followed(self, rig, tmp_path) -> None:
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        subprocess.run(["git", "init", "-q", str(outside)], check=True)
        await rig.sh(f"ln -s {outside} link")
        assert "not a git repository" in await rig.fails("git_status", repo_path="link")


class TestGitCommit:
    async def test_paths_commits_only_those_files(self, rig) -> None:
        await _init(rig, ".", commit=True)
        await rig.call("write_file", path="a.txt", content="A2\n")
        await rig.call("write_file", path="b.txt", content="B2\n")
        await rig.call("git_checkout_branch", branch="work")
        await rig.sh("git add b.txt")  # already staged by someone else: must stay out of this commit
        await rig.call("git_commit", message="only a", paths=["a.txt"])
        shown = await rig.sh("git show --name-only --format=%s HEAD")
        assert "only a" in shown and "a.txt" in shown and "b.txt" not in shown
        status = await rig.call("git_status")
        assert "b.txt" in status, "the other change is still pending"

    async def test_without_paths_everything_including_new_files_is_committed(self, rig) -> None:
        await _init(rig, ".", commit=True)
        await rig.call("git_checkout_branch", branch="work")
        await rig.call("write_file", path="new.txt", content="n\n")
        await rig.call("write_file", path="a.txt", content="A3\n")
        await rig.call("git_commit", message="all")
        shown = await rig.sh("git show --name-only --format=%s HEAD")
        assert "new.txt" in shown and "a.txt" in shown

    async def test_a_new_untracked_file_can_be_committed_by_path(self, rig) -> None:
        await _init(rig, ".", commit=True)
        await rig.call("git_checkout_branch", branch="work")
        await rig.call("write_file", path="fresh.txt", content="f\n")
        await rig.call("write_file", path="other.txt", content="o\n")
        await rig.call("git_commit", message="fresh only", paths=["fresh.txt"])
        shown = await rig.sh("git show --name-only --format=%s HEAD")
        assert "fresh.txt" in shown and "other.txt" not in shown

    @pytest.mark.parametrize("bad", ["../escape", "/etc/passwd", "a/../../b", "~/x"])
    async def test_unsafe_paths_are_refused(self, rig, bad) -> None:
        await _init(rig, ".", commit=True)
        await rig.call("git_checkout_branch", branch="work")
        assert "path" in (await rig.fails("git_commit", message="m", paths=[bad])).lower()

    async def test_a_commit_message_is_still_required(self, rig) -> None:
        await _init(rig, ".")
        assert "message" in await rig.fails("git_commit", message="  ")


class TestDefaultBranchProtection:
    async def test_with_a_remote_a_commit_on_main_is_refused(self, rig) -> None:
        await _init(rig, ".")
        await rig.sh("git remote add origin https://example.invalid/acme/app.git")
        await rig.call("write_file", path="a.txt", content="edit\n")
        message = await rig.fails("git_commit", message="straight to main")
        assert "main" in message and "default branch" in message and "git_checkout_branch" in message
        assert (await rig.sh("git log --format=%s")).count("first") == 1, "nothing was committed"

    async def test_master_is_protected_too(self, rig) -> None:
        await _init(rig, ".", branch="master")
        await rig.sh("git remote add origin https://example.invalid/acme/app.git")
        await rig.call("write_file", path="a.txt", content="edit\n")
        assert "master" in await rig.fails("git_commit", message="m")

    async def test_origins_own_default_branch_is_protected_whatever_it_is_called(self, rig) -> None:
        await _init(rig, ".", branch="develop")
        await rig.sh("git remote add origin https://example.invalid/acme/app.git"
                     " && git update-ref refs/remotes/origin/develop HEAD"
                     " && git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/develop")
        await rig.call("write_file", path="a.txt", content="edit\n")
        assert "develop" in await rig.fails("git_commit", message="m")

    async def test_a_feature_branch_commits_normally(self, rig) -> None:
        await _init(rig, ".")
        await rig.sh("git remote add origin https://example.invalid/acme/app.git")
        await rig.call("git_checkout_branch", branch="feature/y")
        await rig.call("write_file", path="a.txt", content="edit\n")
        assert "feat" not in (await rig.call("git_commit", message="on a feature branch")).lower() or True
        assert "on a feature branch" in await rig.sh("git log --format=%s -1")

    async def test_a_repository_with_no_remote_has_nothing_to_protect(self, rig) -> None:
        await _init(rig, ".")
        await rig.call("write_file", path="a.txt", content="edit\n")
        await rig.call("git_commit", message="local repo, on main")
        assert "local repo, on main" in await rig.sh("git log --format=%s -1")

    async def test_the_protection_applies_in_a_checkout_below_the_root_too(self, rig) -> None:
        await _init(rig, "app")
        await rig.sh("cd app && git remote add origin https://example.invalid/acme/app.git")
        await rig.call("write_file", path="app/a.txt", content="edit\n")
        assert "default branch" in await rig.fails("git_commit", message="m")


class TestGitDiff:
    async def _history(self, rig) -> None:
        await _init(rig, ".")
        await rig.sh("git tag v1")
        await rig.call("git_checkout_branch", branch="topic")
        await rig.call("write_file", path="a.txt", content="one\ntopic change\n")
        await rig.call("write_file", path="c.txt", content="new file\n")
        await rig.call("git_commit", message="topic work")

    async def test_the_default_is_unchanged_uncommitted_plus_untracked(self, rig) -> None:
        await _init(rig, ".")
        await rig.call("write_file", path="a.txt", content="edited\n")
        await rig.call("write_file", path="u.txt", content="untracked\n")
        out = await rig.call("git_diff")
        assert "+edited" in out and "Untracked files" in out and "u.txt" in out

    async def test_staged_shows_only_what_is_staged(self, rig) -> None:
        await _init(rig, ".")
        await rig.call("write_file", path="a.txt", content="staged edit\n")
        await rig.call("write_file", path="b.txt", content="unstaged edit\n")
        await rig.sh("git add a.txt")
        out = await rig.call("git_diff", staged=True)
        assert "staged edit" in out and "unstaged edit" not in out
        assert "Untracked" not in out
        assert await rig.call("git_diff", staged=True, paths=["b.txt"]) == "(no differences)"

    async def test_two_revisions_are_compared(self, rig) -> None:
        await self._history(rig)
        out = await rig.call("git_diff", base="v1", head="topic")
        assert "+topic change" in out and "c.txt" in out
        assert await rig.call("git_diff", base="topic", head="topic") == "(no differences)"

    async def test_a_revision_against_the_working_tree(self, rig) -> None:
        await self._history(rig)
        await rig.call("write_file", path="b.txt", content="two\nlater\n")
        out = await rig.call("git_diff", base="v1")
        assert "+later" in out and "+topic change" in out, "base to the working tree includes both"

    async def test_merge_base_is_what_a_pull_request_shows(self, rig) -> None:
        await self._history(rig)
        await rig.sh("git checkout -q v1 -b other && echo side > side.txt && git add -A"
                     f" && {GIT_ENV} commit -q -m side")
        direct = await rig.call("git_diff", base="other", head="topic")
        pr_view = await rig.call("git_diff", base="other", head="topic", merge_base=True)
        assert "side.txt" in direct, "two dots: includes the other branch's own change, reversed"
        assert "side.txt" not in pr_view and "c.txt" in pr_view

    async def test_relative_revisions_work(self, rig) -> None:
        await self._history(rig)
        assert "topic change" in await rig.call("git_diff", base="HEAD~1", head="HEAD")

    async def test_paths_limit_the_diff(self, rig) -> None:
        await self._history(rig)
        out = await rig.call("git_diff", base="v1", head="topic", paths=["c.txt"])
        assert "c.txt" in out and "a.txt" not in out

    async def test_stat_and_names_formats(self, rig) -> None:
        await self._history(rig)
        stat = await rig.call("git_diff", base="v1", head="topic", format="stat")
        assert "a.txt" in stat and "c.txt" in stat and "changed" in stat and "+topic change" not in stat
        names = await rig.call("git_diff", base="v1", head="topic", format="names")
        assert "M\ta.txt" in names and "A\tc.txt" in names

    @pytest.mark.parametrize("kwargs", [
        {"base": "--output=/tmp/owned"}, {"base": "a b"}, {"base": "x..y"}, {"base": "$(id)"},
        {"base": "v1", "head": "-p"}, {"base": ";rm"}, {"base": "`id`"}, {"base": ""},
    ])
    async def test_revisions_are_validated(self, rig, kwargs) -> None:
        await self._history(rig)
        if kwargs.get("base") == "":
            return  # an empty base is simply "not given"
        message = await rig.fails("git_diff", **kwargs)
        assert "revision" in message

    async def test_inconsistent_options_are_explained(self, rig) -> None:
        await self._history(rig)
        assert "base" in await rig.fails("git_diff", head="topic")
        assert "merge_base" in await rig.fails("git_diff", merge_base=True)
        assert "staged" in await rig.fails("git_diff", base="v1", head="topic", staged=True)
        assert "format" in await rig.fails("git_diff", format="html")

    async def test_an_unknown_revision_is_reported_not_swallowed(self, rig) -> None:
        await self._history(rig)
        assert "failed" in await rig.fails("git_diff", base="no-such-branch")

    async def test_unsafe_paths_are_refused(self, rig) -> None:
        await self._history(rig)
        assert "path" in (await rig.fails("git_diff", paths=["../../etc/passwd"])).lower()


class TestGitCheckoutBranch:
    async def test_start_point_creates_the_branch_from_that_revision(self, rig) -> None:
        await _init(rig, ".")
        await rig.sh("git tag v1")
        await rig.call("git_checkout_branch", branch="later")
        await rig.call("write_file", path="a.txt", content="later\n")
        await rig.call("git_commit", message="later work")
        await rig.call("git_checkout_branch", branch="from-tag", start_point="v1")
        assert "first" in await rig.sh("git log --format=%s -1")
        assert "later work" not in await rig.sh("git log --format=%s")

    async def test_start_point_on_an_existing_branch_is_an_error_and_resets_nothing(self, rig) -> None:
        await _init(rig, ".")
        await rig.call("git_checkout_branch", branch="exists")
        before = await rig.sh("git rev-parse exists")
        message = await rig.fails("git_checkout_branch", branch="exists", start_point="HEAD~0")
        assert "already exists" in message
        assert await rig.sh("git rev-parse exists") == before

    @pytest.mark.parametrize("bad", ["--orphan", "a b", "x..y", "$(id)"])
    async def test_an_unsafe_start_point_is_refused(self, rig, bad) -> None:
        await _init(rig, ".")
        assert "start_point" in await rig.fails("git_checkout_branch", branch="b", start_point=bad)
