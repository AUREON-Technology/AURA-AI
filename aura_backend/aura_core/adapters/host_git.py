"""Real ``git``, run by the gateway against a workspace directory.

The point of running it *here* rather than inside the workspace container is the credential. A
private clone needs a token; a workspace runs shell commands a model chose against a repository
an attacker may control. Running git on the host side means the container gets a complete
checkout -- history, branches, tags, everything ``git log`` needs -- while the token never enters
it and never appears in any file it can read.

Three rules make that true rather than merely intended, and all three have been got wrong in real
systems:

1. **The token never reaches argv.** ``/proc/<pid>/cmdline`` is world-readable, so a URL of the
   form ``https://x-access-token:TOKEN@github.com/...`` leaks to every process on the host for as
   long as the clone runs. It is passed through ``GIT_ASKPASS`` instead, which git invokes and
   reads a single line of stdout from.
2. **The token never reaches the checkout.** ``git clone`` records the URL it was given in
   ``.git/config``; if that URL carried the credential, the token is now a file inside the
   directory bind-mounted into the container. The remote is written credential-free from the
   start, and asserted after.
3. **The token never reaches the environment of anything else.** The askpass helper receives it
   through its own environment, and that environment belongs to the git subprocess alone.

Everything here shells out rather than using a git library on purpose: the deployment already
depends on the ``git`` binary (it is in the sandbox image and on the host), and a pure-Python
implementation would be a second, subtly different notion of what a repository is.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..observability.logging import get_logger

__all__ = ["HostGitService", "GitCommandError", "GitResult"]

logger = get_logger("adapters.host_git")

#: Long enough for a large repository over a slow link; short enough that a hung clone fails the
#: turn rather than occupying a worker until the request deadline.
_DEFAULT_TIMEOUT_S = 300.0

#: Written by `clone` unless the caller asks for full history. A workspace is for working on the
#: current state of a branch, and a deep monorepo history is minutes of transfer and gigabytes of
#: disk for something the agent almost never reads. `fetch_full_history` lifts it when it matters.
_DEFAULT_DEPTH = 50

_ASKPASS_SCRIPT = """#!/bin/sh
# git calls this and reads one line of stdout. The value is in this process's environment, never
# in argv, so it does not appear in /proc/<pid>/cmdline for other processes on the host.
printf '%s\\n' "$AURA_GIT_TOKEN"
"""


class GitCommandError(RuntimeError):
    """A git invocation failed. Carries stderr, with the token scrubbed if it ever appeared."""


@dataclass(frozen=True, slots=True)
class GitResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _scrub(text: str, token: str | None) -> str:
    """Remove a credential from output before it is logged or shown to a model.

    Belt and braces: the rules above are meant to keep the token out of git's output entirely,
    but a message from a version of git that echoes a URL differently should not be the thing
    that leaks it.
    """
    if not token:
        return text
    return text.replace(token, "***")


class HostGitService:
    """Runs git commands against workspace directories on this host."""

    def __init__(self, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    async def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        token: str | None = None,
        timeout_s: float | None = None,
    ) -> GitResult:
        askpass_dir: str | None = None
        env = {
            # A deliberately minimal environment. Inheriting the gateway's would hand git every
            # secret this process holds, and git reads more environment variables than anyone
            # remembers.
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(cwd),
            "GIT_TERMINAL_PROMPT": "0",  # never block a turn waiting on a password prompt
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "LC_ALL": "C",
        }
        try:
            if token:
                askpass_dir = tempfile.mkdtemp(prefix="aura-git-askpass-")
                helper = Path(askpass_dir) / "askpass.sh"
                helper.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
                helper.chmod(stat.S_IRWXU)  # 0700: readable only by this process's user
                env["GIT_ASKPASS"] = str(helper)
                env["AURA_GIT_TOKEN"] = token
                # Any username works for a GitHub token; the token is the password.
                env["GIT_USERNAME"] = "x-access-token"

            process = await asyncio.create_subprocess_exec(
                "git", *args,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_s or self._timeout_s
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise GitCommandError(
                    f"git {args[0]} timed out after {timeout_s or self._timeout_s:.0f}s"
                ) from None

            return GitResult(
                stdout=_scrub(stdout.decode("utf-8", errors="replace"), token),
                stderr=_scrub(stderr.decode("utf-8", errors="replace"), token),
                exit_code=process.returncode or 0,
            )
        finally:
            if askpass_dir:
                shutil.rmtree(askpass_dir, ignore_errors=True)

    async def _checked(
        self, args: Sequence[str], *, cwd: Path, token: str | None = None,
        timeout_s: float | None = None,
    ) -> GitResult:
        result = await self._run(args, cwd=cwd, token=token, timeout_s=timeout_s)
        if not result.ok:
            raise GitCommandError(
                f"git {' '.join(args[:2])} failed ({result.exit_code}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    @staticmethod
    def remote_url(repo: str) -> str:
        """Credential-free HTTPS remote. The only URL that is ever written to disk."""
        return f"https://github.com/{repo}.git"

    async def clone(
        self,
        repo: str,
        *,
        dest: Path,
        token: str | None = None,
        ref: str | None = None,
        depth: int | None = _DEFAULT_DEPTH,
    ) -> GitResult:
        """Clone ``repo`` into ``dest``, leaving no credential behind.

        The remote is the credential-free URL from the start, so ``.git/config`` never holds a
        token even transiently -- rewriting it afterwards would leave a window in which a command
        running in the container could read it, and this directory is bind-mounted there.
        """
        dest.mkdir(parents=True, exist_ok=True)
        args = ["clone", self.remote_url(repo)]
        if depth:
            # Shallow, but not single-branch: an agent asked to compare against main needs the
            # other branch to exist.
            args += ["--depth", str(depth), "--no-single-branch"]
        if ref:
            args += ["--branch", ref]
        args.append(str(dest))

        result = await self._checked(args, cwd=dest.parent, token=token)
        await self._assert_no_credential_in_config(dest)
        return result

    async def _assert_no_credential_in_config(self, repo_dir: Path) -> None:
        """Fail loudly if a token ended up in ``.git/config``.

        This is the assertion behind the promise the tool layer makes to the user, so it is
        checked rather than assumed: the config file is inside the directory bind-mounted into a
        container running model-chosen commands, and `grep -r` on it is one of the first things a
        hostile repository's build script would try.
        """
        config = repo_dir / ".git" / "config"
        if not config.is_file():
            return
        text = await asyncio.to_thread(config.read_text, "utf-8")
        if "@github.com" in text or "x-access-token" in text:
            raise GitCommandError(
                "refusing to continue: a credential reached .git/config, which is readable "
                "from inside the workspace container"
            )

    async def fetch(self, repo_dir: Path, *, token: str | None = None) -> GitResult:
        return await self._checked(["fetch", "--all", "--prune"], cwd=repo_dir, token=token)

    async def checkout_branch(
        self, repo_dir: Path, *, branch: str, create: bool = True
    ) -> GitResult:
        args = ["checkout", "-B", branch] if create else ["checkout", branch]
        return await self._checked(args, cwd=repo_dir)

    async def current_branch(self, repo_dir: Path) -> str:
        result = await self._run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
        return result.stdout.strip()

    async def default_branch(self, repo_dir: Path) -> str:
        """The remote's HEAD, used to refuse pushes to it.

        Falls back to ``main`` when the symbolic ref is missing, which happens on a shallow clone
        that never fetched it -- the conservative direction, since the fallback only ever adds a
        name to the refuse-list.
        """
        result = await self._run(
            ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], cwd=repo_dir
        )
        if result.ok and result.stdout.strip():
            return result.stdout.strip().split("/", 1)[-1]
        return "main"

    async def status(self, repo_dir: Path) -> str:
        return (await self._run(["status", "--short", "--branch"], cwd=repo_dir)).stdout

    async def diff(self, repo_dir: Path, *, staged: bool = False) -> str:
        args = ["diff", "--cached"] if staged else ["diff"]
        return (await self._run(args, cwd=repo_dir)).stdout

    async def diffstat(self, repo_dir: Path, *, against: str = "origin") -> str:
        """A summary of what a push would carry, for the confirmation prompt.

        A person asked to approve `git_push` should see what is in it. "Approve tool git_push" is
        not a decision anyone can make well.
        """
        result = await self._run(["diff", "--stat", f"{against}/HEAD...HEAD"], cwd=repo_dir)
        if result.ok and result.stdout.strip():
            return result.stdout
        return (await self._run(["diff", "--stat", "HEAD~1..HEAD"], cwd=repo_dir)).stdout

    async def commit(self, repo_dir: Path, *, message: str, author: str | None = None) -> GitResult:
        await self._checked(["add", "-A"], cwd=repo_dir)
        args = ["commit", "-m", message]
        if author:
            args += ["--author", author]
        # Set per-invocation rather than in a config file: the identity belongs to this commit,
        # and writing it into the checkout would leave it for whoever opens the workspace next.
        args = [
            "-c", "user.name=AURA Engineer Agent",
            "-c", "user.email=engineer-agent@aura.local",
            *args,
        ]
        return await self._checked(args, cwd=repo_dir)

    async def push(
        self, repo_dir: Path, *, branch: str, token: str | None = None, repo: str | None = None
    ) -> GitResult:
        """Push ``branch`` to origin. Callers gate this; it does not gate itself."""
        args = ["push", "--set-upstream", self.remote_url(repo) if repo else "origin", branch]
        return await self._checked(args, cwd=repo_dir, token=token)

    async def log(self, repo_dir: Path, *, limit: int = 20) -> str:
        return (
            await self._run(["log", f"-{limit}", "--oneline", "--no-decorate"], cwd=repo_dir)
        ).stdout

    async def is_repository(self, path: Path) -> bool:
        return (path / ".git").exists()
