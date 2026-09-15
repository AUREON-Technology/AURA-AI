"""Docker sandbox adapter: one ephemeral container per engineer-agent task.

``docker`` is imported lazily inside methods, matching every other adapter's rule that importing
this module must never require the dependency -- let alone a reachable daemon -- to be present.
:func:`docker_available` is the same "import succeeds *and* the thing actually answers" probe as
``tesseract_available()``/``faster_whisper_available()``, so ``Container._build_sandbox_runner``
can return ``None`` for a deployment that opted in but has no reachable Docker daemon, rather than
failing at startup.

**Bind-mount sources are host paths.** When this adapter runs inside a container itself (the
deployed case: the gateway talks to the host daemon through ``docker-socket-proxy``), a
workspace directory it creates in its *own* filesystem is not the directory the daemon mounts
under that name -- the daemon resolves the source on the host and silently creates an empty one
there. The two sides then disagree: ``write_file`` writes here, ``run_command`` reads there, and
nothing errors. ``host_root`` closes that gap by naming a directory bind-mounted into this
process at the same absolute path it has on the host, so one path means one directory. It is
optional precisely because the local and test cases have no such split.

**Commands run in their own container, not via ``docker exec``.** The deployed gateway reaches
the daemon through ``docker-socket-proxy``, whose allowlist is daemon-wide rather than
per-container: turning on ``EXEC`` so a workspace could be entered would equally allow entering
``postgres`` or ``ollama``, so both compose files set ``EXEC: "0"`` and exec is refused with a
403. Starting a container needs only ``CONTAINERS``, which is already allowed. See :meth:`run`.

Network isolation is split between this adapter and the compose stack, and neither half works
alone. This adapter attaches containers to the network name it is given (or disables networking
outright when given none) and sets ``HTTP(S)_PROXY`` when a proxy is configured. The compose file
makes ``sandbox_egress`` ``internal: true`` and puts an allowlisting ``sandbox-proxy`` sidecar on
it, so those variables are not a suggestion a determined command could ignore: there is no other
route out, and a client that skips the proxy simply fails to connect.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

from ..observability.logging import get_logger
from ..ports.coding import FileWriteOp, SandboxHandle, SandboxResult

__all__ = ["DockerSandboxRunner", "SandboxError", "docker_available"]

logger = get_logger("adapters.docker_sandbox")

_WORKDIR = "/workspace"

#: Marks a container as a durable engineer-agent workspace and carries its key. The label, not
#: this process's memory, is the source of truth: it survives a gateway restart, which is exactly
#: when in-memory bookkeeping would otherwise start a second container over an existing checkout.
_WORKSPACE_LABEL = "aura.workspace"

#: Marks a throwaway container started to run exactly one command. It is removed as soon as the
#: command finishes; the label exists so one orphaned by this process dying mid-command is
#: findable (`docker ps -a --filter label=aura.sandbox.oneshot`) rather than anonymous.
_ONESHOT_LABEL = "aura.sandbox.oneshot"

#: Seconds to let a workspace's processes finish on pause before the daemon kills them. Short --
#: a paused workspace's value is its files, and nothing in flight is expected to survive.
_STOP_TIMEOUT_S = 10

#: Slack added to a command's own timeout before this process stops waiting on the daemon at all.
#: The inner `container.wait(timeout=...)` is what normally enforces the deadline; this only
#: bounds the case where the daemon itself stops answering, which would otherwise hang the turn.
_WAIT_GRACE_S = 15.0

#: Must match the uid/gid created in deploy/sandbox/Dockerfile, because the host workspace
#: directory is chowned to it. Mismatch shows up as permission errors on the first write, not at
#: container start, so the two are deliberately stated in both places rather than inferred.
_SANDBOX_UID_N = 10001
_SANDBOX_GID_N = 10001
_SANDBOX_UID = f"{_SANDBOX_UID_N}:{_SANDBOX_GID_N}"

#: Enough for a real build (a test runner forks freely); far short of exhausting the host.
_PIDS_LIMIT = 512
_NOFILE_LIMIT = 4096
_NPROC_LIMIT = 512


def _ulimit(name: str, value: int) -> Any:
    """Build a docker-py Ulimit lazily, so importing this module still needs no `docker`."""
    from docker.types import Ulimit

    return Ulimit(name=name, soft=value, hard=value)


def _is_read_timeout(exc: BaseException) -> bool:
    """True for the read timeout ``container.wait(timeout=...)`` surfaces when a command overruns.

    Matched on the exception's *name*, walking the ``__cause__``/``__context__`` chain, because
    docker-py raises this from ``requests`` and wraps it inconsistently across versions --
    and because naming the class would mean importing ``requests`` at module scope, which this
    module deliberately does not do (see the module docstring's lazy-import rule). Anything that
    is not a timeout is re-raised by the caller rather than reported as one.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if "timeout" in type(current).__name__.lower():
            return True
        current = current.__cause__ or current.__context__
    return False


class SandboxError(RuntimeError):
    """Raised for any sandbox lifecycle or containment failure."""


def docker_available() -> bool:
    """True iff the ``docker`` package is importable and a daemon actually answers a ping."""
    try:
        import docker
    except ImportError:
        return False
    try:
        client = docker.from_env()
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


class DockerSandboxRunner:
    """Isolated containers for agent work, in two lifetimes.

    *Ephemeral*: ``create_task`` provisions a scratch directory and returns a new
    :class:`SandboxHandle`; ``write_file``/``read_file`` act on that directory directly and
    ``run`` starts a throwaway container per command, bind-mounting it at ``/workspace``;
    ``destroy`` removes the directory. Nothing cleans these up on a timer -- the caller owns each
    handle's lifetime.

    Commands never use ``docker exec``; see :meth:`run` for why the socket proxy's ``EXEC: "0"``
    makes a container-per-command the only way to keep that allowlist as narrow as it is.

    *Durable*: ``open_workspace`` keys off a caller-supplied id instead of a fresh one, labels
    the container ``aura.workspace=<key>``, and resumes it on later calls. ``pause_workspace``
    stops it while keeping the checkout, so a workspace between turns holds no memory;
    ``destroy_workspace`` removes container and files together. Since commands run in their own
    containers, this one carries the workspace's *identity* -- the label reconciliation and the
    reaper find it by -- rather than executing anything.

    The label is what makes the durable half survive this process restarting. Every durable
    lookup goes to the daemon, never to ``self._containers``, because after a restart that
    dictionary is empty while the containers are still running -- and a second container over the
    same checkout is both a correctness bug and an invisible resource leak, since the duplicate
    would carry no label for anything to find it by.
    """

    def __init__(
        self,
        *,
        image: str,
        network: str = "",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        host_root: str = "",
        proxy_url: str = "",
    ) -> None:
        self._image = image
        self._network = network
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._host_root = Path(host_root) if host_root else None
        self._proxy_url = proxy_url
        self._client: Any = None
        self._scratch_dirs: dict[str, Path] = {}
        self._containers: dict[str, Any] = {}

    def _get_client(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def _make_scratch(self, task_id: str) -> Path:
        """Create this task's workspace directory at a path the *daemon* can also resolve.

        ``self._host_root`` is a directory bind-mounted into this process's own container at the
        same absolute path it has on the host, so a path built under it means the same thing on
        both sides of the Docker API. Without it (local runs, tests, any deployment where this
        process and the daemon share a filesystem) a plain temp directory is already such a path.
        """
        if self._host_root is None:
            scratch = Path(tempfile.mkdtemp(prefix=f"aura-sandbox-{task_id}-"))
        else:
            scratch = self._host_root / task_id
            scratch.mkdir(parents=True, exist_ok=True)
        self._grant_to_sandbox_user(scratch)
        return scratch

    @staticmethod
    def _grant_to_sandbox_user(scratch: Path) -> None:
        """Make the workspace writable by the unprivileged uid the container runs as.

        ``bootstrap-host.sh`` chowns the *root*, but these per-workspace directories are created
        here at runtime and inherit this process's ownership -- root, in the deployed gateway. A
        root-owned bind mount under a container running as uid 10001 is readable and not
        writable, which surfaces as permission denied on the first file the agent creates rather
        than as anything at container start.

        Falls back to a permissive mode when this process cannot chown (not root, or a
        filesystem that refuses): the directory is inside the workspace root on a single-tenant
        host, and an unwritable workspace is a broken feature.
        """
        try:
            os.chown(scratch, _SANDBOX_UID_N, _SANDBOX_GID_N)
        except (OSError, AttributeError):
            try:
                scratch.chmod(0o777)
            except OSError:
                logger.warning(
                    "could not make sandbox workspace writable by the container user; "
                    "commands in it will fail to write",
                    extra={"path": str(scratch)},
                )

    async def create_task(
        self, *, user_id: Any | None = None, session_id: str | None = None, isolate: bool = False
    ) -> SandboxHandle:
        # `user_id`/`session_id`/`isolate` are accepted for `SandboxRunner` protocol conformance
        # and ignored: this server-side sandbox has its own durable-workspace story
        # (`open_workspace`/`pause_workspace`), not D8's per-task git worktrees, which only the
        # desktop host-agent bridge (`adapters/host_agent.py`) implements.
        task_id = uuid.uuid4().hex
        scratch = await asyncio.to_thread(self._make_scratch, task_id)
        self._scratch_dirs[task_id] = scratch
        return SandboxHandle(task_id=task_id)

    def _scratch(self, handle: SandboxHandle) -> Path:
        scratch = self._scratch_dirs.get(handle.task_id)
        if scratch is None:
            raise SandboxError(f"unknown sandbox task {handle.task_id!r}; call create_task first")
        return scratch

    def _container_kwargs(self, scratch: Path) -> dict[str, Any]:
        """Launch arguments shared by ephemeral tasks and durable workspaces.

        One builder rather than two so the isolation settings cannot drift apart: a durable
        workspace lives for days and is the one that least deserves weaker confinement than a
        per-turn sandbox.
        """
        run_kwargs: dict[str, Any] = dict(
            image=self._image,
            command=["sleep", "infinity"],
            working_dir=_WORKDIR,
            volumes={str(scratch): {"bind": _WORKDIR, "mode": "rw"}},
            mem_limit=self._memory_limit,
            # Without this, `mem_limit` bounds RAM but not swap, and a container over its limit
            # swaps instead of failing -- which on this host means competing for disk I/O with
            # Postgres rather than hitting a ceiling.
            memswap_limit=self._memory_limit,
            nano_cpus=int(self._cpu_limit * 1_000_000_000),
            detach=True,
            # A workspace runs commands a model chose, against a repository an attacker may
            # control, and lives for days rather than one turn. Everything below is the same
            # confinement a per-turn sandbox gets or stronger; none of it costs anything a
            # legitimate build needs.
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            # Root in the container is root on the bind-mounted host directory. The image
            # creates this uid and bootstrap-host.sh chowns the workspace root to match.
            user=_SANDBOX_UID,
            # Only /workspace and the tmpfs mounts below are writable, which also means the
            # reaper has exactly one directory to remove to reclaim a workspace.
            read_only=True,
            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,size=256m",
                # git refuses to run with nowhere to write its config; pip and npm want a cache.
                "/home/aura": "rw,nosuid,nodev,size=64m",
            },
            # A fork bomb should hit a limit rather than the host's process table.
            pids_limit=_PIDS_LIMIT,
            ulimits=[
                _ulimit("nofile", _NOFILE_LIMIT),
                _ulimit("nproc", _NPROC_LIMIT),
            ],
        )
        if self._network:
            run_kwargs["network"] = self._network
            # Use Docker's embedded DNS resolver for service name resolution on the network
            run_kwargs["dns"] = ["127.0.0.11"]
        else:
            run_kwargs["network_disabled"] = True
        if self._proxy_url:
            # Egress goes through the allowlisting proxy or not at all -- see the
            # `sandbox-proxy` service and the internal `sandbox_egress` network.
            run_kwargs["environment"] = {
                "HTTP_PROXY": self._proxy_url,
                "HTTPS_PROXY": self._proxy_url,
                "http_proxy": self._proxy_url,
                "https_proxy": self._proxy_url,
                "NO_PROXY": "localhost,127.0.0.1",
                "no_proxy": "localhost,127.0.0.1",
            }
        return run_kwargs

    async def run(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> SandboxResult:
        """Run one command in a container of its own, over this handle's workspace directory.

        **Deliberately not ``docker exec``.** The gateway reaches the daemon through
        ``docker-socket-proxy`` with ``EXEC: "0"`` in both compose files, so
        ``POST /containers/{id}/exec`` is refused with a 403 -- and that flag cannot be narrowed
        to the sandbox, because the proxy's allowlist is daemon-wide: permitting exec so a
        workspace could be entered would equally permit entering ``postgres`` and ``ollama``.
        Starting a container needs only ``CONTAINERS``, which the proxy already allows, so
        command execution works without giving back any of the reduction Unit 4 made.

        Nothing is lost by not reusing a container. A workspace's state is the checkout on the
        bind mount -- that is the same claim the hot/warm design already rests on when it stops a
        workspace between turns -- so a fresh container mounting the same directory sees exactly
        the same files. The cost is container start (~100-300 ms) rather than exec (~30 ms),
        which against this deployment's 5-10 tok/s inference is not where the time goes.

        The container inherits :meth:`_container_kwargs` unchanged, so a one-shot command runs
        under the same confinement a durable workspace does: unprivileged uid, all capabilities
        dropped, read-only rootfs, pid and file limits, and egress only through the proxy.
        """
        scratch = self._scratch(handle)

        def _run_once() -> SandboxResult:
            client = self._get_client()
            run_kwargs = self._container_kwargs(scratch)
            run_kwargs["command"] = list(command)
            run_kwargs["labels"] = {_ONESHOT_LABEL: handle.task_id}
            # Never restart: a command that failed should stay failed and be reported, not be
            # retried by the daemon behind the agent's back.
            run_kwargs["restart_policy"] = {"Name": "no"}
            container = client.containers.run(**run_kwargs)
            try:
                try:
                    status = container.wait(timeout=timeout_s)
                except Exception as exc:
                    if not _is_read_timeout(exc):
                        raise
                    # Kill rather than leave it running: the caller has already given up on the
                    # result, and a runaway build would otherwise hold its memory and CPU share
                    # until the workspace was reaped.
                    try:
                        container.kill()
                    except Exception:
                        logger.debug("timed-out command kill failed", extra={"task": handle.task_id})
                    return SandboxResult(
                        stdout="",
                        stderr=f"command timed out after {timeout_s:.0f}s",
                        exit_code=124,
                    )
                # Read the streams separately rather than demuxed: this container ran one
                # command, so its whole log *is* that command's output.
                stdout_bytes = container.logs(stdout=True, stderr=False) or b""
                stderr_bytes = container.logs(stdout=False, stderr=True) or b""
                return SandboxResult(
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
                    exit_code=int(status.get("StatusCode", 0) or 0),
                )
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.debug(
                        "one-shot container removal failed", extra={"task": handle.task_id}
                    )

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_once), timeout=timeout_s + _WAIT_GRACE_S
            )
        except asyncio.TimeoutError:
            # Only reachable when the daemon itself stopped answering -- `container.wait`'s own
            # timeout handles an overrunning command and returns 124 above.
            return SandboxResult(
                stdout="",
                stderr=f"the Docker daemon did not respond within {timeout_s:.0f}s",
                exit_code=124,
            )

    def _resolve_within(self, handle: SandboxHandle, relative: str) -> Path:
        """Contain every path to the task's scratch directory -- the same symlink-safe
        containment ``tools/builtin.py::build_file_tools`` uses for the (different, read-only)
        user workspace root: resolve, then check containment, so a symlink inside the scratch
        directory cannot point outside it."""
        root = self._scratch(handle).resolve()
        if "\x00" in relative:
            raise SandboxError("path contains a null byte")
        candidate = (root / relative.lstrip("/")).resolve()
        if candidate != root and root not in candidate.parents:
            raise SandboxError(f"path {relative!r} escapes the sandbox workspace")
        return candidate

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        target = self._resolve_within(handle, path)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write)

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        target = self._resolve_within(handle, path)
        if not target.is_file():
            raise SandboxError(f"{path} is not a file in the sandbox workspace")
        return await asyncio.to_thread(target.read_text, "utf-8")

    async def write_files(self, handle: SandboxHandle, operations: Sequence[FileWriteOp]) -> None:
        """D6: pre-validate every operation, then write all of them -- see the port's own
        docstring for why nothing is written until every operation has already passed."""

        def _validate_and_plan() -> list[tuple[Path, str]]:
            plan: list[tuple[Path, str]] = []
            for op in operations:
                target = self._resolve_within(handle, op.path)
                if op.content is not None:
                    plan.append((target, op.content))
                elif op.find is not None and op.replace is not None:
                    if not target.is_file():
                        raise SandboxError(f"{op.path} does not exist")
                    current = target.read_text("utf-8")
                    occurrences = current.count(op.find)
                    if occurrences != 1:
                        raise SandboxError(
                            f"expected exactly one occurrence of the given text in {op.path}, "
                            f"found {occurrences}"
                        )
                    plan.append((target, current.replace(op.find, op.replace, 1)))
                else:
                    raise SandboxError(f"operation for {op.path} must set either content, or both find and replace")
            return plan

        def _write_all() -> None:
            plan = _validate_and_plan()
            for target, content in plan:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write_all)

    # -- durable workspaces ------------------------------------------------------------------
    #
    # Everything below keys off a Docker *label* rather than this object's dictionaries. That is
    # the whole point: after a gateway restart the dictionaries are empty while the containers
    # and their checkouts are still there, and a runner that trusted its own memory would start a
    # second container over the same files instead of resuming the first.

    async def open_workspace(self, key: str) -> SandboxHandle:
        handle = SandboxHandle(task_id=key)
        scratch = self._scratch_dirs.get(key)
        if scratch is None:
            scratch = await asyncio.to_thread(self._make_scratch, key)
            self._scratch_dirs[key] = scratch

        def _open() -> Any:
            client = self._get_client()
            existing = client.containers.list(
                all=True, filters={"label": f"{_WORKSPACE_LABEL}={key}"}
            )
            if existing:
                container = existing[0]
                container.reload()
                if container.status != "running":
                    container.start()
                return container
            run_kwargs = self._container_kwargs(scratch)
            run_kwargs["labels"] = {_WORKSPACE_LABEL: key}
            # Never `unless-stopped`: a host reboot must not bring every workspace back up
            # unprompted, which would spend the memory budget before anyone asked for it.
            run_kwargs["restart_policy"] = {"Name": "no"}
            return client.containers.run(**run_kwargs)

        container = await asyncio.to_thread(_open)
        self._containers[key] = container
        return handle

    async def pause_workspace(self, handle: SandboxHandle) -> None:
        def _pause() -> None:
            client = self._get_client()
            for container in client.containers.list(
                all=True, filters={"label": f"{_WORKSPACE_LABEL}={handle.task_id}"}
            ):
                try:
                    container.stop(timeout=_STOP_TIMEOUT_S)
                except Exception:
                    logger.debug("workspace stop failed", extra={"workspace": handle.task_id})

        await asyncio.to_thread(_pause)
        self._containers.pop(handle.task_id, None)

    async def destroy_workspace(self, handle: SandboxHandle) -> None:
        def _remove() -> None:
            client = self._get_client()
            for container in client.containers.list(
                all=True, filters={"label": f"{_WORKSPACE_LABEL}={handle.task_id}"}
            ):
                try:
                    container.remove(force=True)
                except Exception:
                    logger.debug("workspace removal failed", extra={"workspace": handle.task_id})

        await asyncio.to_thread(_remove)
        self._containers.pop(handle.task_id, None)
        scratch = self._scratch_dirs.pop(handle.task_id, None)
        if scratch is None and self._host_root is not None:
            # Reaping a workspace this process never opened (the usual case after a restart):
            # the directory is still derivable from the key.
            scratch = self._host_root / handle.task_id
        if scratch is not None:
            await asyncio.to_thread(shutil.rmtree, scratch, True)

    async def workspace_keys(self) -> Sequence[str]:
        def _keys() -> list[str]:
            client = self._get_client()
            found: list[str] = []
            for container in client.containers.list(all=True, filters={"label": _WORKSPACE_LABEL}):
                key = (container.labels or {}).get(_WORKSPACE_LABEL)
                if key:
                    found.append(key)
            return found

        return await asyncio.to_thread(_keys)

    async def workspace_disk_usage_mb(self, handle: SandboxHandle) -> float:
        scratch = self._scratch_dirs.get(handle.task_id)
        if scratch is None and self._host_root is not None:
            scratch = self._host_root / handle.task_id
        if scratch is None or not scratch.exists():
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
        scratch = self._scratch_dirs.get(handle.task_id)
        if scratch is None and self._host_root is not None:
            scratch = self._host_root / handle.task_id
        if scratch is None:
            raise SandboxError(f"unknown workspace {handle.task_id!r}")
        return str(scratch)

    async def destroy(self, handle: SandboxHandle) -> None:
        container = self._containers.pop(handle.task_id, None)
        if container is not None:
            def _remove() -> None:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.debug(
                        "sandbox container removal failed", extra={"task": handle.task_id}
                    )

            await asyncio.to_thread(_remove)
        scratch = self._scratch_dirs.pop(handle.task_id, None)
        if scratch is not None:
            await asyncio.to_thread(shutil.rmtree, scratch, True)
