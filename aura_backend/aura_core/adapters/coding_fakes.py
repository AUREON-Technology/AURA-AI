"""In-memory/local fakes for the engineer agent's three adapters.

Same convention as ``adapters/memory_stores.py``: a real, working implementation of each port
that needs no external process, network, or daemon, so ``tests/conftest.py``'s default ``pytest``
run (and any deployment that has not opted into Docker/GitHub/MCP) never needs one. These are not
mocks of AURA's own code -- ``InMemorySandboxRunner`` really runs commands (via
``asyncio.create_subprocess_exec`` in a real temp directory, just without container isolation),
and ``InMemoryGitHubClient``/``InMemoryMcpClient`` really hold state across calls, so tests
exercise the same tool-layer code production runs.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..ports.coding import McpToolDescriptor, RepoFile, RepoTree, SandboxHandle, SandboxResult

__all__ = ["InMemorySandboxRunner", "InMemoryGitHubClient", "InMemoryMcpClient"]


class InMemorySandboxRunner:
    """Runs commands for real, in a real (unsandboxed) temp directory per task.

    Correct for tests -- ``git``, ``sh``, arbitrary commands all behave exactly as
    :class:`~aura_core.adapters.docker_sandbox.DockerSandboxRunner` would from the tool layer's
    point of view -- but provides none of that adapter's container isolation, so it must never be
    wired into a production container.
    """

    def __init__(self) -> None:
        self._scratch_dirs: dict[str, Path] = {}

    async def create_task(self, *, user_id: Any | None = None) -> SandboxHandle:
        # `user_id` accepted for `SandboxRunner` protocol conformance, ignored -- this
        # fake has no concept of per-caller routing.
        task_id = uuid.uuid4().hex
        scratch = await asyncio.to_thread(tempfile.mkdtemp, prefix=f"aura-fake-sandbox-{task_id}-")
        self._scratch_dirs[task_id] = Path(scratch)
        return SandboxHandle(task_id=task_id)

    def _scratch(self, handle: SandboxHandle) -> Path:
        scratch = self._scratch_dirs.get(handle.task_id)
        if scratch is None:
            raise RuntimeError(f"unknown sandbox task {handle.task_id!r}; call create_task first")
        return scratch

    def _resolve_within(self, handle: SandboxHandle, relative: str) -> Path:
        root = self._scratch(handle).resolve()
        if "\x00" in relative:
            raise RuntimeError("path contains a null byte")
        candidate = (root / relative.lstrip("/")).resolve()
        if candidate != root and root not in candidate.parents:
            raise RuntimeError(f"path {relative!r} escapes the sandbox workspace")
        return candidate

    async def run(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> SandboxResult:
        root = self._scratch(handle)
        try:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=str(root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
            return SandboxResult(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                exit_code=process.returncode or 0,
            )
        except asyncio.TimeoutError:
            return SandboxResult(
                stdout="", stderr=f"command timed out after {timeout_s:.0f}s", exit_code=124
            )
        except FileNotFoundError as exc:
            return SandboxResult(stdout="", stderr=str(exc), exit_code=127)

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        target = self._resolve_within(handle, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, "utf-8")

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        target = self._resolve_within(handle, path)
        if not target.is_file():
            raise RuntimeError(f"{path} is not a file in the sandbox workspace")
        return await asyncio.to_thread(target.read_text, "utf-8")

    async def destroy(self, handle: SandboxHandle) -> None:
        scratch = self._scratch_dirs.pop(handle.task_id, None)
        if scratch is not None:
            await asyncio.to_thread(shutil.rmtree, scratch, True)


class InMemoryGitHubClient:
    """Holds repo/branch/file/PR state in memory -- no network, real read-after-write."""

    def __init__(self) -> None:
        self._files: dict[tuple[str, str, str], str] = {}  # (repo, branch, path) -> content
        self._branches: dict[str, set[str]] = {}
        self._issues: dict[tuple[str, int], Mapping[str, Any]] = {}
        self._pulls: dict[tuple[str, int], Mapping[str, Any]] = {}
        self._next_pr_number = 1

    def seed_file(self, repo: str, branch: str, path: str, content: str) -> None:
        self._files[(repo, branch, path)] = content
        self._branches.setdefault(repo, set()).add(branch)

    def seed_issue(self, repo: str, number: int, *, title: str, body: str, state: str = "open") -> None:
        self._issues[(repo, number)] = {"number": number, "title": title, "body": body, "state": state}

    async def get_file_contents(self, repo: str, path: str, *, ref: str | None = None) -> str:
        branch = ref or "main"
        try:
            return self._files[(repo, branch, path)]
        except KeyError as exc:
            raise RuntimeError(f"{path} not found in {repo}@{branch}") from exc

    async def list_files(self, repo: str, *, ref: str | None = None) -> RepoTree:
        branch = ref or "main"
        files = tuple(
            RepoFile(path=path, size=len(content.encode("utf-8")))
            for (r, b, path), content in sorted(self._files.items())
            if r == repo and b == branch
        )
        return RepoTree(files=files)

    async def get_issue(self, repo: str, number: int) -> str:
        issue = self._issues.get((repo, number))
        if issue is None:
            raise RuntimeError(f"issue #{number} not found in {repo}")
        return f"#{issue['number']} {issue['title']} ({issue['state']})\n{issue['body']}"

    async def get_pull_request(self, repo: str, number: int) -> str:
        pr = self._pulls.get((repo, number))
        if pr is None:
            raise RuntimeError(f"PR #{number} not found in {repo}")
        return f"#{pr['number']} {pr['title']} ({pr['state']}) {pr['branch']} -> {pr['base']}\n{pr['body']}"

    async def create_branch(self, repo: str, *, branch: str, from_ref: str) -> str:
        self._branches.setdefault(repo, {"main"}).add(branch)
        for (r, b, path), content in list(self._files.items()):
            if r == repo and b == from_ref:
                self._files[(repo, branch, path)] = content
        return f"Created branch {branch!r} on {repo} from {from_ref!r}"

    async def commit_files(
        self, repo: str, *, branch: str, message: str, files: Mapping[str, str]
    ) -> str:
        self._branches.setdefault(repo, set()).add(branch)
        for path, content in files.items():
            self._files[(repo, branch, path)] = content
        return f"Committed {len(files)} file(s) to {repo}@{branch}: {', '.join(files)}"

    async def create_draft_pull_request(
        self, repo: str, *, branch: str, base: str, title: str, body: str
    ) -> str:
        number = self._next_pr_number
        self._next_pr_number += 1
        self._pulls[(repo, number)] = {
            "number": number, "title": title, "body": body, "state": "open",
            "branch": branch, "base": base, "draft": True,
        }
        return f"Opened draft PR #{number}: https://github.com/{repo}/pull/{number}"

    async def close(self) -> None:
        return None


class InMemoryMcpClient:
    """Scripted MCP client: a fixed tool list, and a caller-supplied handler per tool."""

    def __init__(
        self,
        *,
        tools: Sequence[McpToolDescriptor] = (),
        handlers: Mapping[tuple[str, str], Any] | None = None,
    ) -> None:
        self._tools = list(tools)
        self._handlers = dict(handlers or {})

    async def list_tools(self) -> Sequence[McpToolDescriptor]:
        return list(self._tools)

    async def call_tool(self, server: str, name: str, arguments: Mapping[str, Any]) -> str:
        handler = self._handlers.get((server, name))
        if handler is None:
            raise RuntimeError(f"no handler configured for MCP tool {server}/{name}")
        result = handler(arguments)
        if asyncio.iscoroutine(result):
            result = await result
        return str(result)

    async def close(self) -> None:
        return None
