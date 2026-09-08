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
    """Ephemeral, isolated container per task.

    ``create_task`` provisions a scratch directory and returns a new :class:`SandboxHandle`;
    ``run``/``write_file``/``read_file`` lazily start (or reuse) one container per handle,
    bind-mounting that scratch directory at ``/workspace``; ``destroy`` removes both. There is no
    implicit cleanup on a timer here -- the caller (``tools/coding.py``'s
    ``_SandboxSessionCache``) owns each handle's lifetime.
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

    async def _ensure_container(self, handle: SandboxHandle) -> Any:
        container = self._containers.get(handle.task_id)
        if container is not None:
            return container

        def _start() -> Any:
            client = self._get_client()
            run_kwargs: dict[str, Any] = dict(
                image=self._image,
                command=["sleep", "infinity"],
                working_dir=_WORKDIR,
                volumes={str(self._scratch(handle)): {"bind": _WORKDIR, "mode": "rw"}},
                mem_limit=self._memory_limit,
                nano_cpus=int(self._cpu_limit * 1_000_000_000),
                detach=True,
                security_opt=["no-new-privileges"],
            )
            if self._network:
                run_kwargs["network"] = self._network
            else:
                run_kwargs["network_disabled"] = True
            return client.containers.run(**run_kwargs)

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
