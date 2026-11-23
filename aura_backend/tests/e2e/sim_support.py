"""Shared stand-ins for ``test_incomplete_tools_*_simulated.py``.

Everything here follows the real contract of the thing it replaces; each helper says what it
stands in for. Nothing here is imported by product code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from aura_core.adapters.host_git import HostGitService
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.ports.runtime import ToolContext

ENGINEER = ("engineer_agent",)
ALICE = UserId("alice-sim")


def context(correlation_id: str = "sim-1", *, user: UserId = ALICE, **kw: Any) -> ToolContext:
    return ToolContext(
        user_id=user, correlation_id=correlation_id, deadline_s=60.0, capabilities=ENGINEER,
        metadata={"session_id": "sim-session"}, **kw,
    )


async def call(registry: Any, name: str, *, confirmed: bool = False, ctx: ToolContext | None = None, **arguments: Any):
    """One call through the real ``ToolRegistry.execute`` as the Engineer persona."""
    result, report = await registry.execute(
        ToolCall(id=ToolCallId.new(), name=name, arguments=arguments), ctx or context(),
        capabilities=ENGINEER, confirmed=confirmed,
    )
    return result, report


async def ok_json(registry: Any, name: str, **arguments: Any) -> dict:
    """A call that must succeed and whose content is a JSON object."""
    result, _report = await call(registry, name, **arguments)
    assert result.ok, f"{name} failed: {result.content}"
    return json.loads(result.content)


def git(cwd: Path, *args: str) -> str:
    """Plain git in a temp dir for building the *remote* side of a simulation (never product code)."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "LC_ALL": "C",
        "GIT_AUTHOR_NAME": "Remote", "GIT_AUTHOR_EMAIL": "remote@example.test",
        "GIT_COMMITTER_NAME": "Remote", "GIT_COMMITTER_EMAIL": "remote@example.test",
    }
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout


class LocalRemoteGit(HostGitService):
    """``HostGitService`` whose "GitHub" is a directory of bare repositories on disk.

    SIMULATED: only :meth:`remote_url` (which the real class hard-codes to https://github.com) is
    replaced; every clone, status, commit and push is the real service running the real ``git``
    binary against a real (local) remote.
    """

    def __init__(self, remotes: Path, **kw: Any) -> None:
        super().__init__(**kw)
        self._remotes = remotes

    def remote_url(self, repo: str) -> str:  # type: ignore[override]
        return (self._remotes / (repo.replace("/", "__") + ".git")).as_uri()

    def make_remote(self, repo: str, files: dict[str, str], *, default_branch: str = "main") -> Path:
        bare = self._remotes / (repo.replace("/", "__") + ".git")
        bare.parent.mkdir(parents=True, exist_ok=True)
        git(bare.parent, "init", "-q", "--bare", "-b", default_branch, bare.name)
        seed = self._remotes / (repo.replace("/", "__") + ".seed")
        seed.mkdir()
        git(seed, "init", "-q", "-b", default_branch)
        for name, text in files.items():
            target = seed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "initial")
        git(seed, "remote", "add", "origin", bare.as_uri())
        git(seed, "push", "-q", "origin", default_branch)
        shutil.rmtree(seed)
        return bare


class _FakeDockerContainer:
    def __init__(self, kwargs: dict[str, Any], log: list[dict[str, Any]]) -> None:
        self.kwargs = kwargs
        self._stdout = b""
        self._stderr = b""
        self.killed = False
        self.removed = False
        log.append(kwargs)

    def wait(self, timeout: float | None = None) -> dict[str, int]:
        # The "container": the command really runs, in the directory the volume binds to
        # /workspace, with the container's tmpfs-only HOME. It is NOT isolated (that is Docker's job).
        (host_dir, spec), = self.kwargs["volumes"].items()
        assert spec["bind"] == "/workspace"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": host_dir, "LC_ALL": "C"}
        try:
            done = subprocess.run(
                list(self.kwargs["command"]), cwd=host_dir, env=env, capture_output=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            class ReadTimeout(Exception):  # docker-py surfaces requests' ReadTimeout, matched by name
                pass

            raise ReadTimeout("read timed out") from None
        self._stdout, self._stderr = done.stdout, done.stderr
        return {"StatusCode": done.returncode}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        return (self._stdout if stdout else b"") + (self._stderr if stderr else b"")

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeDockerClient:
    """The slice of ``docker.DockerClient`` that ``DockerSandboxRunner.run`` uses.

    SIMULATED: no daemon. ``containers.run(**kwargs)`` records the launch arguments (so a test can
    assert the hardening flags the real daemon would receive) and executes the command as a plain
    subprocess in the bind-mounted directory. ``exec_run``/``exec_create`` exist only to fail the
    test: the socket proxy answers 403 to them in production, so the adapter must never call them.
    """

    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.containers_made: list[_FakeDockerContainer] = []
        outer = self

        class _Containers:
            def run(self, **kwargs: Any) -> _FakeDockerContainer:
                container = _FakeDockerContainer(kwargs, outer.launches)
                outer.containers_made.append(container)
                return container

            def __getattr__(self, name: str) -> Any:
                raise AssertionError(f"the sandbox adapter must not call containers.{name} (exec is 403 behind the socket proxy)")

        self.containers = _Containers()



