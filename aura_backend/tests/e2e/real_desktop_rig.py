"""A real desktop path, assembled for a test: the production gateway, the real Dart daemon, a real
WebSocket between them.

Nothing on the path between "an Engineer tool was called" and "a byte changed on disk" is replaced:

* **gateway** -- ``aura_service.app`` (the production assembly: the real authenticator, the real
  ``/api/v3/host/agent`` route, the real pairing endpoints), served by a real uvicorn server on a
  loopback port, in *this* process and event loop so a test can also reach ``app.state.aura`` (the
  container, its tool registry and its kernel) without a second HTTP layer.
* **daemon** -- ``aura_hostd`` as a real child process (the compiled binary when
  ``AURA_HOSTD_BIN`` names one, otherwise ``dart run``), with its own config directory and a
  deliberately dirty environment (canary secrets), paired through the real
  ``auth pair`` flow: it prints a code, this rig approves the code at
  ``/api/v3/auth/desktop/pair/approve`` with a real user JWT, the daemon polls and stores the
  device token the gateway issued.
* **transport** -- the daemon's own ``dart:io`` WebSocket client to uvicorn's websocket stack.

What stays a stand-in: storage (the container's in-memory adapters) and the language model (the
scripted planner in the kernel-driven tests). Neither is on the desktop path.

Gated: the test module imports this only when ``AURA_REAL_DESKTOP=1`` (see
``scripts/real_desktop_path.sh``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[3]
HOSTD_DIR = REPO / "aura_hostd"

#: The operator account everything below acts as. ``SUPERADMIN_EMAILS`` makes this address a
#: SuperAdmin; ``AURA_ENGINEER_AGENT_ROLES`` lets that role host a daemon (api/host_agent.may_host).
OPERATOR_EMAIL = "boss@example.com"
OPERATOR_UID = "real-desktop-operator"

#: Planted in the daemon's own environment. None may ever appear in a command's output.
CANARIES = {
    "AURA_CANARY_SECRET": "canary-aura-" + uuid.uuid4().hex,
    "JWT_SECRET": "canary-jwt-" + uuid.uuid4().hex,
    "GITHUB_TOKEN": "canary-gh-" + uuid.uuid4().hex,
    "AWS_SECRET_ACCESS_KEY": "canary-aws-" + uuid.uuid4().hex,
    "OPENAI_API_KEY": "canary-oai-" + uuid.uuid4().hex,
}


def prepare_environment() -> None:
    """Set the gateway's configuration before ``aura_service`` is imported (it builds the app at
    import). Local environment, in-memory stores, no sandbox, an operator who may host a daemon."""
    os.environ.setdefault("AURA_ENVIRONMENT", "local")
    os.environ["AURA_ENGINEER_AGENT_ROLES"] = "SuperAdmin"
    os.environ["SUPERADMIN_EMAILS"] = OPERATOR_EMAIL
    for var in ("ADMIN_EMAILS", "DEVELOPER_EMAILS"):
        os.environ.pop(var, None)
    os.environ.setdefault("JWT_SECRET", "real-desktop-test-secret-" + "x" * 40)
    # A gateway's own process must not be the thing under test for env hygiene: the canaries go
    # only into the daemon's environment (below), never this one.
    for name in CANARIES:
        if name != "JWT_SECRET":
            os.environ.pop(name, None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a live, non-zombie process, read from the process table."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            state = handle.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False
    return state != "Z"


def group_members(pgid: int) -> list[int]:
    """Live pids whose process group is ``pgid`` (the process table, not a tool's claim)."""
    members: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError, IndexError, PermissionError):
            continue
        if fields[0] != "Z" and int(fields[2]) == pgid:
            members.append(int(entry.name))
    return members


def cmdline_pids(needle: str) -> list[int]:
    """Live pids whose command line contains ``needle`` -- finds a marker a test planted."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if needle in raw and pid_alive(int(entry.name)):
            found.append(int(entry.name))
    return found


async def wait_until(predicate, *, timeout: float, interval: float = 0.1, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {what}")
        await asyncio.sleep(interval)


class Gateway:
    """The production app on a real uvicorn server, in this event loop."""

    def __init__(self) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.app: Any = None
        self.container: Any = None
        self._server: Any = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        import uvicorn

        import aura_service
        from aura_core.api import host_agent as host_agent_route
        from aura_core.adapters.host_agent import HostAgentSession

        self.app = aura_service.app
        self.container = self.app.state.aura

        class RecordingSession(HostAgentSession):
            """The real session, plus a tape of every frame the daemon sent. Observation only:
            ``resolve`` still does exactly what it always does."""

            tape: list[dict[str, Any]] = []

            def resolve(self, frame: Mapping[str, Any]) -> None:
                RecordingSession.tape.append(dict(frame))
                super().resolve(frame)

        self.tape = RecordingSession.tape
        self._original_session = host_agent_route.HostAgentSession
        host_agent_route.HostAgentSession = RecordingSession
        self._route_module = host_agent_route

        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="warning", ws="auto",
            lifespan="on", access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._task = asyncio.create_task(self._server.serve())
        await wait_until(lambda: self._server.started or self._task.done(), timeout=60,
                         what="the gateway to start")
        if self._task.done():
            self._task.result()
            raise RuntimeError("the gateway exited during start-up")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=20)
            except Exception:
                self._task.cancel()
        if getattr(self, "_route_module", None) is not None:
            self._route_module.HostAgentSession = self._original_session

    def operator_token(self) -> str:
        """A real access token for the operator, as ``/token`` or ``/web/token`` would issue."""
        import services.jwt_auth as jwt_auth

        pair = jwt_auth.issue_token_pair(
            {"uid": OPERATOR_UID, "email": OPERATOR_EMAIL, "name": "Operator",
             "email_verified": True, "sign_in_provider": "google.com", "role": "SuperAdmin"}
        )
        return pair["access_token"]

    async def online(self) -> bool:
        from aura_core.domain.identifiers import UserId

        return await self.container.host_agent_registry.resolve(UserId(OPERATOR_UID)) is not None


def hostd_command() -> list[str]:
    """How to start the daemon: the compiled binary if one is named, else ``dart run``."""
    binary = os.environ.get("AURA_HOSTD_BIN")
    if binary and Path(binary).is_file():
        return [binary]
    dart = shutil.which("dart")
    if dart is None:
        raise RuntimeError("no daemon: set AURA_HOSTD_BIN or put `dart` on PATH")
    return [dart, "run", str(HOSTD_DIR / "bin" / "aura_hostd.dart")]


class Daemon:
    """One ``aura_hostd`` child process with its own config dir, home and (dirty) environment."""

    def __init__(self, base: Path, gateway: Gateway) -> None:
        self.gateway = gateway
        self.base = base
        self.home = base / "home"
        self.config_home = base / "config"
        self.config_dir = self.config_home / "aura_hostd"
        self.home.mkdir(parents=True, exist_ok=True)
        self.config_home.mkdir(parents=True, exist_ok=True)
        self.process: asyncio.subprocess.Process | None = None
        self.log: list[str] = []
        self._pumps: list[asyncio.Task] = []
        self.control_port: int | None = None
        self.control_token: str | None = None
        self.extra_env: dict[str, str] = {}

    def env(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "LANG": "C.UTF-8",
        }
        # `dart run` needs its package cache and SDK even though HOME is not the real one.
        for keep in ("PUB_CACHE", "DART_SDK", "TMPDIR"):
            if keep in os.environ:
                env[keep] = os.environ[keep]
        if "PUB_CACHE" not in env:
            env["PUB_CACHE"] = str(Path(os.environ.get("HOME", "/root")) / ".pub-cache")
        env.update(CANARIES)
        env.update(self.extra_env)
        return env

    async def _run_cli(self, *args: str, timeout: float = 120) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *hostd_command(), *args, env=self.env(), cwd=str(HOSTD_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode("utf-8", "replace")

    async def grant(self, path: Path, *, trusted: bool) -> None:
        code, out = await self._run_cli("grant", str(path), *(["--trust"] if trusted else []))
        if code != 0:
            raise RuntimeError(f"grant {path} failed: {out}")

    async def pair(self, approve_with: str) -> str:
        """The real pairing flow end to end. Returns the CLI's output."""
        import httpx

        proc = await asyncio.create_subprocess_exec(
            *hostd_command(), "auth", "pair", "--backend-url", self.gateway.url,
            env=self.env(), cwd=str(HOSTD_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        lines: list[str] = []
        code: str | None = None

        async def read_until_code() -> None:
            nonlocal code
            while code is None:
                line = await proc.stdout.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace")
                lines.append(text)
                found = re.search(r"\b([A-Z2-9]{4}-[A-Z2-9]{4})\b", text)
                if found:
                    code = found.group(1)

        await asyncio.wait_for(read_until_code(), timeout=120)
        if code is None:
            raise RuntimeError("the daemon printed no pairing code:\n" + "".join(lines))
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.gateway.url}/api/v3/auth/desktop/pair/approve",
                json={"user_code": code}, headers={"Authorization": f"Bearer {approve_with}"},
            )
        if response.status_code != 200:
            proc.kill()
            raise RuntimeError(f"pair/approve refused: {response.status_code} {response.text}")
        rest, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        lines.append(rest.decode("utf-8", "replace"))
        if proc.returncode != 0:
            raise RuntimeError("pairing failed:\n" + "".join(lines))
        return "".join(lines)

    async def start(self) -> None:
        assert self.process is None or self.process.returncode is not None
        self.log.clear()
        control_file = self.config_dir / "control.json"
        control_file.unlink(missing_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            *hostd_command(), "run", env=self.env(), cwd=str(HOSTD_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

        async def pump(stream) -> None:
            while True:
                line = await stream.readline()
                if not line:
                    return
                self.log.append(line.decode("utf-8", "replace").rstrip("\n"))

        assert self.process.stdout is not None
        self._pumps = [asyncio.create_task(pump(self.process.stdout))]
        await wait_until(control_file.exists, timeout=60, what="the daemon's control.json")
        await asyncio.sleep(0.2)
        info = json.loads(control_file.read_text())
        self.control_port, self.control_token = int(info["port"]), str(info["token"])

    async def wait_connected(self, timeout: float = 60) -> None:
        await wait_until(self.gateway.online, timeout=timeout, what="the daemon to register")

    async def stop(self, *, sig: int = signal.SIGTERM) -> None:
        proc = self.process
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    @property
    def pid(self) -> int:
        assert self.process is not None
        return self.process.pid

    async def control(self, method: str, path: str, *, json_body: Any = None, token: str | None = ...):  # type: ignore[assignment]
        import httpx

        headers = {}
        use = self.control_token if token is ... else token
        if use:
            headers["Authorization"] = f"Bearer {use}"
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.request(
                method, f"http://127.0.0.1:{self.control_port}{path}", json=json_body, headers=headers
            )


def run_git(repo: Path, *args: str) -> str:
    """A real ``git`` in ``repo`` run by the *test* (never by the daemon), to build and to check."""
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(repo), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    done = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout


def make_repo(path: Path, files: Mapping[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", "main")
    run_git(path, "config", "user.name", "Test")
    run_git(path, "config", "user.email", "t@example.com")
    for name, text in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    run_git(path, "add", "-A")
    run_git(path, "commit", "-q", "-m", "initial")
    return path


def python_executable() -> str:
    return sys.executable
