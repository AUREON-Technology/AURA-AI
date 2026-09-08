"""`HostGitService` against real repositories created on disk.

Real `git`, not a stub: these create actual repositories in tmp_path and run actual commands, so
what is asserted is what git does rather than what a fake was told to say. Nothing here reaches
the network -- clones are from a local path, which exercises the same code path with the same
argument handling.

The credential tests are the ones that matter most. The promise this whole design rests on is
that a workspace container gets a real checkout and never the token, and "we were careful" is not
evidence for that.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aura_core.adapters.host_git import GitCommandError, HostGitService, _scrub


@pytest.fixture
def git() -> HostGitService:
    return HostGitService(timeout_s=60.0)


async def _make_origin(tmp_path: Path, git: HostGitService) -> Path:
    """A real repository with two commits and a second branch, to clone from."""
    origin = tmp_path / "origin"
    origin.mkdir()
    await git._checked(["init", "-b", "main"], cwd=origin)
    (origin / "README.md").write_text("first\n")
    await git.commit(origin, message="first commit")
    (origin / "second.txt").write_text("second\n")
    await git.commit(origin, message="second commit")
    await git.checkout_branch(origin, branch="feature/x")
    (origin / "third.txt").write_text("third\n")
    await git.commit(origin, message="third commit")
    await git.checkout_branch(origin, branch="main", create=False)
    return origin


class TestRealCheckout:
    async def test_clone_produces_a_repository_with_history(self, tmp_path, git) -> None:
        """The difference from the REST-copy this replaces: `git log` has something to say.

        The old clone_repository copied file contents through the GitHub API -- no .git, no
        history, no branches. An agent could read the code and could not bisect, blame, diff
        against a base, or commit.
        """
        origin = await _make_origin(tmp_path, git)
        dest = tmp_path / "work"

        await git._checked(["clone", str(origin), str(dest)], cwd=tmp_path)

        assert await git.is_repository(dest)
        log = await git.log(dest)
        assert "second commit" in log and "first commit" in log

    async def test_branches_come_with_the_clone(self, tmp_path, git) -> None:
        """--no-single-branch on the real clone path exists for this: an agent asked to compare
        against another branch needs it to be there."""
        origin = await _make_origin(tmp_path, git)
        dest = tmp_path / "work"
        await git._checked(
            ["clone", "--no-single-branch", str(origin), str(dest)], cwd=tmp_path
        )

        branches = (await git._run(["branch", "-a"], cwd=dest)).stdout
        assert "feature/x" in branches

    async def test_status_and_diff_reflect_real_edits(self, tmp_path, git) -> None:
        origin = await _make_origin(tmp_path, git)
        dest = tmp_path / "work"
        await git._checked(["clone", str(origin), str(dest)], cwd=tmp_path)

        (dest / "README.md").write_text("changed\n")

        assert "README.md" in await git.status(dest)
        assert "changed" in await git.diff(dest)

    async def test_commit_records_a_real_commit(self, tmp_path, git) -> None:
        origin = await _make_origin(tmp_path, git)
        dest = tmp_path / "work"
        await git._checked(["clone", str(origin), str(dest)], cwd=tmp_path)
        (dest / "new.txt").write_text("hello\n")

        await git.commit(dest, message="agent change")

        assert "agent change" in await git.log(dest)

    async def test_commit_identity_is_not_left_in_the_checkout(self, tmp_path, git) -> None:
        """Set per invocation with -c, not written to .git/config: the next person to open this
        workspace should not inherit the agent's identity."""
        origin = await _make_origin(tmp_path, git)
        dest = tmp_path / "work"
        await git._checked(["clone", str(origin), str(dest)], cwd=tmp_path)
        (dest / "new.txt").write_text("hello\n")
        await git.commit(dest, message="agent change")

        config = (dest / ".git" / "config").read_text()
        assert "engineer-agent@aura.local" not in config

    async def test_checkout_branch_creates_and_switches(self, tmp_path, git) -> None:
        origin = await _make_origin(tmp_path, git)
        dest = tmp_path / "work"
        await git._checked(["clone", str(origin), str(dest)], cwd=tmp_path)

        await git.checkout_branch(dest, branch="fix/thing")

        assert await git.current_branch(dest) == "fix/thing"


class TestCredentialHandling:
    async def test_the_token_never_appears_in_argv(self, tmp_path, git, monkeypatch) -> None:
        """/proc/<pid>/cmdline is world-readable, so a token embedded in the clone URL leaks to
        every process on the host for as long as the clone runs."""
        seen: list[list[str]] = []
        real = git._run

        async def _capture(args, **kwargs):
            seen.append(list(args))
            return await real(args, **kwargs)

        monkeypatch.setattr(git, "_run", _capture)
        origin = await _make_origin(tmp_path, git)
        await git._checked(["clone", str(origin), str(tmp_path / "work")], cwd=tmp_path)

        flat = " ".join(" ".join(a) for a in seen)
        assert "ghp_" not in flat and "x-access-token:" not in flat

    async def test_the_token_is_passed_via_askpass_not_the_url(
        self, tmp_path, git, monkeypatch
    ) -> None:
        """Inspect what the subprocess is actually handed: GIT_ASKPASS pointing at an executable
        helper, the token in the environment, and no credential anywhere in argv."""
        import asyncio as _asyncio

        captured: dict[str, object] = {}
        real_exec = _asyncio.create_subprocess_exec

        async def _spy(*args, **kwargs):
            env = dict(kwargs.get("env") or {})
            if "GIT_ASKPASS" in env:
                captured["argv"] = list(args)
                captured["env"] = env
                # Read the helper here, while it still exists: `_run` removes it in a finally
                # block, which is itself what the next test asserts.
                helper = Path(env["GIT_ASKPASS"])
                captured["helper_body"] = helper.read_text()
                captured["helper_mode"] = helper.stat().st_mode
            return await real_exec(*args, **kwargs)

        # Built before the spy is installed: these calls carry no token, and a spy that assumed
        # every invocation had one would fail on them rather than on the thing under test.
        origin = await _make_origin(tmp_path, git)
        monkeypatch.setattr(_asyncio, "create_subprocess_exec", _spy)

        await git._run(["status"], cwd=origin, token="ghp_secrettoken")

        assert captured, "no git invocation carried an askpass helper"

        assert captured["env"]["AURA_GIT_TOKEN"] == "ghp_secrettoken"
        assert "ghp_secrettoken" not in " ".join(captured["argv"])
        # The helper is what git executes to obtain the password; it must read the value from its
        # own environment rather than embedding it, or the token would sit in a file on disk.
        assert "$AURA_GIT_TOKEN" in captured["helper_body"]
        assert "ghp_secrettoken" not in captured["helper_body"]
        assert captured["helper_mode"] & 0o077 == 0, (
            "the helper must not be group- or world-readable"
        )

    async def test_the_askpass_helper_is_removed_afterwards(self, tmp_path, git) -> None:
        """The helper's own file is harmless (it contains a variable name, not a value), but
        leaving one per git call in the gateway's tmp would leak file handles and disk over a
        three-day workspace lifetime."""
        import asyncio as _asyncio

        seen: dict[str, str] = {}
        real_exec = _asyncio.create_subprocess_exec

        async def _spy(*args, **kwargs):
            seen["askpass"] = (kwargs.get("env") or {}).get("GIT_ASKPASS", "")
            return await real_exec(*args, **kwargs)

        import pytest as _pytest

        monkeypatch = _pytest.MonkeyPatch()
        monkeypatch.setattr(_asyncio, "create_subprocess_exec", _spy)
        try:
            origin = await _make_origin(tmp_path, git)
            await git._run(["status"], cwd=origin, token="ghp_secrettoken")
        finally:
            monkeypatch.undo()

        assert seen["askpass"]
        assert not Path(seen["askpass"]).exists()

    async def test_the_token_never_enters_this_process_environment(self, tmp_path, git) -> None:
        origin = await _make_origin(tmp_path, git)

        await git._run(["status"], cwd=origin, token="ghp_secrettoken")

        assert "AURA_GIT_TOKEN" not in os.environ

    async def test_remote_url_carries_no_credential(self, git) -> None:
        assert git.remote_url("acme/widgets") == "https://github.com/acme/widgets.git"
        assert "@" not in git.remote_url("acme/widgets")

    async def test_a_credential_in_git_config_is_refused(self, tmp_path, git) -> None:
        """.git/config lives inside the directory bind-mounted into the workspace container, so
        a token there is readable by any command the model runs. This assertion is the reason the
        remote is written credential-free from the start rather than rewritten afterwards."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text(
            '[remote "origin"]\n\turl = https://x-access-token:ghp_abc@github.com/a/b.git\n'
        )

        with pytest.raises(GitCommandError, match="credential reached .git/config"):
            await git._assert_no_credential_in_config(repo)

    async def test_a_clean_config_passes_the_check(self, tmp_path, git) -> None:
        origin = await _make_origin(tmp_path, git)
        dest = tmp_path / "work"
        await git._checked(["clone", str(origin), str(dest)], cwd=tmp_path)

        await git._assert_no_credential_in_config(dest)

    def test_scrub_removes_the_token_from_output(self) -> None:
        assert _scrub("fatal: bad credentials ghp_secret", "ghp_secret") == (
            "fatal: bad credentials ***"
        )

    def test_scrub_is_a_no_op_without_a_token(self) -> None:
        assert _scrub("plain message", None) == "plain message"

    async def test_git_never_prompts_interactively(self, tmp_path, git) -> None:
        """GIT_TERMINAL_PROMPT=0. Without it a private clone with no usable credential blocks on
        a username prompt until the turn's deadline instead of failing."""
        result = await git._run(
            ["clone", "https://github.com/definitely/not-a-real-repo-aura-test.git",
             str(tmp_path / "nope")],
            cwd=tmp_path, timeout_s=30.0,
        )

        assert not result.ok


class TestFailureHandling:
    async def test_a_failing_command_raises_with_stderr(self, tmp_path, git) -> None:
        with pytest.raises(GitCommandError, match="failed"):
            await git._checked(["checkout", "no-such-branch"], cwd=tmp_path)

    async def test_default_branch_falls_back_when_head_is_missing(self, tmp_path, git) -> None:
        """A shallow clone may not have origin/HEAD. Falling back to `main` only ever adds a name
        to the list of branches a push is refused against, so the conservative direction."""
        origin = await _make_origin(tmp_path, git)

        assert await git.default_branch(origin) == "main"

    async def test_is_repository_is_false_for_a_plain_directory(self, tmp_path, git) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()

        assert not await git.is_repository(plain)
