"""An in-process stand-in for ``aura_sandbox_broker``, behind the same HTTP routes.

An ``httpx`` transport rather than a mock of the runner: the backend's requests are really built,
really parsed and answered in the broker's own wire format (``aura_sandbox_broker/README.md``,
``src/server.ts``). Each "sandbox" is a temporary directory and commands really run in it, so the
runner contract suite exercises behaviour rather than call order. It applies the broker's own
refusals (token, ids, checkpoint names, file paths, command limits); it does not model the network
fence, the unprivileged user or the vendor, which only the broker's own tests and the live proof can.

Faults are injected per route (``broker.fail("exec", ...)``) and an operation can be held open
(``broker.hold("checkpoint")``) so a test can act while it is in flight.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Sequence

import httpx

__all__ = ["BROKER_TOKEN", "BROKER_URL", "FakeSandboxBroker", "Fault"]

BROKER_URL = "http://broker.test"
BROKER_TOKEN = "test-broker-token-0123456789abcdef0123456789"

_CHECKPOINT = re.compile(r"^ws-[0-9a-f]{32}$")
_SANDBOX_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_FILE_LIMIT = 100 * 1024 * 1024


@dataclass
class Fault:
    """What the next request on a route does instead of succeeding."""

    status: int = 502
    code: str = "provider_error"
    #: Raise before anything reaches the broker (it was never reached).
    unreachable: bool = False
    #: Handle the request, then raise, so the caller never learns the answer.
    lose_answer: bool = False
    #: Exec only: answer 200 with exactly these NDJSON objects.
    lines: Sequence[dict] | None = None
    #: Exec only: break the connection after ``lines``.
    cut: bool = False
    #: Answer 200 with this body, whatever the route would have said.
    body: bytes | None = None


def _force_remove(function, path, _excinfo) -> None:
    """Git writes read-only object files, which Windows will not delete until made writable."""
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        pass


def _remove(path: Path) -> None:
    shutil.rmtree(path, onerror=_force_remove)


class _Lines(httpx.AsyncByteStream):
    def __init__(self, lines: Sequence[dict], *, cut: bool = False) -> None:
        self._lines = lines
        self._cut = cut

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for line in self._lines:
            # Exactly as `JSON.stringify` writes it: U+2028 and friends stay raw in the text.
            yield (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
        if self._cut:
            raise httpx.RemoteProtocolError("peer closed connection without sending complete message")


class FakeSandboxBroker(httpx.AsyncBaseTransport):
    def __init__(self, *, token: str = BROKER_TOKEN) -> None:
        self.token = token
        self._root = Path(tempfile.mkdtemp(prefix="aura-fake-broker-"))
        #: Running sandboxes: id -> the directory that stands for its workspace.
        self.sandboxes: dict[str, Path] = {}
        self.checkpoints: dict[str, Path] = {}
        #: Every request that reached the broker, as ``(route, sandbox id or "", body)``.
        self.requests: list[tuple[str, str, bytes]] = []
        self.created: list[dict] = []
        self.destroyed: list[str] = []
        self._faults: dict[str, list[Fault]] = {}
        self._holds: dict[str, asyncio.Event] = {}
        self.entered: dict[str, asyncio.Event] = {}

    # -- test controls ----------------------------------------------------------------------

    def fail(self, route: str, fault: Fault | None = None) -> None:
        self._faults.setdefault(route, []).append(fault or Fault())

    def hold(self, route: str) -> asyncio.Event:
        """Block requests on ``route`` until the returned event is set."""
        release = asyncio.Event()
        self._holds[route] = release
        self.entered[route] = asyncio.Event()
        return release

    def count(self, route: str) -> int:
        return sum(1 for name, _, _ in self.requests if name == route)

    def vanish(self, sandbox_id: str) -> None:
        """The vendor destroyed the sandbox on its own (idle timeout, a crash)."""
        _remove(self.sandboxes.pop(sandbox_id))

    def cleanup(self) -> None:
        _remove(self._root)

    # -- the wire ---------------------------------------------------------------------------

    @staticmethod
    def _error(status: int, code: str) -> httpx.Response:
        return httpx.Response(status, json={"error": code})

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        parts = request.url.path.strip("/").split("/")
        route, sandbox_id = self._route(request.method, parts)
        queued = self._faults.get(route)
        fault = queued.pop(0) if queued else None
        if fault is not None and fault.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return self._error(401, "unauthorized")
        self.requests.append((route, sandbox_id, body))
        if route in self._holds:
            self.entered[route].set()
            await self._holds[route].wait()
        if fault is not None and fault.body is not None:
            return httpx.Response(200, content=fault.body)
        if fault is not None and fault.lines is not None:
            return httpx.Response(200, stream=_Lines(fault.lines, cut=fault.cut))
        if fault is not None and not fault.lose_answer:
            return self._error(fault.status, fault.code)
        response = await self._answer(request, route, sandbox_id, parts, body)
        if fault is not None and fault.lose_answer:
            raise httpx.ReadTimeout("no answer", request=request)
        return response

    @staticmethod
    def _route(method: str, parts: list[str]) -> tuple[str, str]:
        if parts[:2] == ["v1", "checkpoints"]:
            return ("delete_checkpoint" if method == "DELETE" else "checkpoints"), ""
        if parts[:2] != ["v1", "sandboxes"]:
            return "unknown", ""
        if len(parts) == 2:
            return ("create" if method == "POST" else "list"), ""
        sandbox_id = parts[2]
        if len(parts) == 3:
            return "destroy", sandbox_id
        action = parts[3]
        if action == "exec":
            return ("cancel" if len(parts) == 6 else "exec"), sandbox_id
        if action == "files":
            return ("put" if method == "PUT" else "get"), sandbox_id
        return action, sandbox_id

    async def _answer(
        self, request: httpx.Request, route: str, sandbox_id: str, parts: list[str], body: bytes
    ) -> httpx.Response:
        if route == "create":
            return self._create(json.loads(body or b"{}"))
        if route == "list":
            return httpx.Response(200, json={"sandboxes": [
                {"id": i, "status": "RUNNING", "createdAt": "2026-11-25T00:00:00Z"}
                for i in self.sandboxes]})
        if route == "checkpoints":
            return httpx.Response(200, json={"checkpoints": [
                {"name": n, "createdAt": "2026-11-25T00:00:00Z"} for n in self.checkpoints]})
        if route == "delete_checkpoint":
            name = parts[2]
            if not _CHECKPOINT.match(name):
                return self._error(400, "invalid_request")
            path = self.checkpoints.pop(name, None)
            if path is not None:
                _remove(path)
            return httpx.Response(204)
        if not _SANDBOX_ID.match(sandbox_id):
            return self._error(404, "sandbox_not_found")
        if route == "destroy":
            path = self.sandboxes.pop(sandbox_id, None)
            if path is not None:
                _remove(path)
                self.destroyed.append(sandbox_id)
            return httpx.Response(204)
        workspace = self.sandboxes.get(sandbox_id)
        if workspace is None:
            return self._error(404, "sandbox_not_found")
        if route == "exec":
            return await self._exec(workspace, json.loads(body))
        if route == "checkpoint":
            name = json.loads(body).get("name", "")
            if not _CHECKPOINT.match(name):
                return self._error(400, "invalid_request")
            target = self._root / f"checkpoint-{uuid.uuid4().hex}"
            shutil.copytree(workspace, target, symlinks=True)
            self.checkpoints[name] = target
            return httpx.Response(204)
        if route == "usage":
            total = sum(
                p.stat().st_size for p in workspace.rglob("*") if p.is_file() and not p.is_symlink()
            )
            return httpx.Response(200, json={"bytes": total})
        if route in ("put", "get"):
            return self._file(workspace, route, request.url.params.get("path", ""), body)
        return self._error(404, "not_found")

    def _create(self, body: dict) -> httpx.Response:
        if set(body) - {"checkpoint"}:
            return self._error(400, "invalid_request")
        name = body.get("checkpoint")
        if name is not None and not _CHECKPOINT.match(name):
            return self._error(400, "invalid_request")
        if name is not None and name not in self.checkpoints:
            return self._error(404, "checkpoint_not_found")
        sandbox_id = str(uuid.uuid4())
        workspace = self._root / sandbox_id
        if name is None:
            workspace.mkdir()
        else:
            shutil.copytree(self.checkpoints[name], workspace, symlinks=True)
        self.sandboxes[sandbox_id] = workspace
        self.created.append({"id": sandbox_id, **body})
        return httpx.Response(201, json={"id": sandbox_id})

    async def _exec(self, workspace: Path, body: dict) -> httpx.Response:
        command, timeout_s = body.get("command"), body.get("timeout_s")
        valid = (
            set(body) == {"command", "timeout_s"}
            and isinstance(command, list) and 0 < len(command) <= 1000
            and all(isinstance(p, str) and "\0" not in p for p in command)
            and sum(len(p.encode()) for p in command) <= 64 * 1024
            and type(timeout_s) is int and 1 <= timeout_s <= 900
        )
        if not valid:
            return self._error(400, "invalid_request")
        lines: list[dict] = [{"type": "started", "command_id": uuid.uuid4().hex}]
        timed_out = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            lines += [{"type": "stderr", "data": str(exc)},
                      {"type": "exit", "exit_code": 127, "timed_out": False, "truncated": False}]
            return httpx.Response(200, stream=_Lines(lines))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
            exit_code = process.returncode or 0
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            stdout, stderr, exit_code, timed_out = b"", b"", 124, True
        for kind, data in (("stdout", stdout), ("stderr", stderr)):
            if data:
                lines.append({"type": kind, "data": data.decode("utf-8", errors="replace")})
        lines.append(
            {"type": "exit", "exit_code": exit_code, "timed_out": timed_out, "truncated": False})
        return httpx.Response(200, stream=_Lines(lines))

    def _file(self, workspace: Path, route: str, path: str, body: bytes) -> httpx.Response:
        if (
            not path or "\0" in path or path.startswith("/")
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            return self._error(400, "invalid_request")
        target = (workspace / path).resolve()
        if workspace.resolve() not in target.parents:
            return self._error(400, "path_refused")
        if route == "get":
            if not target.is_file():
                return self._error(404, "file_not_found")
            if target.stat().st_size > _FILE_LIMIT:
                return self._error(413, "file_too_large")
            return httpx.Response(
                200, content=target.read_bytes(),
                headers={"content-type": "application/octet-stream"})
        if len(body) > _FILE_LIMIT:
            return self._error(413, "file_too_large")
        if target.is_dir():
            return self._error(400, "path_refused")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        except OSError:
            return self._error(502, "provider_error")
        return httpx.Response(204)
