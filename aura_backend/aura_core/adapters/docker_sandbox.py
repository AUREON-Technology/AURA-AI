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

Network isolation is the operator's responsibility at the compose level (see
``deploy/docker-compose.*.yml``'s ``sandbox_egress`` network): this adapter only attaches
containers to whatever network name it is given, or disables networking entirely when none is
configured. It does not itself enforce an egress allowlist -- that is a property of the Docker
network the operator provisions, not something a container-launch call can add after the fact.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

from ..observability.logging import get_logger
from ..ports.coding import SandboxHandle, SandboxResult

__all__ = ["DockerSandboxRunner", "SandboxError", "docker_available"]

logger = get_logger("adapters.docker_sandbox")

_WORKDIR = "/workspace"

#: Marks a container as a durable engineer-agent workspace and carries its key. The label, not
#: this process's memory, is the source of truth: it survives a gateway restart, which is exactly
#: when in-memory bookkeeping would otherwise start a second container over an existing checkout.
_WORKSPACE_LABEL = "aura.workspace"

#: Seconds to let a workspace's processes finish on pause before the daemon kills them. Short --
#: a paused workspace's value is its files, and nothing in flight is expected to survive.
_STOP_TIMEOUT_S = 10


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
    :class:`SandboxHandle`; ``run``/``write_file``/``read_file`` lazily start (or reuse) one
    container per handle, bind-mounting that directory at ``/workspace``; ``destroy`` removes
    both. Nothing cleans these up on a timer -- the caller owns each handle's lifetime.

    *Durable*: ``open_workspace`` keys off a caller-supplied id instead of a fresh one, labels
    the container ``aura.workspace=<key>``, and resumes it on later calls. ``pause_workspace``
    stops it while keeping the checkout, so a workspace between turns holds no memory;
    ``destroy_workspace`` removes container and files together.

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
    ) -> None:
        self._image = image
        self._network = network
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._host_root = Path(host_root) if host_root else None
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
            return Path(tempfile.mkdtemp(prefix=f"aura-sandbox-{task_id}-"))
        scratch = self._host_root / task_id
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    async def create_task(self) -> SandboxHandle:
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
            nano_cpus=int(self._cpu_limit * 1_000_000_000),
            detach=True,
            security_opt=["no-new-privileges"],
        )
        if self._network:
            run_kwargs["network"] = self._network
        else:
            run_kwargs["network_disabled"] = True
        return run_kwargs

    async def _ensure_container(self, handle: SandboxHandle) -> Any:
        container = self._containers.get(handle.task_id)
        if container is not None:
            return container

        def _start() -> Any:
            client = self._get_client()
            # A durable workspace may already have a container this process has forgotten
            # (restart). Adopting it is not an optimisation: starting a fresh one would leave two
            # containers bind-mounting one checkout, and the second would not be labelled, so
            # neither reconciliation nor the reaper would ever find it again.
            existing = client.containers.list(
                all=True, filters={"label": f"{_WORKSPACE_LABEL}={handle.task_id}"}
            )
            if existing:
                adopted = existing[0]
                adopted.reload()
                if adopted.status != "running":
                    adopted.start()
                return adopted
            return client.containers.run(**self._container_kwargs(self._scratch(handle)))

        container = await asyncio.to_thread(_start)
        self._containers[handle.task_id] = container
        return container

    async def run(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> SandboxResult:
        container = await self._ensure_container(handle)

        def _exec() -> SandboxResult:
            exit_code, output = container.exec_run(list(command), demux=True, workdir=_WORKDIR)
            stdout_bytes, stderr_bytes = output if isinstance(output, tuple) else (output, b"")
            return SandboxResult(
                stdout=(stdout_bytes or b"").decode("utf-8", errors="replace"),
                stderr=(stderr_bytes or b"").decode("utf-8", errors="replace"),
                exit_code=int(exit_code or 0),
            )

        try:
            return await asyncio.wait_for(asyncio.to_thread(_exec), timeout=timeout_s)
        except asyncio.TimeoutError:
            return SandboxResult(
                stdout="", stderr=f"command timed out after {timeout_s:.0f}s", exit_code=124
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
