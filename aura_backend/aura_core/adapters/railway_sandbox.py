"""Cloud sandbox runner: Railway Sandboxes through AURA's private broker
(``aura_sandbox_broker/``, ``docs/plans/railway-sandboxes-plan.md``).

The broker holds the Railway token and fixes how a sandbox is made; this adapter only names a
sandbox, a command, a file or a checkpoint. ``httpx`` is imported lazily.

**A key, not a sandbox, is what callers hold.** A workspace or task key has at most one sandbox
attached. It boots on first use, from the key's checkpoint if it has one;
:meth:`RailwaySandboxRunner.pause_workspace` saves a new checkpoint and destroys the sandbox.
Which sandbox and which checkpoint belong to a key lives in a
:class:`~aura_core.ports.coding.CloudSandboxStore`, because several web workers serve one user.

Four rules, each of which something would otherwise lose silently:

* **A command is sent once.** Nothing here repeats an exec request, whatever answers. A stream
  that ends without ``exit`` or ``error`` means the outcome is unknown, and is reported as that.
* **A sandbox that could not be saved is not destroyed.**
* **A vanished sandbox is reported**, not quietly replaced by the older checkpoint.
* **Boot, save and removal outlive their caller.** A tool call has a short deadline and these do
  not always fit in it; cancelled half way they would leave a sandbox nobody owns, or a workspace
  nobody can use.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import tarfile
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Coroutine, Sequence

from ..observability.logging import get_logger
from ..ports.coding import (
    BranchExport,
    CloudSandboxStore,
    FileWriteOp,
    SandboxHandle,
    SandboxOutputEvent,
    SandboxResult,
)
from .docker_sandbox import SandboxError, decode_workspace_text
from .sandbox_transfer import export_branch, workspace_relative

__all__ = ["RailwaySandboxRunner", "SandboxOutcomeUnknown"]

logger = get_logger("adapters.railway_sandbox")

#: The same ceilings as the other runners (``docker_sandbox.py``).
_MAX_EDIT_BYTES = 10 * 1024 * 1024
_MAX_READ_BYTES = 100 * 1024 * 1024
#: Output kept by :meth:`RailwaySandboxRunner.run`, per stream. The broker caps it already.
_MAX_OUTPUT_CHARS = 8 * 1024 * 1024
#: One NDJSON line: the broker's output cap, as JSON escapes it at worst.
_MAX_LINE_BYTES = 32 * 1024 * 1024
#: The broker accepts 1..900 whole seconds.
_MAX_COMMAND_S = 900
#: The memory the broker gives every sandbox (its fixed policy).
_SANDBOX_MEMORY_MB = 2048.0
_BOOT_ATTEMPTS = 3
#: How long a file call may hold the sandbox before it is assumed abandoned.
_CALL_LEASE_S = 600.0
#: Refusals after which the broker certainly changed nothing.
_NOTHING_CHANGED = frozenset(
    {"path_refused", "file_too_large", "invalid_request", "sandbox_busy", "unauthorized"}
)
_ERROR_CODE = re.compile(r"[a-z_]{1,40}")
_SANDBOX_ID = re.compile(r"[0-9A-Za-z-]{1,64}")

#: ``$1`` is the destination folder; refused unless it is absent or an empty folder.
_EMPTY_CHECK = """
d=$1
if [ -e "$d" ] && { [ ! -d "$d" ] || [ -n "$(ls -A -- "$d")" ]; }; then exit 3; fi
"""
#: ``$1`` destination, ``$2`` a scratch folder name at the workspace root, then the uploaded parts
#: of a ``.tar.gz``. The checkout appears at the destination only once it is whole: a command cut
#: off half way leaves at most a hidden folder, which the git tools never take for a repository.
_UNPACK = """
set -e
d=$1
t=$2
shift 2
trap 'rm -rf -- "$t"; rm -f -- "$@"' EXIT
if [ -e "$d" ] && { [ ! -d "$d" ] || [ -n "$(ls -A -- "$d")" ]; }; then exit 3; fi
mkdir -p -- "$(dirname -- "$d")"
mkdir -- "$t"
cat -- "$@" | tar -xzf - -C "$t"
[ ! -d "$d" ] || rmdir -- "$d"
mv -- "$t" "$d"
"""


#: ``delete_file`` and ``rename_file`` run inside the sandbox, under the rules of
#: ``sandbox_fs.py``: an entry is named, not followed (its parent is resolved, the last part is
#: not), nothing at or under ``.git``, delete removes no folder, rename replaces nothing.
#: Exit: 91 leaves the workspace, 92 no such entry, 93 inside ``.git``, 94 a folder,
#: 95 the destination exists, 96 a folder moved into itself.
_ENTRY = """
root=$(realpath -m -- .) || exit 1
entry() {
    parent=$(realpath -m -- "$(dirname -- "$1")") || exit 1
    case "$parent/" in "$root"/*) ;; *) exit 91 ;; esac
    case "$(printf '%s' "${parent#"$root"}/" | tr 'A-Z' 'a-z')" in */.git/*) exit 93 ;; esac
    found="$parent/$(basename -- "$1")"
}
"""
_DELETE = _ENTRY + """
entry "$1"
{ [ -e "$found" ] || [ -L "$found" ]; } || exit 92
if [ -d "$found" ] && [ ! -L "$found" ]; then exit 94; fi
rm -f -- "$found"
"""
_RENAME = _ENTRY + """
entry "$1"
source=$found
entry "$2"
{ [ -e "$source" ] || [ -L "$source" ]; } || exit 92
if [ -e "$found" ] || [ -L "$found" ]; then exit 95; fi
if [ -d "$source" ] && [ ! -L "$source" ]; then case "$found/" in "$source"/*) exit 96 ;; esac; fi
mkdir -p -- "$(dirname -- "$found")" || exit 1
mv -n -- "$source" "$found"
"""


class SandboxOutcomeUnknown(SandboxError):
    """A command may or may not have run. It was not, and must not be, sent again blindly."""


class _Refused(SandboxError):
    """The broker answered with an error code."""

    def __init__(self, code: str) -> None:
        super().__init__(f"the cloud sandbox refused the request ({code})")
        self.code = code


class _NoSuchFile(SandboxError):
    pass


def _lost(detail: str = "") -> SandboxError:
    return SandboxError(
        "the cloud sandbox no longer exists, so anything changed since it was last saved is "
        f"gone. {detail}Try again: the next call starts from the saved copy."
    )


def _pack(source: Path, archive: Path, max_bytes: int) -> None:
    """``source`` as a ``.tar.gz``. Links are stored as links and never followed."""
    total = sum(path.lstat().st_size for path in source.rglob("*"))
    if total > max_bytes:
        raise SandboxError(
            f"the repository is too large for a cloud workspace ({total // (1024 * 1024)} MiB; "
            f"limit {max_bytes // (1024 * 1024)} MiB)"
        )

    def neutral(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        tar.add(source, arcname=".", filter=neutral)


class RailwaySandboxRunner:
    """``SandboxRunner``, ``StreamingSandboxRunner`` and ``RepositoryTransferRunner`` over the
    broker. Nothing touches the network or the store until the first call."""

    def __init__(
        self,
        *,
        broker_url: str,
        token: str,
        store: CloudSandboxStore,
        clock: Any = None,
        transport: Any = None,
        request_timeout_s: float = 120.0,
        boot_timeout_s: float = 300.0,
        checkpoint_timeout_s: float = 600.0,
        save_wait_s: float = 180.0,
        poll_s: float = 0.5,
        max_transfer_bytes: int = 1024 * 1024 * 1024,
        part_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._broker_url = broker_url.rstrip("/")
        self._token = token
        self._store = store
        self._clock = clock
        self._transport = transport
        self._request_timeout_s = request_timeout_s
        self._boot_timeout_s = boot_timeout_s
        self._checkpoint_timeout_s = checkpoint_timeout_s
        self._save_wait_s = save_wait_s
        self._poll_s = poll_s
        self._max_transfer_bytes = max_transfer_bytes
        self._part_bytes = part_bytes
        self._client: Any = None
        self._client_lock = asyncio.Lock()
        self._boots: dict[str, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()

    def __repr__(self) -> str:  # the token must never reach a log through the object
        return f"RailwaySandboxRunner(broker_url={self._broker_url!r})"

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock.now()
        return datetime.now(timezone.utc)

    async def close(self) -> None:
        """Let boots and saves in progress finish, briefly, then close the HTTP client.

        Sandboxes and checkpoints are left alone: workspaces outlive this process.
        """
        if self._background:
            _, pending = await asyncio.wait(set(self._background), timeout=60.0)
            for task in pending:
                task.cancel()
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    def _spawn(self, work: Coroutine[Any, Any, None]) -> asyncio.Task:
        """Run ``work`` so that it finishes even if the caller waiting for it is cancelled."""
        task = asyncio.create_task(work)
        self._background.add(task)

        def done(finished: asyncio.Task) -> None:
            self._background.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.info("cloud sandbox housekeeping failed: %s", finished.exception())

        task.add_done_callback(done)
        return task

    # -- the broker -------------------------------------------------------------------------

    async def _http(self) -> Any:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    import httpx

                    self._client = httpx.AsyncClient(
                        base_url=self._broker_url,
                        headers={"authorization": f"Bearer {self._token}"},
                        transport=self._transport,
                        # The token and the user's code go to the broker and nowhere else: no
                        # proxy variables, no redirect.
                        trust_env=False,
                        follow_redirects=False,
                        timeout=httpx.Timeout(self._request_timeout_s, connect=10.0),
                    )
        return self._client

    @staticmethod
    def _refusal(response: Any) -> _Refused:
        try:
            code = response.json()["error"]
        except Exception:
            code = None
        if not (isinstance(code, str) and _ERROR_CODE.fullmatch(code)):
            code = "http_error"
        return _Refused(code)

    async def _request(
        self, method: str, path: str, *, timeout_s: float | None = None, **kwargs: Any
    ) -> Any:
        import httpx

        client = await self._http()
        if timeout_s is not None:
            kwargs["timeout"] = httpx.Timeout(timeout_s, connect=10.0)
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            # The type only: a transport message can quote the request.
            raise SandboxError(
                f"the sandbox broker did not answer ({type(exc).__name__})"
            ) from None
        if response.status_code >= 300:
            raise self._refusal(response)
        return response

    async def _destroy(self, sandbox_id: str) -> None:
        await self._request("DELETE", f"/v1/sandboxes/{sandbox_id}")

    async def _delete_checkpoint(self, name: str) -> None:
        await self._request("DELETE", f"/v1/checkpoints/{name}")

    async def _quietly(self, work: Coroutine[Any, Any, None], what: str) -> None:
        """Best-effort cleanup. What it leaves behind is found by listing the sandbox project."""
        try:
            await work
        except Exception as exc:
            logger.warning("could not remove %s: %s", what, exc)

    @staticmethod
    def _field(response: Any, name: str) -> Any:
        """One field of a JSON object answer, or ``None`` when the answer is not one."""
        try:
            value = response.json()
        except ValueError:
            return None
        return value.get(name) if isinstance(value, dict) else None

    async def _usage(self, sandbox_id: str) -> int:
        response = await self._request("GET", f"/v1/sandboxes/{sandbox_id}/usage")
        used = self._field(response, "bytes")
        if not isinstance(used, int) or used < 0:
            raise SandboxError("the cloud sandbox did not report the workspace's size")
        return used

    # -- boot and claim ---------------------------------------------------------------------

    async def _boot(self, key: str) -> None:
        """Attach a sandbox to ``key``. One boot per key per process; others wait for it."""
        task = self._boots.get(key)
        if task is None or task.done():
            task = self._spawn(self._boot_once(key))
            self._boots[key] = task
            task.add_done_callback(
                lambda done: self._boots.pop(key, None) if self._boots.get(key) is done else None
            )
        await asyncio.shield(task)

    async def _boot_once(self, key: str) -> None:
        record = await self._store.get(key)
        if record is None or record.sandbox_id is not None:
            return
        checkpoint = record.checkpoint_name
        try:
            response = await self._request(
                "POST", "/v1/sandboxes",
                json={"checkpoint": checkpoint} if checkpoint else {},
                timeout_s=self._boot_timeout_s,
            )
        except _Refused as exc:
            if exc.code != "checkpoint_not_found":
                raise SandboxError(f"could not start a cloud sandbox ({exc.code})") from None
            current = await self._store.get(key)
            if current is None or current.checkpoint_name != checkpoint:
                return  # saved again meanwhile: the caller looks and boots the newer one
            raise SandboxError(
                "this workspace's saved copy no longer exists, so it cannot be restored. "
                "Nothing was started; destroy the workspace to begin again."
            ) from None
        sandbox_id = self._field(response, "id")
        if not isinstance(sandbox_id, str) or not _SANDBOX_ID.fullmatch(sandbox_id):
            raise SandboxError("the sandbox broker answered without a sandbox id")
        attached = False
        try:
            attached = await self._store.attach(
                key, sandbox_id, expected_checkpoint=checkpoint, at=self._now()
            )
        finally:
            if not attached:
                # Another worker attached one first, or the key is gone: this one has no owner.
                await self._quietly(self._destroy(sandbox_id), "an unused sandbox")

    async def _claim(self, key: str, lease_s: float) -> str:
        """A running sandbox for ``key``, counted as in use. Waits out a save in progress."""
        deadline = time.monotonic() + self._save_wait_s
        boots = 0
        while True:
            record = await self._store.get(key)
            if record is None:
                raise SandboxError(f"unknown sandbox task {key!r}; call create_task first")
            if record.lost and await self._store.take_loss(key):
                raise _lost()
            now = self._now()
            saving = record.saving_until is not None and record.saving_until > now
            if not saving:
                if record.sandbox_id is None:
                    if boots >= _BOOT_ATTEMPTS:
                        raise SandboxError("could not keep a cloud sandbox running; try again")
                    boots += 1
                    await self._boot(key)
                    continue
                if await self._store.begin_use(
                    key, record.sandbox_id, at=now, until=now + timedelta(seconds=lease_s)
                ):
                    return record.sandbox_id
            if time.monotonic() > deadline:
                raise SandboxError("the workspace is still being saved; try again shortly")
            await asyncio.sleep(self._poll_s)

    @asynccontextmanager
    async def _sandbox(self, key: str, lease_s: float = _CALL_LEASE_S) -> AsyncIterator[str]:
        sandbox_id = await self._claim(key, lease_s)
        try:
            yield sandbox_id
        except _Refused as exc:
            if exc.code != "sandbox_not_found":
                raise
            await self._store.detach(key, sandbox_id)
            raise _lost() from None
        finally:
            try:
                await self._store.end_use(key, sandbox_id)
            except Exception:
                # The claim expires by itself; the call's own result matters more.
                logger.warning("could not release a cloud sandbox claim", exc_info=True)

    # -- tasks ------------------------------------------------------------------------------

    async def create_task(
        self, *, user_id: Any | None = None, session_id: str | None = None, isolate: bool = False,
        workspace_root: str | None = None, origin: str | None = None,
    ) -> SandboxHandle:
        # The routing arguments mean something to a paired desktop only (see `ports/coding.py`);
        # accepted and ignored here, as the other server-side runners do.
        key = f"task-{uuid.uuid4().hex}"
        await self._store.ensure(key, "task", at=self._now())
        return SandboxHandle(task_id=key)

    async def destroy(self, handle: SandboxHandle) -> None:
        await asyncio.shield(self._spawn(self._discard(handle.task_id)))

    # -- commands ---------------------------------------------------------------------------

    @staticmethod
    async def _lines(response: Any) -> AsyncIterator[dict]:
        """The broker's NDJSON. Split on ``\\n`` only: ``aiter_lines`` also splits on U+2028 and
        other characters that JSON leaves unescaped inside a string."""
        buffer = bytearray()
        async for chunk in response.aiter_bytes():
            buffer += chunk
            while (end := buffer.find(b"\n")) >= 0:
                line = bytes(buffer[:end])
                del buffer[:end + 1]
                if line:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        yield message
            if len(buffer) > _MAX_LINE_BYTES:
                raise ValueError("an output line is longer than the broker may send")

    async def _command_failed(self, key: str, sandbox_id: str, code: str) -> tuple[str, str]:
        """An ``error`` line: raise when the command certainly did not run, else "unknown"."""
        if code == "fence_missing":
            await self._store.detach(key, sandbox_id)
            raise SandboxError(
                "the command did not run: the sandbox's network fence was missing, so it was "
                "discarded. Changes since the last save are gone; try again."
            )
        if code in ("sandbox_unsafe", "sandbox_not_found"):
            await self._store.detach(key, sandbox_id)
            return "unknown", f"{code}: the sandbox was discarded with its unsaved changes"
        return "unknown", code if _ERROR_CODE.fullmatch(code) else "error"

    async def _exec(
        self, key: str, command: Sequence[str], timeout_s: float
    ) -> AsyncIterator[tuple[str, Any]]:
        """``("stdout"|"stderr", text)`` as it arrives, then exactly one ``("exit", code)`` or
        ``("unknown", reason)``. The sandbox is released before that last item."""
        import httpx

        seconds = max(1, min(_MAX_COMMAND_S, math.ceil(timeout_s)))
        final: tuple[str, Any] | None = None
        note = ""
        async with self._sandbox(key, lease_s=seconds + 180.0) as sandbox_id:
            client = await self._http()
            try:
                async with client.stream(
                    "POST", f"/v1/sandboxes/{sandbox_id}/exec",
                    json={"command": list(command), "timeout_s": seconds},
                    timeout=httpx.Timeout(
                        self._request_timeout_s, connect=10.0, read=seconds + 90.0
                    ),
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        refusal = self._refusal(response)
                        if refusal.code == "sandbox_not_found":
                            await self._store.detach(key, sandbox_id)
                            raise _lost("The command did not run. ")
                        if response.status_code < 500:
                            raise SandboxError(
                                f"the command did not run: the sandbox refused it ({refusal.code})"
                            )
                        final = ("unknown", refusal.code)
                    else:
                        async for message in self._lines(response):
                            kind = message.get("type")
                            if kind in ("stdout", "stderr"):
                                yield kind, str(message.get("data") or "")
                            elif kind == "exit" and isinstance(message.get("exit_code"), int):
                                final = ("exit", message["exit_code"])
                                if message.get("timed_out"):
                                    note = f"command timed out after {seconds}s"
                                elif message.get("truncated"):
                                    note = "[aura] output truncated: its middle was left out"
                                break
                            elif kind == "error":
                                final = await self._command_failed(
                                    key, sandbox_id, str(message.get("error") or "error")
                                )
                                break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                raise SandboxError(
                    "the command did not run: the sandbox broker could not be reached"
                ) from None
            except (httpx.HTTPError, ValueError) as exc:
                if final is None:
                    final = ("unknown", type(exc).__name__)
        if note:
            yield "stderr", note
        yield final or ("unknown", "the connection ended without an exit status")

    async def run_streaming(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> AsyncIterator[SandboxOutputEvent]:
        async for kind, value in self._exec(handle.task_id, command, timeout_s):
            if kind in ("stdout", "stderr"):
                yield SandboxOutputEvent(kind=kind, data=value)
            elif kind == "exit":
                yield SandboxOutputEvent(kind="done", exit_code=value)
            else:
                yield SandboxOutputEvent(
                    kind="stderr",
                    data=f"\n[aura] the command's outcome is unknown ({value}): it may or may not "
                         "have run, and was not sent again. Check the workspace before "
                         "repeating it.",
                )
                yield SandboxOutputEvent(kind="done", exit_code=-1)

    async def run(
        self, handle: SandboxHandle, command: Sequence[str], *, timeout_s: float
    ) -> SandboxResult:
        """Raises :class:`SandboxOutcomeUnknown` rather than inventing an exit code, so that no
        caller can mistake "we do not know" for "it failed"."""
        output: dict[str, list[str]] = {"stdout": [], "stderr": []}
        kept = {"stdout": 0, "stderr": 0}
        outcome: tuple[str, Any] = ("unknown", "nothing was reported")
        async for kind, value in self._exec(handle.task_id, command, timeout_s):
            if kind in output:
                part = value[:_MAX_OUTPUT_CHARS - kept[kind]]
                output[kind].append(part)
                kept[kind] += len(part)
            else:
                outcome = (kind, value)
        if outcome[0] == "exit":
            return SandboxResult(
                stdout="".join(output["stdout"]), stderr="".join(output["stderr"]),
                exit_code=outcome[1],
            )
        tail = "".join(output["stderr"])[-500:]
        raise SandboxOutcomeUnknown(
            f"the command's outcome is unknown ({outcome[1]}): it may or may not have run, and "
            "was not sent again. Check the workspace before repeating it."
            + (f" Last output: {tail}" if tail else "")
        )

    # -- files ------------------------------------------------------------------------------

    async def _put(self, sandbox_id: str, relative: str, data: bytes) -> None:
        await self._request(
            "PUT", f"/v1/sandboxes/{sandbox_id}/files", params={"path": relative}, content=data
        )

    async def _get(self, sandbox_id: str, relative: str, label: str, limit: int) -> bytes:
        import httpx

        client = await self._http()
        too_large = SandboxError(f"{label} is too large to read here (limit {limit} bytes)")
        try:
            async with client.stream(
                "GET", f"/v1/sandboxes/{sandbox_id}/files", params={"path": relative}
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    refusal = self._refusal(response)
                    if refusal.code == "file_not_found":
                        raise _NoSuchFile(f"{label} is not a file in the sandbox workspace")
                    if refusal.code == "path_refused":
                        raise SandboxError(f"path {label!r} escapes the sandbox workspace")
                    if refusal.code == "file_too_large":
                        raise too_large
                    raise refusal
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > limit:
                        raise too_large
                return bytes(data)
        except httpx.HTTPError as exc:
            raise SandboxError(
                f"the sandbox broker did not answer ({type(exc).__name__})"
            ) from None

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        relative = workspace_relative(path)
        data = content.encode("utf-8")
        async with self._sandbox(handle.task_id) as sandbox_id:
            try:
                await self._put(sandbox_id, relative, data)
            except _Refused as exc:
                if exc.code == "sandbox_not_found":
                    raise
                raise SandboxError(f"could not write {path[:120]}: {exc.code}") from None

    async def _read(self, handle: SandboxHandle, path: str, limit: int) -> bytes:
        relative = workspace_relative(path)
        async with self._sandbox(handle.task_id) as sandbox_id:
            return await self._get(sandbox_id, relative, path, limit)

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        return decode_workspace_text(await self._read(handle, path, _MAX_EDIT_BYTES), path)

    async def read_file_bytes(self, handle: SandboxHandle, path: str) -> bytes:
        """The file's exact bytes, for callers that must not assume text (a PDF)."""
        return await self._read(handle, path, _MAX_READ_BYTES)

    async def write_files(self, handle: SandboxHandle, operations: Sequence[FileWriteOp]) -> None:
        """Validate every operation, then write them all; a write that fails part-way puts back
        what was there (``ports/coding.py``)."""
        targets: list[tuple[FileWriteOp, str]] = []
        for op in operations:
            if op.content is None and (op.find is None or op.replace is None):
                raise SandboxError(
                    f"operation for {op.path} must set either content, or both find and replace"
                )
            targets.append((op, workspace_relative(op.path)))

        async with self._sandbox(handle.task_id) as sandbox_id:
            plan: list[tuple[str, str, bytes, bytes | None]] = []
            for op, relative in targets:
                try:
                    previous: bytes | None = await self._get(
                        sandbox_id, relative, op.path, _MAX_READ_BYTES
                    )
                except _NoSuchFile:
                    previous = None
                if op.content is not None:
                    new = op.content.encode("utf-8")
                else:
                    if previous is None:
                        raise SandboxError(f"{op.path} does not exist")
                    current = decode_workspace_text(previous, op.path)
                    occurrences = current.count(op.find)
                    if occurrences != 1:
                        raise SandboxError(
                            f"expected exactly one occurrence of the given text in {op.path}, "
                            f"found {occurrences}"
                        )
                    new = current.replace(op.find, op.replace, 1).encode("utf-8")
                plan.append((relative, op.path, new, previous))

            written: list[tuple[str, str, bytes | None]] = []
            for relative, label, new, previous in plan:
                try:
                    await self._put(sandbox_id, relative, new)
                except SandboxError as exc:
                    code = exc.code if isinstance(exc, _Refused) else None
                    if code == "sandbox_not_found":
                        raise
                    # Unless it certainly changed nothing, this file is restored too.
                    if code not in _NOTHING_CHANGED:
                        written.append((relative, label, previous))
                    note = await self._restore(handle, sandbox_id, written)
                    raise SandboxError(f"could not write {label[:120]}: {code or exc}{note}") from None
                written.append((relative, label, previous))

    async def _restore(
        self, handle: SandboxHandle, sandbox_id: str,
        written: Sequence[tuple[str, str, bytes | None]],
    ) -> str:
        stuck: list[str] = []
        for relative, label, previous in reversed(written):
            try:
                if previous is None:
                    removed = await self.run(handle, ["rm", "-f", "--", relative], timeout_s=30.0)
                    if removed.exit_code != 0:
                        stuck.append(label)
                else:
                    await self._put(sandbox_id, relative, previous)
            except Exception:
                stuck.append(label)
        if stuck:
            return f"; could not restore {', '.join(stuck)} -- check them"
        return "; the files already written in this call were restored" if written else ""

    @staticmethod
    def _entry_path(path: str, verb: str) -> str:
        relative = workspace_relative(path)
        if any(part.lower().rstrip(". ") == ".git" for part in relative.split("/")):
            raise SandboxError(
                f"{verb} of {path!r} is refused: it is inside a .git directory -- use git "
                "commands to change a repository's own storage"
            )
        return relative

    async def delete_file(self, handle: SandboxHandle, path: str) -> None:
        relative = self._entry_path(path, "delete")
        result = await self.run(
            handle, ["sh", "-c", _DELETE, "aura-delete", relative], timeout_s=30.0
        )
        if result.exit_code != 0:
            raise self._entry_error(result, "delete", path, path)

    async def rename_file(self, handle: SandboxHandle, path: str, new_path: str) -> None:
        source = self._entry_path(path, "rename")
        target = self._entry_path(new_path, "rename")
        result = await self.run(
            handle, ["sh", "-c", _RENAME, "aura-rename", source, target], timeout_s=30.0
        )
        if result.exit_code != 0:
            raise self._entry_error(result, "rename", path, new_path)

    @staticmethod
    def _entry_error(
        result: SandboxResult, verb: str, path: str, new_path: str
    ) -> SandboxError:
        reasons = {
            91: f"path {new_path if verb == 'rename' else path!r} or its source escapes the "
                "sandbox workspace",
            92: f"{path} does not exist in the sandbox workspace",
            93: f"{verb} of {path!r} is refused: it is inside a .git directory",
            94: f"{path} is a directory -- delete_file removes one file at a time; use "
                "run_command to remove a directory",
            95: f"{new_path} already exists -- rename never replaces an existing entry",
            96: f"cannot move {path} into itself ({new_path})",
        }
        return SandboxError(
            reasons.get(result.exit_code)
            or f"could not {verb} {path}: {result.stderr.strip()[-300:]}"
        )

    # -- durable workspaces -----------------------------------------------------------------

    async def open_workspace(self, key: str) -> SandboxHandle:
        """Record the key. No sandbox starts until something needs one, so listing, pausing and
        removing workspaces cost nothing."""
        await self._store.ensure(key, "workspace", at=self._now())
        return SandboxHandle(task_id=key)

    async def pause_workspace(self, handle: SandboxHandle) -> None:
        """Save a checkpoint, then destroy the sandbox. Raises, leaving the sandbox running, when
        it cannot be saved or a command is running in it."""
        await asyncio.shield(self._spawn(self._save(handle.task_id)))

    async def _save(self, key: str) -> None:
        record = await self._store.get(key)
        if record is None or record.sandbox_id is None:
            return
        sandbox_id = record.sandbox_id
        # A new name every time: the last good checkpoint stays until the new one is confirmed.
        name = f"ws-{uuid.uuid4().hex}"
        now = self._now()
        if not await self._store.begin_save(
            key, sandbox_id, name, at=now,
            until=now + timedelta(seconds=self._checkpoint_timeout_s + 180.0),
        ):
            await self._wait_for_other_save(key, sandbox_id)
            return
        record = await self._store.get(key)
        previous = record.checkpoint_name if record else None
        try:
            used: int | None = await self._usage(sandbox_id)
        except SandboxError:
            used = None  # the size is a nicety; the save is not
        try:
            await self._request(
                "POST", f"/v1/sandboxes/{sandbox_id}/checkpoint", json={"name": name},
                timeout_s=self._checkpoint_timeout_s,
            )
        except BaseException as exc:
            if isinstance(exc, _Refused) and exc.code == "sandbox_not_found":
                # There is nothing left to save, and whoever asked for the pause (often another
                # request making room) is not who lost the work: the key's next user is told.
                logger.warning("a cloud sandbox vanished before it could be saved")
                await self._store.detach(key, sandbox_id, lost=True)
                return
            await self._store.release_save(key, name)
            await self._quietly(self._delete_checkpoint(name), "an unfinished checkpoint")
            if not isinstance(exc, SandboxError):
                raise
            raise SandboxError(
                f"the workspace was not saved, and its sandbox was left running: {exc}"
            ) from None
        if not await self._store.commit_save(key, name, used_bytes=used):
            await self._quietly(self._delete_checkpoint(name), "an unowned checkpoint")
            return
        if previous and previous != name:
            await self._quietly(self._delete_checkpoint(previous), "a replaced checkpoint")
        try:
            await self._destroy(sandbox_id)
        except SandboxError as exc:
            await self._store.release_save(key, name)
            raise SandboxError(
                f"the workspace was saved, but its sandbox could not be removed: {exc}"
            ) from None
        await self._store.detach(key, sandbox_id)

    async def _wait_for_other_save(self, key: str, sandbox_id: str) -> None:
        """After a refused claim: fine once someone else's save finished, an error if the sandbox
        is simply busy."""
        deadline = time.monotonic() + self._save_wait_s
        while True:
            record = await self._store.get(key)
            if record is None or record.sandbox_id != sandbox_id:
                return
            if record.saving_until is None or record.saving_until <= self._now():
                raise SandboxError(
                    "a command is still running in this workspace, so it was not saved"
                )
            if time.monotonic() > deadline:
                raise SandboxError("the workspace is still being saved; try again shortly")
            await asyncio.sleep(self._poll_s)

    async def destroy_workspace(self, handle: SandboxHandle) -> None:
        await asyncio.shield(self._spawn(self._discard(handle.task_id)))

    async def _discard(self, key: str) -> None:
        """Remove the sandbox and checkpoints, then the record. A failure keeps the record, so
        that a retry still knows what to remove."""
        deadline = time.monotonic() + self._save_wait_s
        for _ in range(_BOOT_ATTEMPTS + 2):
            while True:
                record = await self._store.get(key)
                if record is None:
                    return
                if record.saving_until is None or record.saving_until <= self._now():
                    break
                if time.monotonic() > deadline:
                    raise SandboxError("the workspace is still being saved; try again shortly")
                await asyncio.sleep(self._poll_s)
            if record.sandbox_id is not None:
                await self._destroy(record.sandbox_id)
                await self._store.detach(key, record.sandbox_id)
            for name in {record.checkpoint_name, record.saving_name} - {None}:
                await self._delete_checkpoint(name)
            if await self._store.delete(
                key, sandbox_id=None, checkpoint_name=record.checkpoint_name
            ):
                return
        raise SandboxError("could not remove the workspace: it kept changing; try again")

    async def workspace_keys(self) -> Sequence[str]:
        return await self._store.list_keys(kind="workspace")

    async def workspace_disk_usage_mb(self, handle: SandboxHandle) -> float:
        """Measured while a sandbox runs, else the last measurement. Never boots one."""
        key = handle.task_id
        record = await self._store.get(key)
        if record is None:
            return 0.0
        saving = record.saving_until is not None and record.saving_until > self._now()
        if record.sandbox_id is not None and not saving:
            try:
                used = await self._usage(record.sandbox_id)
            except _Refused as exc:
                # A vanished sandbox stays on record: this runs before every write, and
                # forgetting it here would let that write go quietly to the older saved copy.
                if exc.code != "sandbox_not_found":
                    raise
            else:
                await self._store.record_usage(key, used)
                return used / (1024 * 1024)
        return (record.used_bytes or 0) / (1024 * 1024)

    async def workspace_stats(self, handle: SandboxHandle) -> dict[str, Any] | None:
        """When the running sandbox started; the broker reports no CPU or memory use."""
        record = await self._store.get(handle.task_id)
        if record is None or record.sandbox_id is None or record.booted_at is None:
            return None
        return {
            "started_at": record.booted_at.isoformat(), "cpu_percent": None, "memory_mb": None,
            "memory_limit_mb": _SANDBOX_MEMORY_MB,
        }

    async def workspace_path(self, handle: SandboxHandle) -> str:
        raise SandboxError(
            "a cloud workspace has no path on this server; move a repository with "
            "import_repository and export_branch"
        )

    # -- repository transfer ----------------------------------------------------------------

    async def import_repository(
        self, handle: SandboxHandle, source: Path, destination: str
    ) -> None:
        folder = workspace_relative(destination)
        occupied = f"{folder} already exists and is not empty"
        check = await self.run(
            handle, ["sh", "-c", _EMPTY_CHECK, "aura-import", folder], timeout_s=60.0
        )
        if check.exit_code == 3:
            raise SandboxError(occupied)
        if check.exit_code != 0:
            raise SandboxError(f"could not look at {folder}: {check.stderr.strip()[-300:]}")
        with tempfile.TemporaryDirectory(prefix="aura-upload-") as staging:
            archive = Path(staging) / "checkout.tar.gz"
            await asyncio.to_thread(_pack, source, archive, self._max_transfer_bytes)
            token = uuid.uuid4().hex
            prefix, scratch = f".aura-import-{token}", f".aura-unpack-{token}"
            parts: list[str] = []
            try:
                async with self._sandbox(handle.task_id) as sandbox_id:
                    with archive.open("rb") as stream:
                        while chunk := await asyncio.to_thread(stream.read, self._part_bytes):
                            parts.append(f"{prefix}.{len(parts):04d}")
                            await self._put(sandbox_id, parts[-1], chunk)
                result = await self.run(
                    handle, ["sh", "-c", _UNPACK, "aura-import", folder, scratch, *parts],
                    timeout_s=600.0,
                )
            except SandboxOutcomeUnknown:
                raise
            except BaseException:
                if parts:
                    await self._quietly(
                        self._remove(handle, [scratch, *parts]), "an unfinished upload"
                    )
                raise
        if result.exit_code == 3:
            raise SandboxError(occupied)
        if result.exit_code != 0:
            raise SandboxError(
                f"could not unpack the checkout in the workspace: {result.stderr.strip()[-300:]}"
            )

    async def _remove(self, handle: SandboxHandle, names: Sequence[str]) -> None:
        await self.run(handle, ["rm", "-rf", "--", *names], timeout_s=30.0)

    async def export_branch(
        self, handle: SandboxHandle, directory: str, branch: str, destination: Path
    ) -> BranchExport:
        return await export_branch(self, handle, directory, branch, destination)
