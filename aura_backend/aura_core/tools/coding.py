"""Engineer agent tools: sandboxed filesystem/command access, git, GitHub, and bridged MCP tools.

Everything here is reached only through the same planning -> execution -> ``ToolRegistry``/
``ToolPolicy`` path every other tool uses (ADR-006) -- nothing in this module dispatches a tool
itself, and nothing bypasses ``ToolPolicy.evaluate``'s capability, taint, and confirmation rules.

Two things are worth stating explicitly because they look, at a glance, like they contradict
existing decisions elsewhere in ``aura_core/tools/``:

* :func:`build_sandbox_tools`'s ``write_file``/``edit_file`` operate on a *separate, ephemeral,
  per-task sandbox root* -- the workspace a :class:`~aura_core.ports.coding.SandboxRunner`
  provisions and tears down per turn -- never the read-only workspace
  ``tools/builtin.py::build_file_tools`` guards. That function's docstring says writing is
  "deliberately not offered" for *that* root; this module does not reverse that decision, because
  it is a different root entirely, torn down when the task ends.
* Every tool below declares ``required_capability="engineer_agent"``. A caller without that grant
  never sees these tools at all (``ToolRegistry.available()`` filters them out before they reach
  a model's tool list), regardless of what sensitivity ceiling the caller otherwise has.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from ..observability.logging import get_logger
from ..ports.coding import McpToolDescriptor
from ..ports.runtime import Tool, ToolContext
from .base import FunctionTool, param, tool

__all__ = [
    "build_sandbox_tools",
    "build_github_tools",
    "build_mcp_bridge_tools",
]

logger = get_logger("tools.coding")

_ENGINEER_CAPABILITY = "engineer_agent"


# --------------------------------------------------------------------------------------
# Sandbox session management (shared by filesystem, command, and git tools)
# --------------------------------------------------------------------------------------


class _SandboxSessionCache:
    """One sandbox handle per turn, keyed by ``ToolContext.correlation_id``.

    Tools have no turn-completion hook today -- a proper fix would destroy a task's sandbox when
    :class:`~aura_core.execution.agentic_loop.AgenticExecutor` finishes handling it, but that would
    couple a generic execution loop to one tool family's resource. Instead, any handle idle longer
    than ``idle_ttl_s`` is destroyed the next time a *different* correlation id asks for one --
    bounding leaked containers without new kernel plumbing. A handle already in use for the
    current correlation id is reused, so ``write_file`` followed by ``run_command`` in the same
    turn operate on the same container.
    """

    def __init__(self, runner: Any, *, idle_ttl_s: float = 900.0) -> None:
        self._runner = runner
        self._idle_ttl_s = idle_ttl_s
        self._handles: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, correlation_id: str) -> Any:
        async with self._lock:
            now = time.monotonic()
            stale = [
                cid for cid, (_, last) in self._handles.items()
                if cid != correlation_id and now - last > self._idle_ttl_s
            ]
            for cid in stale:
                handle, _ = self._handles.pop(cid)
                try:
                    await self._runner.destroy(handle)
                except Exception:
                    logger.debug("idle sandbox cleanup failed", extra={"correlation_id": cid})

            if correlation_id in self._handles:
                handle, _ = self._handles[correlation_id]
            else:
                handle = await self._runner.create_task()
            self._handles[correlation_id] = (handle, now)
            return handle


def build_sandbox_tools(sandbox_runner: Any) -> list[Tool]:
    """Filesystem, command-execution, and git tools against one shared per-task sandbox."""
    sessions = _SandboxSessionCache(sandbox_runner)

    @tool(
        "write_file",
        "Write a UTF-8 text file inside the engineer agent's ephemeral sandbox workspace -- not "
        "the user's real files, a scratch directory torn down after the task. Overwrites the "
        "file if it already exists; creates parent directories as needed.",
        param("path", "string", "Path relative to the sandbox workspace root.", max_length=300),
        param("content", "string", "The file's full new content.", max_length=200_000),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=15.0,
    )
    async def write_file(path: str, content: str, context: ToolContext) -> str:
        handle = await sessions.get(context.correlation_id)
        await sandbox_runner.write_file(handle, path, content)
        return f"Wrote {len(content)} bytes to {path}"

    @tool(
        "edit_file",
        "Replace one exact occurrence of `find` with `replace` in a file inside the engineer "
        "agent's sandbox workspace. Fails if `find` is not present exactly once -- read the file "
        "first (e.g. via run_command 'cat path') to get the exact text.",
        param("path", "string", "Path relative to the sandbox workspace root.", max_length=300),
        param("find", "string", "Exact text to replace; must occur exactly once in the file.",
              max_length=50_000),
        param("replace", "string", "Replacement text.", max_length=50_000),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=15.0,
    )
    async def edit_file(path: str, find: str, replace: str, context: ToolContext) -> str:
        handle = await sessions.get(context.correlation_id)
        current = await sandbox_runner.read_file(handle, path)
        occurrences = current.count(find)
        if occurrences != 1:
            raise ToolExecutionError(
                f"expected exactly one occurrence of the given text in {path}, "
                f"found {occurrences}",
                tool="edit_file",
            )
        await sandbox_runner.write_file(handle, path, current.replace(find, replace, 1))
        return f"Edited {path}"

    @tool(
        "run_command",
        "Run a shell command inside the engineer agent's ephemeral, network-restricted sandbox "
        "container and return its stdout/stderr/exit code. Use for running tests, linters, "
        "builds, or inspecting the sandbox filesystem -- the sandbox cannot reach the user's real "
        "network or the host.",
        param("command", "string", "The shell command to run, e.g. 'pytest -q'.", max_length=2000),
        param("timeout_s", "number", "Max seconds to allow the command to run.", required=False,
              minimum=1, maximum=600, default=60),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.SYSTEM,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=605.0,
        max_result_chars=20_000,
        # Command output can echo file contents, comments, or anything else in the sandbox
        # workspace -- attacker-influenceable exactly like fetch_url's page content.
        tags=("untrusted_content",),
    )
    async def run_command(command: str, context: ToolContext, timeout_s: float = 60.0) -> str:
        handle = await sessions.get(context.correlation_id)
        result = await sandbox_runner.run(handle, ["sh", "-c", command], timeout_s=timeout_s)
        return (
            f"exit_code={result.exit_code}\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

    @tool(
        "git_diff",
        "Show the sandbox workspace's current git diff (unstaged and staged changes).",
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
        max_result_chars=20_000,
    )
    async def git_diff(context: ToolContext) -> str:
        handle = await sessions.get(context.correlation_id)
        result = await sandbox_runner.run(handle, ["git", "diff", "HEAD"], timeout_s=15.0)
        return result.stdout or "(no changes)"

    @tool(
        "git_commit",
        "Stage all changes in the sandbox workspace and commit them with the given message.",
        param("message", "string", "Commit message.", max_length=2000),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
    )
    async def git_commit(message: str, context: ToolContext) -> str:
        handle = await sessions.get(context.correlation_id)
        await sandbox_runner.run(handle, ["git", "add", "-A"], timeout_s=15.0)
        result = await sandbox_runner.run(handle, ["git", "commit", "-m", message], timeout_s=15.0)
        if result.exit_code != 0:
            raise ToolExecutionError(f"git commit failed: {result.stderr[:300]}", tool="git_commit")
        return result.stdout or "committed"

    @tool(
        "git_checkout_branch",
        "Create (or switch to) a git branch in the sandbox workspace.",
        param("branch", "string", "Branch name.", max_length=200),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=15.0,
    )
    async def git_checkout_branch(branch: str, context: ToolContext) -> str:
        handle = await sessions.get(context.correlation_id)
        result = await sandbox_runner.run(handle, ["git", "checkout", "-B", branch], timeout_s=10.0)
        if result.exit_code != 0:
            raise ToolExecutionError(
                f"git checkout -B {branch} failed: {result.stderr[:300]}",
                tool="git_checkout_branch",
            )
        return result.stdout or f"switched to {branch}"

    return [write_file, edit_file, run_command, git_diff, git_commit, git_checkout_branch]


# --------------------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------------------


def build_github_tools(client_resolver: Any, *, default_repo: str = "") -> list[Tool]:
    """Read/write GitHub tools. Every write is ``side_effecting`` at ``CONFIDENTIAL``
    sensitivity, which ``ToolSpec.__post_init__`` already forces into
    ``requires_confirmation`` -- ``create_draft_pull_request`` always opens as a draft, so a
    confirmed call still leaves a human to mark it ready.

    ``client_resolver`` is ``Container._build_github_client_resolver``'s callable
    ``(UserId) -> Awaitable[GitHubClient | None]``, called with ``context.user_id`` on every
    invocation rather than closing over one fixed client -- whether that resolves to a
    deployment-wide admin token or the calling user's own OAuth-connected identity is a
    container-wiring decision these tools don't need to know about. ``None`` means the caller has
    not connected GitHub (or the admin token disappeared mid-process); every tool below surfaces
    that as a clear, actionable failure rather than a generic API error.
    """

    async def _client(context: ToolContext) -> Any:
        client = await client_resolver(context.user_id)
        if client is None:
            raise ToolExecutionError(
                "GitHub is not connected for this user -- connect an account via "
                "/api/v3/integrations/github/connect, or ask an operator to configure "
                "AURA_GITHUB_TOKEN, before using GitHub tools",
                tool="github",
            )
        return client

    def _repo(explicit: str | None) -> str:
        repo = explicit or default_repo
        if not repo:
            raise ToolExecutionError(
                "no repo given and no default configured "
                "(AURA_GITHUB_DEFAULT_OWNER/AURA_GITHUB_DEFAULT_REPO)",
                tool="github",
            )
        return repo

    _repo_param = param(
        "repo", "string", "Repository as 'owner/name'. Omit to use the configured default.",
        required=False, max_length=200,
    )

    @tool(
        "github_get_file_contents",
        "Read a file's contents from a GitHub repository.",
        param("path", "string", "File path within the repo.", max_length=500),
        _repo_param,
        param("ref", "string", "Branch, tag, or commit SHA. Omit for the default branch.",
              required=False, max_length=200),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
        max_result_chars=40_000,
        tags=("untrusted_content",),
    )
    async def github_get_file_contents(
        path: str, context: ToolContext, repo: str | None = None, ref: str | None = None
    ) -> str:
        client = await _client(context)
        return await client.get_file_contents(_repo(repo), path, ref=ref)

    @tool(
        "github_get_issue",
        "Read a GitHub issue's title, state, and description.",
        param("number", "integer", "Issue number.", minimum=1),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
        max_result_chars=10_000,
        tags=("untrusted_content",),
    )
    async def github_get_issue(number: int, context: ToolContext, repo: str | None = None) -> str:
        client = await _client(context)
        return await client.get_issue(_repo(repo), int(number))

    @tool(
        "github_get_pull_request",
        "Read a GitHub pull request's title, state, branches, and description.",
        param("number", "integer", "Pull request number.", minimum=1),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
        max_result_chars=10_000,
        tags=("untrusted_content",),
    )
    async def github_get_pull_request(
        number: int, context: ToolContext, repo: str | None = None
    ) -> str:
        client = await _client(context)
        return await client.get_pull_request(_repo(repo), int(number))

    @tool(
        "github_create_branch",
        "Create a new branch in a GitHub repository from an existing ref.",
        param("branch", "string", "New branch name.", max_length=200),
        param("from_ref", "string", "Existing branch to branch from, e.g. 'main'.", max_length=200),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
    )
    async def github_create_branch(
        branch: str, from_ref: str, context: ToolContext, repo: str | None = None
    ) -> str:
        client = await _client(context)
        return await client.create_branch(_repo(repo), branch=branch, from_ref=from_ref)

    @tool(
        "github_commit_files",
        "Commit one or more file changes directly to a branch in a GitHub repository.",
        param("branch", "string", "Branch to commit to.", max_length=200),
        param("message", "string", "Commit message.", max_length=2000),
        param("files", "object", "Mapping of file path to its full new text content."),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=60.0,
    )
    async def github_commit_files(
        branch: str, message: str, files: Mapping[str, str], context: ToolContext,
        repo: str | None = None,
    ) -> str:
        client = await _client(context)
        return await client.commit_files(
            _repo(repo), branch=branch, message=message, files=files
        )

    @tool(
        "github_create_draft_pull_request",
        "Open a draft pull request in a GitHub repository. Always opens as a draft, never "
        "ready-for-review -- a human decides when it is ready to merge.",
        param("branch", "string", "Head branch (the branch with the changes).", max_length=200),
        param("base", "string", "Base branch to merge into, e.g. 'main'.", max_length=200),
        param("title", "string", "Pull request title.", max_length=300),
        param("body", "string", "Pull request description.", max_length=10_000),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
    )
    async def github_create_draft_pull_request(
        branch: str, base: str, title: str, body: str, context: ToolContext,
        repo: str | None = None,
    ) -> str:
        client = await _client(context)
        return await client.create_draft_pull_request(
            _repo(repo), branch=branch, base=base, title=title, body=body
        )

    return [
        github_get_file_contents, github_get_issue, github_get_pull_request,
        github_create_branch, github_commit_files, github_create_draft_pull_request,
    ]


# --------------------------------------------------------------------------------------
# MCP bridging
# --------------------------------------------------------------------------------------

_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def _sanitize_name_part(value: str) -> str:
    """Tool names are pinned to lowercase snake_case (``ToolSpec._NAME_OK``); an MCP server or
    tool name is arbitrary text an operator configured, not guaranteed to already conform."""
    lowered = value.strip().lower()
    return "".join(c if c in _NAME_OK else "_" for c in lowered) or "tool"


def _params_from_json_schema(schema: Mapping[str, Any]) -> list[ParameterSpec]:
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    supported = {"string", "number", "integer", "boolean", "array", "object"}
    params: list[ParameterSpec] = []
    for name, raw_prop in properties.items():
        prop = raw_prop if isinstance(raw_prop, Mapping) else {}
        json_type = prop.get("type") if prop.get("type") in supported else "string"
        params.append(
            param(
                name,
                json_type,
                str(prop.get("description") or name),
                required=name in required,
                item_type="string" if json_type == "array" else None,
            )
        )
    return params


async def build_mcp_bridge_tools(
    mcp_client: Any, *, safe_tools: Sequence[str] = ()
) -> list[Tool]:
    """Discover every tool every configured MCP server advertises and wrap each as a
    :class:`~aura_core.tools.base.FunctionTool`.

    Default-deny: a discovered tool is ``sensitivity=CONFIDENTIAL, side_effecting=True`` (so
    ``ToolPolicy`` always requires confirmation) unless its fully qualified name
    (``mcp_{server}_{tool}``) is explicitly listed in ``safe_tools`` (``AURA_MCP_SAFE_TOOLS``),
    in which case it registers as ``PERSONAL``/non-side-effecting instead. An MCP server an
    operator has not individually reviewed is therefore inert-but-confirmable, never silently
    trusted, regardless of what the server itself claims about its own tools.
    """
    safe = frozenset(safe_tools)
    descriptors: Sequence[McpToolDescriptor] = await mcp_client.list_tools()
    tools: list[Tool] = []
    for descriptor in descriptors:
        qualified_name = (
            f"mcp_{_sanitize_name_part(descriptor.server)}_{_sanitize_name_part(descriptor.name)}"
        )
        is_safe = qualified_name in safe
        spec = ToolSpec(
            name=qualified_name,
            description=f"[MCP:{descriptor.server}] {descriptor.description}"[:2000] or (
                f"MCP tool {descriptor.name!r} from server {descriptor.server!r}."
            ),
            parameters=tuple(_params_from_json_schema(descriptor.input_schema)),
            category=ToolCategory.DEVELOPMENT,
            sensitivity=ToolSensitivity.PERSONAL if is_safe else ToolSensitivity.CONFIDENTIAL,
            side_effecting=not is_safe,
            required_capability=_ENGINEER_CAPABILITY,
            timeout_s=30.0,
            max_result_chars=20_000,
            tags=() if is_safe else ("untrusted_content",),
        )

        def _make_handler(server: str, name: str) -> Any:
            async def handler(context: ToolContext, **kwargs: Any) -> str:
                return await mcp_client.call_tool(server, name, kwargs)

            return handler

        tools.append(FunctionTool(spec, _make_handler(descriptor.server, descriptor.name)))
    return tools
