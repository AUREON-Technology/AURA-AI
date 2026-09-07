"""Ports for the engineer agent's adapters: MCP client, code-hosting client, sandbox runner.

Stdlib only, like every other file in ``ports/`` (ADR-001) -- concrete adapters (the ``mcp``
SDK, ``httpx``, ``docker``) are wired in ``container.py`` and imported lazily inside the adapter
modules themselves (``aura_core/adapters/{mcp_client,github_client,docker_sandbox}.py``), never
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.engineering import EngineerRun
from ..domain.identifiers import UserId

__all__ = [
    "McpToolDescriptor",
    "McpClient",
    "RepoFile",
    "RepoTree",
    "CodeHostClient",
    "SandboxHandle",
    "SandboxResult",
    "SandboxRunner",
    "EngineerRunStore",
]


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """One tool as advertised by an MCP server, before it is bridged into the tool registry."""

    server: str
    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class McpClient(Protocol):
    """Discovers and invokes tools exposed by configured MCP servers."""

    async def list_tools(self) -> Sequence[McpToolDescriptor]:
        """Every tool every configured server currently advertises."""
        ...

    async def call_tool(self, server: str, name: str, arguments: Mapping[str, Any]) -> str:
        """Invoke one tool and return its textual result."""
        ...


@dataclass(frozen=True, slots=True)
class RepoFile:
    """One file (blob) in a repository at a given ref. ``size`` is in bytes, 0 if unreported."""

    path: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class RepoTree:
    """A repository's file listing at one ref.

    ``truncated`` is not cosmetic: a forge that caps how much of a tree it will return in one
    response (GitHub does, at ~100k entries) hands back a *partial* listing with no error, and an
    agent that treats a partial listing as complete will confidently report that a file does not
    exist. Carrying the flag lets the tool layer say so out loud.
    """

    files: tuple[RepoFile, ...] = ()
    truncated: bool = False


@runtime_checkable
class CodeHostClient(Protocol):
    """Read/write access to a git forge.

    Named for the capability, not the vendor -- the one implementation today
    (``adapters/github_client.py``) talks to GitHub's REST API, but nothing above this port
    should need to know that.
    """

    async def get_file_contents(self, repo: str, path: str, *, ref: str | None = None) -> str:
        ...

    async def list_files(self, repo: str, *, ref: str | None = None) -> RepoTree:
        """Every file in the repository at ``ref`` (the default branch when ``None``).

        Directories are not returned -- a caller wanting the shape of the tree derives it from
        the paths. This is what makes "explore/analyse this repository" answerable at all:
        :meth:`get_file_contents` needs a path the caller already knows, which an agent handed
        only a repository URL does not have.
        """
        ...

    async def get_issue(self, repo: str, number: int) -> str:
        ...

    async def get_pull_request(self, repo: str, number: int) -> str:
        ...

    async def create_branch(self, repo: str, *, branch: str, from_ref: str) -> str:
        ...

    async def commit_files(
        self, repo: str, *, branch: str, message: str, files: Mapping[str, str]
    ) -> str:
        ...

    async def create_draft_pull_request(
        self, repo: str, *, branch: str, base: str, title: str, body: str
    ) -> str:
        """Always opens the PR as a draft -- never ready-for-review. Callers that want a
        non-draft PR go through a human, not this port."""
        ...


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """Opaque identity of one ephemeral per-task sandbox."""

    task_id: str


@dataclass(frozen=True, slots=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int


@runtime_checkable
class SandboxRunner(Protocol):
    """An isolated, ephemeral execution environment, one instance per agent task.

    A handle's lifetime is caller-managed: :meth:`create_task` before the first use,
    :meth:`destroy` when the task ends (success, failure, or turn abandonment) -- there is no
    implicit cleanup, because a runner that reaps sandboxes on a timer cannot tell a slow legitimate
    task from an abandoned one.
    """

    async def create_task(self) -> SandboxHandle:
        ...

    async def run(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> SandboxResult:
        ...

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        ...

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        ...

    async def destroy(self, handle: SandboxHandle) -> None:
        ...


@runtime_checkable
class EngineerRunStore(Protocol):
    """Persistence for :class:`~aura_core.domain.engineering.EngineerRun` rows.

    Read-only from the Flutter client's perspective (it pulls via the generic sync cursor, never
    pushes) -- this port has no ``update``/``delete``; :meth:`record` always inserts a new row.
    """

    async def record(self, run: EngineerRun) -> None:
        ...

    async def list_for_user(
        self, user_id: UserId, *, since: datetime | None = None, limit: int = 200,
    ) -> Sequence[EngineerRun]:
        ...
