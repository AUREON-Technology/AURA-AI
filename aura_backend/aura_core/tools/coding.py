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
* :func:`build_sandbox_tools`'s ``clone_repository`` does not shell out to ``git clone``: it
  copies files through the code-host port into the sandbox. That keeps network egress and a
  forge credential out of a container that runs model-chosen shell commands, and it is why
  "analyse this repository" works on a deployment whose sandbox has no network at all.
"""

from __future__ import annotations

import asyncio
import re
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
    "REPOSITORY_TOOL_NAMES",
    "matching_repository_tool",
]

logger = get_logger("tools.coding")

_ENGINEER_CAPABILITY = "engineer_agent"

#: Caps for `clone_repository`. A repository is arbitrary third-party data of unbounded size, and
#: the sandbox workspace is a scratch directory inside a memory-limited container -- copying a
#: multi-gigabyte monorepo into it would fail the turn slowly instead of quickly. These bound the
#: work and are reported in the tool's result, so the agent knows the snapshot is partial rather
#: than concluding a skipped file does not exist.
_CLONE_MAX_FILES = 400
_CLONE_MAX_FILE_BYTES = 256_000
_CLONE_MAX_TOTAL_BYTES = 8_000_000
_CLONE_CONCURRENCY = 6

#: Paths per `github_list_repository_files` response. A large repository's full tree is tens of
#: thousands of paths -- more context than the listing is worth, and `max_result_chars` would
#: truncate it mid-path anyway; the tool says how many it withheld and how to narrow the listing.
_LIST_FILES_MAX_ENTRIES = 1_000

#: Extensions whose contents are not text and so cannot survive `SandboxRunner.write_file`'s
#: UTF-8 contract. Skipped by name rather than by sniffing bytes: the code-host port hands back
#: `str`, so by the time a binary blob reached here it would already have been lossily decoded.
_BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".icns", ".svgz",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg", ".webm", ".flac",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class", ".pyc", ".wasm",
    ".db", ".sqlite", ".sqlite3", ".keystore", ".jks", ".p12", ".pack", ".idx",
})


def _is_binary_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in _BINARY_SUFFIXES


def _normalize_repo(value: str) -> str:
    """``owner/name`` from whatever form the user pasted.

    A user asking an agent to look at a repository pastes a URL
    (``https://github.com/owner/name``, with or without ``.git``, ``/tree/main``, or a trailing
    slash), and the model passes that string straight through as the ``repo`` argument. Every
    call below wants the ``owner/name`` slug the API is addressed by, so normalise once here
    rather than making each tool -- or the model -- get it right.
    """
    repo = value.strip()
    for prefix in ("https://", "http://", "ssh://", "git@", "www."):
        if repo.startswith(prefix):
            repo = repo[len(prefix):]
    repo = repo.replace("github.com:", "github.com/")
    if repo.startswith("github.com/"):
        repo = repo[len("github.com/"):]
    repo = repo.strip("/")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    parts = [p for p in repo.split("/") if p]
    # `owner/name/tree/main/subdir` and friends: the slug is the first two segments.
    return "/".join(parts[:2])


#: The tools that can answer "look at this repository", best first. `clone_repository` gives the
#: fullest answer (the files land in the sandbox, so tests and grep work), but it needs a sandbox;
#: the GitHub reads need nothing but a connected account.
_REPOSITORY_TOOLS_BY_PREFERENCE = (
    "clone_repository",
    "github_list_repository_files",
    "github_get_file_contents",
)
REPOSITORY_TOOL_NAMES = frozenset(_REPOSITORY_TOOLS_BY_PREFERENCE)

_REPO_REFERENCE = re.compile(
    r"https?://\S+|git@\S+|\b(?:github|gitlab|bitbucket)\.(?:com|org)\b|\b[\w.-]+/[\w.-]+\.git\b",
    re.I,
)
#: Verb stems, not exact forms: `\w*` after the alternation is deliberate. The original pattern
#: matched each verb's dictionary form only (`\breview\b`, `\bclone\b`), so `\b` immediately
#: after the stem required a non-word character there -- which "reviewing", "cloned", and
#: "analyzing" never have (the suffix is a word character too). That silently missed every
#: inflected phrasing of a repository request and reproduced this module's own reason for
#: existing, just for a different verb tense.
_REPO_ACTION = re.compile(
    r"\b(?:clon|fork|check(?:ing)? ?out|pull|inspect|explor|analy[sz]|review|audit|"
    r"summari[sz]|read|look(?:ing)? at|go(?:ing)? through|walk(?:ing)? through)\w*\b",
    re.I,
)


def matching_repository_tool(message: str, available_tools: Sequence[str]) -> str | None:
    """The repository tool a "clone/analyse this repo" request should reach, or ``None``.

    The same shape and the same job as ``tools/personal_os.py``'s
    :func:`matching_personal_os_tool`: :meth:`PlanningEngine.needs_plan` skips planning for
    anything its prose heuristics do not recognise as multi-step, and without planning nothing
    ever runs a tool (ADR-006) -- so an unrecognised request is answered by the model alone,
    which can only say it is unable to reach a repository. "clone <url> and analyse it" is
    exactly that case: the verb precedes the conjunction, so the multi-step markers miss it.

    Returns ``None`` unless a tool that could actually satisfy the request is being offered,
    so a caller without the engineer grant does not pay for a planning generation that could
    only end in a plain answer.
    """
    offered = [name for name in _REPOSITORY_TOOLS_BY_PREFERENCE if name in set(available_tools)]
    if not offered:
        return None
    text = message.strip()
    if not text:
        return None
    if _REPO_REFERENCE.search(text) and _REPO_ACTION.search(text):
        return offered[0]
    return None


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


def build_sandbox_tools(sandbox_runner: Any, *, code_host_resolver: Any = None) -> list[Tool]:
    """Filesystem, command-execution, and git tools against one shared per-task sandbox.

    ``code_host_resolver`` -- the same ``(UserId) -> Awaitable[CodeHostClient | None]`` callable
    :func:`build_github_tools` takes -- additionally wires ``clone_repository``. It is built here,
    next to the sandbox tools rather than with the GitHub ones, because it must write into the
    *same* per-turn sandbox handle ``run_command`` will then read from: a clone that landed in a
    different container would be invisible to the very next tool call.
    """
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

    tools: list[Tool] = [
        write_file, edit_file, run_command, git_diff, git_commit, git_checkout_branch,
    ]
    if code_host_resolver is None:
        return tools

    @tool(
        "clone_repository",
        "Copy a GitHub repository's files into the engineer agent's sandbox workspace, so they "
        "can be explored, searched, built, or tested with run_command. Use this when asked to "
        "clone, analyse, review, or work on a repository. Text files only, up to a size and "
        "count budget -- binaries and oversized files are skipped and the skips are reported.",
        param("repo", "string",
              "Repository to copy, as 'owner/name' or its GitHub URL.", max_length=400),
        param("ref", "string", "Branch, tag, or commit SHA. Omit for the default branch.",
              required=False, max_length=200),
        param("destination", "string",
              "Workspace subdirectory to copy into. Defaults to the repository's name.",
              required=False, max_length=200),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=300.0,
        max_result_chars=20_000,
        # Everything this pulls in is third-party repository content -- exactly as
        # attacker-influenceable as fetch_url's page bodies or run_command's output.
        tags=("untrusted_content",),
    )
    async def clone_repository(
        repo: str, context: ToolContext, ref: str | None = None, destination: str | None = None,
    ) -> str:
        """Not `git clone`: the files are fetched through the code-host port and written into the
        sandbox one by one.

        A real clone would need the sandbox container to reach the network *and* to hold a
        credential for private repositories -- an egress hole and a secret in a container running
        model-chosen shell commands. Copying through the already-authenticated, already
        repo-allowlisted client keeps both out of the sandbox, and works unchanged on a
        deployment whose sandbox network is disabled entirely (`AURA_SANDBOX_NETWORK` unset).
        """
        slug = _normalize_repo(repo)
        if slug.count("/") != 1 or not all(slug.split("/")):
            raise ToolExecutionError(
                f"could not read a repository from {repo!r}; expected 'owner/name' or a "
                "GitHub URL like https://github.com/owner/name",
                tool="clone_repository",
            )
        client = await code_host_resolver(context.user_id)
        if client is None:
            raise ToolExecutionError(
                "GitHub is not connected for this user -- connect an account via "
                "/api/v3/integrations/github/connect, or ask an operator to configure "
                "AURA_GITHUB_TOKEN, before cloning a repository",
                tool="clone_repository",
            )
        try:
            tree = await client.list_files(slug, ref=ref)
        except Exception as exc:
            raise ToolExecutionError(
                f"could not list {slug}{'@' + ref if ref else ''}: {exc}",
                tool="clone_repository",
            ) from exc
        if not tree.files:
            raise ToolExecutionError(
                f"{slug}{'@' + ref if ref else ''} has no files to copy (empty repository, "
                "wrong ref, or no access to it)",
                tool="clone_repository",
            )

        root = (destination or slug.split("/")[1]).strip("/") or "repo"
        selected: list[Any] = []
        skipped_binary = skipped_large = skipped_budget = 0
        total_bytes = 0
        for entry in tree.files:
            if _is_binary_path(entry.path):
                skipped_binary += 1
            elif entry.size > _CLONE_MAX_FILE_BYTES:
                skipped_large += 1
            elif (
                len(selected) >= _CLONE_MAX_FILES
                or total_bytes + entry.size > _CLONE_MAX_TOTAL_BYTES
            ):
                skipped_budget += 1
            else:
                selected.append(entry)
                total_bytes += entry.size

        handle = await sessions.get(context.correlation_id)
        semaphore = asyncio.Semaphore(_CLONE_CONCURRENCY)
        failed: list[str] = []

        async def _copy(entry: Any) -> None:
            async with semaphore:
                try:
                    content = await client.get_file_contents(slug, entry.path, ref=ref)
                    await sandbox_runner.write_file(handle, f"{root}/{entry.path}", content)
                except Exception as exc:  # one unreadable file must not lose the whole clone
                    failed.append(f"{entry.path}: {exc}"[:200])

        await asyncio.gather(*(_copy(entry) for entry in selected))

        copied = len(selected) - len(failed)
        lines = [
            f"Copied {copied} file(s) from {slug}{'@' + ref if ref else ''} into "
            f"./{root} in the sandbox workspace.",
            "Explore it with run_command (e.g. 'ls -R {0}', 'cat {0}/README.md').".format(root),
        ]
        if skipped_binary or skipped_large or skipped_budget or failed or tree.truncated:
            lines.append(
                "Snapshot is partial -- do not conclude a file is absent from the repository "
                "because it is absent here:"
            )
        if skipped_binary:
            lines.append(f"- {skipped_binary} binary file(s) skipped")
        if skipped_large:
            lines.append(
                f"- {skipped_large} file(s) over {_CLONE_MAX_FILE_BYTES // 1000}kB skipped"
            )
        if skipped_budget:
            lines.append(
                f"- {skipped_budget} file(s) skipped after the {_CLONE_MAX_FILES}-file / "
                f"{_CLONE_MAX_TOTAL_BYTES // 1_000_000}MB budget was reached"
            )
        if failed:
            lines.append(f"- {len(failed)} file(s) failed to copy: {'; '.join(failed[:5])}")
        if tree.truncated:
            lines.append("- the repository's file listing was truncated by GitHub")
        return "\n".join(lines)

    tools.append(clone_repository)
    return tools


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
        repo = _normalize_repo(explicit or default_repo)
        if not repo:
            raise ToolExecutionError(
                "no repo given and no default configured "
                "(AURA_GITHUB_DEFAULT_OWNER/AURA_GITHUB_DEFAULT_REPO)",
                tool="github",
            )
        if repo.count("/") != 1:
            raise ToolExecutionError(
                f"could not read a repository from {explicit!r}; expected 'owner/name' or a "
                "GitHub URL like https://github.com/owner/name",
                tool="github",
            )
        return repo

    _repo_param = param(
        "repo", "string",
        "Repository as 'owner/name' or its GitHub URL. Omit to use the configured default.",
        required=False, max_length=400,
    )

    @tool(
        "github_list_repository_files",
        "List the file paths in a GitHub repository. Use this first when asked to explore, "
        "analyse, or review a repository -- it is what turns a bare repository URL into paths "
        "github_get_file_contents can then read. Works without a sandbox.",
        _repo_param,
        param("ref", "string", "Branch, tag, or commit SHA. Omit for the default branch.",
              required=False, max_length=200),
        param("path_prefix", "string",
              "Only list files under this directory, e.g. 'src/'. Omit for the whole repository.",
              required=False, max_length=300),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
        max_result_chars=40_000,
        # Path names are repository-controlled text, same as file contents.
        tags=("untrusted_content",),
    )
    async def github_list_repository_files(
        context: ToolContext, repo: str | None = None, ref: str | None = None,
        path_prefix: str | None = None,
    ) -> str:
        client = await _client(context)
        tree = await client.list_files(_repo(repo), ref=ref)
        prefix = (path_prefix or "").lstrip("/")
        files = [f for f in tree.files if not prefix or f.path.startswith(prefix)]
        if not files:
            return (
                f"No files{' under ' + prefix if prefix else ''} in {_repo(repo)}"
                f"{'@' + ref if ref else ''}."
            )
        shown = files[:_LIST_FILES_MAX_ENTRIES]
        lines = [f"{f.path} ({f.size} bytes)" for f in shown]
        if len(files) > len(shown):
            lines.append(
                f"... and {len(files) - len(shown)} more file(s) not shown; narrow with "
                "path_prefix"
            )
        if tree.truncated:
            lines.append("(GitHub truncated this repository's file listing -- it is incomplete)")
        return "\n".join(lines)

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
        github_list_repository_files, github_get_file_contents, github_get_issue,
        github_get_pull_request, github_create_branch, github_commit_files,
        github_create_draft_pull_request,
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
