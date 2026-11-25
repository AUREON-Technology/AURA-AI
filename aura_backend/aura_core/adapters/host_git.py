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

**When the workspace's files are not on this host** (a cloud sandbox), git never runs where the
sandbox can write at all. A clone is made in a folder of the gateway's own and moved in
(``ports/coding.py::RepositoryTransferRunner``). A push takes a *bundle* out of the sandbox into
a second clone made here (:meth:`HostGitService.clone_for_push`): the bundle's header must carry
exactly the branch asked for, its commits must build on history the remote really has, and its
objects pass ``git``'s own receive-time checks (:meth:`HostGitService.import_bundle`). The
sandbox's configuration and hooks never reach this host.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

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


#: Overrides every host-side git command is run with. ``-c`` outranks anything in a repository's own
#: ``.git/config``, and this process runs git in directories that a container running model-chosen
#: commands can write to -- including ``.git/hooks``. A hook is not configuration, so an allowlist of
#: config keys (below) cannot see it: without ``core.hooksPath`` pointing nowhere, a ``pre-push``
#: script the container wrote would run *here*, in the gateway, with the token in its environment.
_HARDENING: tuple[str, ...] = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "credential.helper=",
    "-c", "protocol.ext.allow=never",
)

#: ``.git/config`` keys that may exist in a checkout this process runs git on *with a credential*.
#: The file is writable from inside the workspace container, and several keys turn a push or fetch
#: into a credential leak or a code execution here: ``url.<x>.insteadOf`` re-points the remote at a
#: host of the writer's choosing, ``http.*`` redirects or weakens TLS, ``credential.*`` hands the
#: token to a program, ``core.sshCommand``/``core.gitProxy``/``include.path`` run or import more.
#: Anything not matched below is refused rather than judged, because the list of keys that can do
#: that is longer than anyone remembers and an allowlist fails closed.
_SAFE_CONFIG_KEYS = re.compile(
    r"^(?:"
    r"core\.(?:repositoryformatversion|filemode|bare|logallrefupdates|symlinks|ignorecase|"
    r"precomposeunicode|autocrlf|eol|safecrlf|longpaths|sparsecheckout)|"
    r"user\.(?:name|email)|"
    r"init\.defaultbranch|"
    r"branch\..+\.(?:remote|merge|rebase|description)|"
    r"remote\..+\.(?:url|fetch|tagopt|prunetags)|"
    r"(?:advice|color)\..+|"
    r"push\.default|pull\.rebase|fetch\.prune|"
    r"extensions\.(?:objectformat|worktreeconfig)"
    r")$"
)
#: What ``remote.<name>.url`` may be when a credential is about to be used against that remote.
_CREDENTIAL_REMOTE_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?/?$")


#: Object checks that are only warnings by default, raised to errors for a sandbox's bundle: each
#: is a path that would escape or poison a later checkout. The ``gitmodules*`` ones already fail.
_BUNDLE_FSCK = ",".join(
    f"{check}=error"
    for check in ("hasDot", "hasDotdot", "hasDotgit", "emptyName", "fullPathname")
)
_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
#: A bundle header is a handful of short lines; anything far beyond that is not one.
_BUNDLE_LINE_BYTES = 4096
_BUNDLE_HEADER_LINES = 10_000


class GitCommandError(RuntimeError):
    """A git invocation failed. Carries stderr, with the token scrubbed if it ever appeared."""


def _bundle_header(path: Path) -> tuple[list[str], list[tuple[str, str]], int]:
    """``(prerequisite commits, (ref, commit) pairs, offset of the pack)`` of a git bundle.

    Read here rather than asked of ``git`` so that exactly what is imported is what was checked,
    and so that the same rules hold whichever git version the image carries.
    """
    unreadable = GitCommandError("refusing the bundle: it is not a plain git bundle")
    prerequisites: list[str] = []
    refs: list[tuple[str, str]] = []
    with path.open("rb") as stream:
        version = stream.readline(_BUNDLE_LINE_BYTES)
        if version not in (b"# v2 git bundle\n", b"# v3 git bundle\n"):
            raise unreadable
        for _ in range(_BUNDLE_HEADER_LINES):
            line = stream.readline(_BUNDLE_LINE_BYTES)
            if line == b"\n":
                return prerequisites, refs, stream.tell()
            if not line.endswith(b"\n"):
                break
            try:
                text = line[:-1].decode("utf-8")
            except UnicodeDecodeError:
                raise unreadable from None
            if text.startswith("@"):
                # A capability. Only the object format is understood; a partial ("filter")
                # bundle would import a history with holes in it.
                if not text.startswith("@object-format="):
                    raise unreadable
            elif text.startswith("-"):
                oid = text[1:].split(" ", 1)[0]
                if not _OBJECT_ID.fullmatch(oid):
                    raise unreadable
                prerequisites.append(oid)
            else:
                oid, _, name = text.partition(" ")
                if not _OBJECT_ID.fullmatch(oid) or not name:
                    raise unreadable
                refs.append((name, oid))
    raise unreadable


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
        stdin: Any = None,
    ) -> GitResult:
        if token:
            await self._assert_safe_config_for_credential(cwd, fetches=bool(args) and args[0] in ("fetch", "pull"))
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
                "git", *_HARDENING, *args,
                cwd=str(cwd),
                env=env,
                stdin=stdin,
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

    async def _assert_safe_config_for_credential(self, cwd: Path, *, fetches: bool) -> None:
        """Refuse to use a credential in a repository whose config the workspace has tampered with.

        Reads the local config only (``git config --list`` executes nothing) and checks every key
        against :data:`_SAFE_CONFIG_KEYS`; for a ``fetch``, every ``remote.*.url`` must also be a
        plain GitHub URL. When ``cwd`` is not inside a repository there is no local config to check.
        """
        result = await self._run(["config", "--local", "--list", "--no-includes", "-z"], cwd=cwd)
        if not result.ok:
            if any((directory / ".git").exists() for directory in (cwd, *cwd.parents)):
                # Inside a repository whose config cannot even be read: that is not "nothing to
                # check", it is a config git itself rejects, and the credential stays home.
                raise GitCommandError(
                    "refusing to use the GitHub credential: this workspace's git configuration "
                    f"cannot be read ({result.stderr.strip()[:200] or 'unknown error'})"
                )
            return
        for entry in result.stdout.split("\0"):
            if not entry:
                continue
            key, _, value = entry.partition("\n")
            lowered = key.lower()
            if not _SAFE_CONFIG_KEYS.match(lowered):
                raise GitCommandError(
                    f"refusing to use the GitHub credential: the workspace's .git/config sets "
                    f"{key!r}, which can redirect or capture it. Remove that setting, or destroy "
                    "and re-clone the workspace."
                )
            if (
                fetches
                and lowered.startswith("remote.")
                and lowered.endswith(".url")
                and not _CREDENTIAL_REMOTE_URL.match(value)
            ):
                # `fetch` uses the remotes' own URLs, so with a credential it must only ever
                # talk to GitHub. (`push` names its URL itself and never reads these.)
                raise GitCommandError(
                    f"refusing to use the GitHub credential: {key!r} is {value!r}, not a GitHub "
                    "repository URL."
                )

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
        existed = dest.exists()
        dest.mkdir(parents=True, exist_ok=True)
        args = ["clone"]
        if depth:
            # Shallow, but not single-branch: an agent asked to compare against main needs the
            # other branch to exist.
            args += ["--depth", str(depth), "--no-single-branch"]
        if ref:
            args += ["--branch", ref]
        # `--` so neither the URL nor the destination can ever be read as an option.
        args += ["--", self.remote_url(repo), str(dest)]

        try:
            result = await self._checked(args, cwd=dest.parent, token=token)
            await self._assert_no_credential_in_config(dest)
        except BaseException:
            # A failed clone (bad ref, no access, timeout) leaves a partial `.git` behind, and the
            # next attempt then fails with "already exists and is not an empty directory" -- a
            # second, unrelated-looking error for the same problem. Only a directory this call
            # made is removed; one that was already there is the caller's.
            if not existed:
                shutil.rmtree(dest, ignore_errors=True)
            raise
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
        """Push ``branch`` to origin. Callers gate this; it does not gate itself.

        The refspec is always spelled out (``refs/heads/X:refs/heads/X``) after ``--``, so the
        branch can never be read as an option (``--mirror``, ``--force``) or as a refspec whose
        destination differs from its name (``HEAD:main``) -- the caller's default-branch check
        compares names, and this is what makes the name the destination.
        """
        ref = f"refs/heads/{branch}"
        args = [
            "push", "--set-upstream", "--",
            self.remote_url(repo) if repo else "origin", f"{ref}:{ref}",
        ]
        return await self._checked(args, cwd=repo_dir, token=token)

    # -- pushing from a sandbox whose files are not here -----------------------------------

    async def clone_for_push(
        self, repo: str, *, dest: Path, token: str | None = None
    ) -> GitResult:
        """A bare, full clone of ``repo`` in a folder only this process writes to.

        Full history, because the sandbox's commits may build on anything the remote has; bare,
        because nothing is ever checked out; from an empty template, so it starts with no hook.
        """
        template = dest.parent / "empty-template"
        template.mkdir(parents=True, exist_ok=True)
        return await self._checked(
            ["clone", "--bare", f"--template={template}", "--", self.remote_url(repo), str(dest)],
            cwd=dest.parent, token=token,
        )

    async def default_branch_of(self, bare: Path) -> str:
        """The remote's default branch, as a fresh :meth:`clone_for_push` recorded it."""
        result = await self._checked(["symbolic-ref", "--short", "HEAD"], cwd=bare)
        return result.stdout.strip()

    async def has_commit(self, repo_dir: Path, oid: str) -> bool:
        if not _OBJECT_ID.fullmatch(oid):
            return False
        return (await self._run(["cat-file", "-e", f"{oid}^{{commit}}"], cwd=repo_dir)).ok

    async def import_bundle(self, bare: Path, bundle: Path, *, branch: str) -> str:
        """Add a sandbox's bundle to ``bare`` after checking it, and return the branch's tip.

        Refuses a bundle carrying anything but ``refs/heads/<branch>``, commits that build on
        history this clone does not have, and objects ``index-pack --strict`` rejects (the check
        a server applies to a push).
        """
        prerequisites, refs, offset = await asyncio.to_thread(_bundle_header, bundle)
        wanted = f"refs/heads/{branch}"
        if [name for name, _ in refs] != [wanted]:
            raise GitCommandError(
                f"refusing the bundle: it must carry exactly {wanted}, not "
                f"{', '.join(name for name, _ in refs)[:200] or 'nothing'}"
            )
        tip = refs[0][1]
        for oid in prerequisites:
            if not await self.has_commit(bare, oid):
                raise GitCommandError(
                    "the new commits build on history the remote does not have (it may have been "
                    "rewritten); fetch in the workspace and rebase, or clone again"
                )
        # Unbuffered, so git reads from exactly where the header ended.
        with open(bundle, "rb", buffering=0) as pack:
            pack.seek(offset)
            result = await self._run(
                ["index-pack", "--stdin", "--fix-thin", f"--strict={_BUNDLE_FSCK}"],
                cwd=bare, stdin=pack,
            )
        if result.ok:
            # Every object the tip needs is now present.
            result = await self._run(
                ["rev-list", "--objects", "--quiet", tip, "--not", "--all"], cwd=bare
            )
        if not result.ok or not await self.has_commit(bare, tip):
            raise GitCommandError(
                "the commits failed the object check and were not pushed: "
                f"{result.stderr.strip()[:300]}"
            )
        return tip

    async def push_commit(
        self, bare: Path, *, tip: str, branch: str, repo: str, token: str | None = None
    ) -> GitResult:
        """Push ``tip`` to ``refs/heads/<branch>`` from a :meth:`clone_for_push`. Never forced."""
        return await self._checked(
            ["push", "--", self.remote_url(repo), f"{tip}:refs/heads/{branch}"],
            cwd=bare, token=token,
        )

    async def log(self, repo_dir: Path, *, limit: int = 20) -> str:
        return (
            await self._run(["log", f"-{limit}", "--oneline", "--no-decorate"], cwd=repo_dir)
        ).stdout

    async def is_repository(self, path: Path) -> bool:
        # A real directory, not a symlink or a `gitdir:` file: those are how a workspace could
        # point this process's credentialed git at a repository that is not the checkout.
        git_dir = path / ".git"
        return git_dir.is_dir() and not git_dir.is_symlink()
