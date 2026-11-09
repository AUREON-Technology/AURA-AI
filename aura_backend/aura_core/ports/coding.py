"""Ports for the engineer agent's adapters: MCP client, code-hosting client, sandbox runner.

Stdlib only, like every other file in ``ports/`` (ADR-001) -- concrete adapters (the ``mcp``
SDK, ``httpx``, ``docker``) are wired in ``container.py`` and imported lazily inside the adapter
modules themselves (``aura_core/adapters/{mcp_client,github_client,docker_sandbox}.py``), never
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.agent_runs import AgentRun, AgentStep
from ..domain.engineering import AgentWorkspace, EngineerRun
from ..domain.identifiers import AgentRunId, UserId, WorkspaceId

__all__ = [
    "McpToolDescriptor",
    "McpClient",
    "RepoFile",
    "RepoTree",
    "CodeHostClient",
    "SandboxHandle",
    "SandboxResult",
    "FileWriteOp",
    "SandboxRunner",
    "SandboxOutputEvent",
    "StreamingSandboxRunner",
    "EngineerRunStore",
    "AgentRunStore",
    "WorkspaceStore",
]


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """One tool as advertised by an MCP server, before it is bridged into the tool registry."""

    server: str
    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    #: The server's ``outputSchema``, when it advertises one (ADR-025).
    output_schema: Mapping[str, Any] | None = None


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


@dataclass(frozen=True, slots=True)
class FileWriteOp:
    """One file operation in an atomic :meth:`SandboxRunner.write_files` batch (D6 --
    ``docs/architecture/015-desktop-ide.md``): either a full overwrite (``content`` set) or a
    find/replace edit (``find``/``replace`` set), mirroring ``write_file``/``edit_file``'s two
    existing single-file tool shapes. A given instance uses exactly one shape -- constructing one
    with both, or neither, is a caller bug, checked by the runner at validation time (see
    :meth:`SandboxRunner.write_files`'s docstring), not enforced by the type itself.
    """

    path: str
    content: str | None = None
    find: str | None = None
    replace: str | None = None


@runtime_checkable
class SandboxRunner(Protocol):
    """An isolated execution environment for agent work.

    Two lifetimes, deliberately kept apart rather than unified:

    *Ephemeral* (:meth:`create_task` / :meth:`destroy`) is the original per-task shape. The
    handle's lifetime is caller-managed and there is no implicit cleanup, because a runner that
    reaps on a timer cannot tell a slow legitimate task from an abandoned one. The one exception
    is a runner that routes by user (the desktop host-agent bridge, ``adapters/host_agent.py``):
    there, a "task" is scoped to the user's own open workspace rather than one chat turn, and
    :meth:`destroy` is deliberately a no-op -- documented on that adapter, not here, since it does
    not change this protocol's contract for every other implementation.

    ``user_id`` on :meth:`create_task` is ``None`` for every runner that does not need it (the
    server-side Docker sandbox, the in-memory test fake) -- it exists only so a runner that must
    route per-caller (again, the host-agent bridge) can be handed the caller's identity without
    every other implementation having to know why.

    *Durable* (:meth:`open_workspace` / :meth:`pause_workspace` / :meth:`destroy_workspace`) is
    keyed by a caller-supplied, stable ``key`` instead of a fresh id, so the same key resumes the
    same filesystem across turns, restarts of this process, and days. What makes that affordable
    is :meth:`pause_workspace`: the content of a workspace is its checkout on disk, not a running
    process, so between turns the execution environment is stopped and costs no memory at all.
    Peak memory is therefore bounded by how many are *running*, never by how many exist.

    The reason a timer-based reaper is acceptable for the durable half when it was not for tasks
    is that "abandoned" is now observable: a durable workspace records when it was last used, so a
    reaper can distinguish idle-for-days from busy-right-now. Whoever holds that record (see
    :class:`WorkspaceStore`) decides; this port only carries it out.

    The two are complementary rather than competing. The desktop bridge routes a task to the
    user's own machine; the durable half keeps a server-side checkout alive between turns. A
    runner implements whichever half it can serve.
    """

    async def create_task(
        self,
        *,
        user_id: UserId | None = None,
        session_id: str | None = None,
        isolate: bool = False,
        workspace_root: str | None = None,
    ) -> SandboxHandle:
        """``workspace_root`` is the Desktop IDE's active workspace root (audit FSA-006), in the
        *user's machine's* path syntax, and binds the task to that root for every call made on the
        handle -- run, write, read, git -- so a user with several granted roots gets the Engineer
        in the one the IDE shows, not in whichever root was granted first. The host-agent bridge
        sends it to the daemon, which binds the task only if the path lies inside a granted root
        and refuses otherwise; every other runner accepts and ignores it, because a path on the
        user's machine means nothing to a server-side checkout. Callers pass it only when they
        have one, so a runner written before this parameter existed keeps working unchanged.

        ``session_id``/``isolate`` are D8's per-task isolated-workspace request (Desktop IDE
        only): ``isolate=True`` asks the host-agent bridge's daemon to carve out a dedicated git
        worktree for this task rather than writing into the user's live checkout, keyed by
        ``session_id`` (stable across a chat session's many turns, unlike the fresh ``task_id``
        this method mints on every call) so a multi-turn conversation keeps landing in the same
        worktree. Every runner that doesn't implement isolation (the server-side Docker sandbox,
        the in-memory test fake) accepts and ignores both -- they have their own, unrelated
        durable-workspace story (``open_workspace``/``pause_workspace``/``destroy_workspace``
        above), not per-task git worktrees. Defaults keep every existing caller's behavior
        unchanged: only a caller that explicitly asks for isolation gets it.
        """
        ...

    async def run(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> SandboxResult:
        ...

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        ...

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        ...

    async def write_files(self, handle: SandboxHandle, operations: Sequence[FileWriteOp]) -> None:
        """Atomically write or find/replace-edit multiple files (D6, ``tools/coding.py::write_files``).

        "Atomic" means pre-validate-all-then-write-all: every operation's path must resolve within
        the sandbox and, for a find/replace operation, its ``find`` text must occur in the current
        file exactly once -- all checked *before* any file is touched. If any operation fails
        validation, the whole call raises and nothing in the batch is written. This is what makes
        the guarantee real for a runner whose writes cross a wire (the desktop host-agent bridge):
        looping individual `write_file`/`edit_file` calls could not make this promise, since
        nothing would stop a crash or a concurrent write from landing between two of them.

        Implementations should raise :class:`~aura_core.domain.errors.ToolExecutionError` (or let
        the tool layer wrap whatever they raise) naming the first failing operation, exactly like
        :meth:`write_file`'s existing failure contract.
        """
        ...

    async def destroy(self, handle: SandboxHandle) -> None:
        ...

    async def open_workspace(self, key: str) -> SandboxHandle:
        """Resume, or first create, the durable workspace named ``key``.

        Idempotent, and must remain so across a restart of this process: a runner that has
        forgotten its in-memory bookkeeping still has to find the existing environment rather
        than silently start a second one over the same files.
        """
        ...

    async def pause_workspace(self, handle: SandboxHandle) -> None:
        """Stop the execution environment, keeping the workspace's files.

        The memory story rests entirely on this being a real stop rather than an idle process:
        a paused workspace must hold no RAM.
        """
        ...

    async def destroy_workspace(self, handle: SandboxHandle) -> None:
        """Remove the environment *and* its files. What the reaper calls at expiry."""
        ...

    async def workspace_keys(self) -> Sequence[str]:
        """Every durable workspace key this runner can currently see.

        Read from the substrate rather than from memory, so it stays true after a restart. This
        is the half of reconciliation that finds environments no record knows about; the other
        half (records whose environment is gone) comes from the store.
        """
        ...

    async def workspace_disk_usage_mb(self, handle: SandboxHandle) -> float:
        """Size of the workspace's files, in MiB. Bounds a checkout that grows without limit."""
        ...

    async def workspace_path(self, handle: SandboxHandle) -> str:
        """Where this workspace's files live *on this host*.

        Asked of the runner rather than derived by the caller, because only the runner knows: a
        deployed runner roots workspaces under a configured host directory, a local one uses a
        temp directory, and a caller that recomputed the path would be right in one case and
        silently wrong in the other. Host-side git (``adapters/host_git.py``) needs the real one.
        """
        ...


@dataclass(frozen=True, slots=True)
class SandboxOutputEvent:
    """One increment of a streamed command's output, or its terminal outcome.

    ``kind`` is ``"stdout"``/``"stderr"`` while the command is still running, in which case
    ``data`` carries the chunk of output produced since the previous event -- or ``"done"``
    exactly once, as the final event in the sequence, in which case ``exit_code`` carries the
    command's exit status and ``data`` is unused. A well-behaved :class:`StreamingSandboxRunner`
    implementation always terminates its sequence with exactly one ``"done"`` event, even on a
    daemon-side timeout or error (mapped to a non-zero synthetic exit code) -- callers rely on
    seeing ``"done"`` to know the command has actually finished, not just that the connection
    went quiet.
    """

    kind: str
    data: str = ""
    exit_code: int = 0


@runtime_checkable
class StreamingSandboxRunner(Protocol):
    """Optional capability: a :class:`SandboxRunner` that can report a command's output as it is
    produced, not only once the command finishes.

    Purely additive over :class:`SandboxRunner` -- a runner implements this *in addition to*
    :meth:`SandboxRunner.run`, never instead of it, so every existing runner that does not
    implement it keeps working exactly as before. Callers check via ``isinstance`` (this is a
    ``@runtime_checkable`` Protocol) before using it, e.g. ``tools/coding.py::run_command``.

    Today only the desktop host-agent bridge (``adapters/host_agent.py::HostAgentSandboxRunner``,
    and ``CompositeSandboxRunner`` when it has routed a handle there) implements this -- the
    server-side Docker sandbox and the in-memory test fake do not, so a deployment with no paired
    desktop app is unaffected either way.
    """

    def run_streaming(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> AsyncIterator[SandboxOutputEvent]:
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


@runtime_checkable
class AgentRunStore(EngineerRunStore, Protocol):
    """:class:`EngineerRunStore`, generalised to every agent's run (GAP-024).

    An extension of the engineer-runs port rather than a parallel one: the same adapter holds
    both ledgers (``aura_engineer_runs`` for per-engineer-tool-call rows, ``aura_agent_runs`` /
    ``aura_agent_steps`` for per-run state of any persona), shares one connection pool, and a deployment that wires one has wired both.

    Every read is owner-scoped by construction -- ``user_id`` is a required argument, not a
    filter a caller may forget -- so a run id guessed or leaked across users reads as absent.
    Runs are upserted by id (live state: iterations and budgets move while the loop runs);
    steps are append-only, like engineer runs.
    """

    async def save_run(self, run: AgentRun) -> None:
        ...

    async def append_step(self, step: AgentStep) -> None:
        ...

    async def get_run(self, run_id: AgentRunId, *, user_id: UserId) -> AgentRun | None:
        ...

    async def list_runs(
        self,
        user_id: UserId,
        *,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> Sequence[AgentRun]:
        ...

    async def list_steps(
        self, run_id: AgentRunId, *, user_id: UserId, limit: int = 500,
    ) -> Sequence[AgentStep]:
        ...


@runtime_checkable
class WorkspaceStore(Protocol):
    """Persistence for :class:`~aura_core.domain.engineering.AgentWorkspace` rows.

    Deliberately mutable, unlike :class:`EngineerRunStore` next to it. A run is a fact that
    happened and is only ever appended; a workspace is live state whose whole purpose is to be
    updated -- ``touch`` on every tool call is what the idle TTL measures, and without it a
    workspace in continuous use would be reaped on its third day.

    ``touch`` is separated from ``upsert`` rather than folded into it because it is the hot path:
    every single tool call performs one, and it must not require reading, mutating and writing
    back a whole row (which would also race two concurrent calls against each other, with the
    slower one restoring stale fields).
    """

    async def upsert(self, workspace: AgentWorkspace) -> None:
        """Create or replace the row for this workspace id."""
        ...

    async def get(self, user_id: UserId, repo: str) -> AgentWorkspace | None:
        """The workspace for this ``(user, repo)`` pair, or ``None``.

        This pair is the natural key the tool layer resolves on -- an agent asked to work on a
        repository knows the user and the repository, not a workspace id.
        """
        ...

    async def get_by_id(self, workspace_id: WorkspaceId) -> AgentWorkspace | None:
        ...

    async def list_for_user(self, user_id: UserId) -> Sequence[AgentWorkspace]:
        """Every workspace this user owns, most recently active first."""
        ...

    async def list_all(self) -> Sequence[AgentWorkspace]:
        """Every workspace, for the reaper and for startup reconciliation.

        Not scoped to a user because neither caller has one: they are reconciling this host's
        containers against this host's rows.
        """
        ...

    async def list_idle_since(self, cutoff: datetime) -> Sequence[AgentWorkspace]:
        """Workspaces whose ``last_active_at`` is at or before ``cutoff``.

        The reaper's query. Taking a cutoff rather than a TTL keeps the clock in the caller,
        which is what lets a test drive expiry with ``FrozenClock`` instead of sleeping.
        """
        ...

    async def touch(self, workspace_id: WorkspaceId, *, at: datetime, status: str | None = None) -> None:
        """Record activity, optionally moving the workspace's status in the same write."""
        ...

    async def delete(self, workspace_id: WorkspaceId) -> None:
        ...
