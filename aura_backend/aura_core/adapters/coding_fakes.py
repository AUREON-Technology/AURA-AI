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

from ..domain.files import strip_absolute_prefix
from ..ports.coding import FileWriteOp, McpToolDescriptor, RepoFile, RepoTree, SandboxHandle, SandboxResult

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
        self._paused: set[str] = set()

    async def create_task(
        self, *, user_id: Any | None = None, session_id: str | None = None, isolate: bool = False,
        workspace_root: str | None = None,
    ) -> SandboxHandle:
        # `workspace_root` (FSA-006) is a path on the user's machine; this fake's workspace is its
        # own scratch directory, so it is accepted and ignored.
        # `user_id`/`session_id`/`isolate` accepted for `SandboxRunner` protocol conformance,
        # ignored -- this fake has no concept of per-caller routing or per-task git worktrees.
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
        candidate = (root / strip_absolute_prefix(relative)).resolve()
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

    async def write_files(self, handle: SandboxHandle, operations: Sequence[FileWriteOp]) -> None:
        """D6: pre-validate every operation, then write all of them -- mirrors
        `DockerSandboxRunner.write_files` exactly, so tests exercise the same contract."""
        plan: list[tuple[Any, str]] = []
        for op in operations:
            target = self._resolve_within(handle, op.path)
            if op.content is not None:
                plan.append((target, op.content))
            elif op.find is not None and op.replace is not None:
                if not target.is_file():
                    raise RuntimeError(f"{op.path} does not exist")
                current = target.read_text("utf-8")
                occurrences = current.count(op.find)
                if occurrences != 1:
                    raise RuntimeError(
                        f"expected exactly one occurrence of the given text in {op.path}, "
                        f"found {occurrences}"
                    )
                plan.append((target, current.replace(op.find, op.replace, 1)))
            else:
                raise RuntimeError(f"operation for {op.path} must set either content, or both find and replace")
        for target, content in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, content, "utf-8")

    async def destroy(self, handle: SandboxHandle) -> None:
        scratch = self._scratch_dirs.pop(handle.task_id, None)
        self._paused.discard(handle.task_id)
        if scratch is not None:
            await asyncio.to_thread(shutil.rmtree, scratch, True)

    # -- durable workspaces ------------------------------------------------------------------
    #
    # Real, not simulated: a workspace key maps to a real directory that really persists across
    # `open_workspace` calls, so a test that asserts "same repo, later turn, files still there"
    # is exercising the property rather than a stub agreeing with it. What is *not* modelled is
    # the container -- there is none here -- so "paused" is tracked as a flag. That is honest:
    # this fake can prove the bookkeeping, and only `tests/load/test_docker_sandbox_live.py`
    # against a real daemon can prove a paused workspace actually releases memory.

    async def open_workspace(self, key: str) -> SandboxHandle:
        handle = SandboxHandle(task_id=key)
        self._paused.discard(key)
        if key not in self._scratch_dirs:
            scratch = await asyncio.to_thread(
                tempfile.mkdtemp, prefix=f"aura-fake-workspace-{key}-"
            )
            self._scratch_dirs[key] = Path(scratch)
        return handle

    async def pause_workspace(self, handle: SandboxHandle) -> None:
        if handle.task_id in self._scratch_dirs:
            self._paused.add(handle.task_id)

    async def destroy_workspace(self, handle: SandboxHandle) -> None:
        await self.destroy(handle)

    async def workspace_keys(self) -> list[str]:
        return sorted(self._scratch_dirs)

    async def workspace_disk_usage_mb(self, handle: SandboxHandle) -> float:
        scratch = self._scratch_dirs.get(handle.task_id)
        if scratch is None:
            return 0.0

        def _size() -> float:
            total = 0
            for path in scratch.rglob("*"):
                try:
                    if path.is_file() and not path.is_symlink():
                        total += path.stat().st_size
                except OSError:
                    continue
            return total / (1024 * 1024)

        return await asyncio.to_thread(_size)

    async def workspace_path(self, handle: SandboxHandle) -> str:
        return str(self._scratch(handle))

    def is_paused(self, key: str) -> bool:
        """Test-only view of the flag above; no production caller."""
        return key in self._paused


class InMemoryGitHubClient:
    """Holds repo/branch/file/PR state in memory -- no network, real read-after-write."""

    def __init__(self) -> None:
        self._files: dict[tuple[str, str, str], str] = {}  # (repo, branch, path) -> content
        self._branches: dict[str, set[str]] = {}
        self._issues: dict[tuple[str, int], Mapping[str, Any]] = {}
        self._pulls: dict[tuple[str, int], Mapping[str, Any]] = {}
        self._next_pr_number = 1
        self._default_branches: dict[str, str] = {}

    def set_default_branch(self, repo: str, branch: str) -> None:
        self._default_branches[repo] = branch

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

    async def default_branch(self, repo: str) -> str:
        return self._default_branches.get(repo, "main")

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
