"""Deferred (ADR-036) -- deliberately NOT registered as a tool. Long-running processes, a
process-global PID table and ``shell=True`` cannot be made safe in the gateway. The home for this
is a ``dev_server.*`` RPC in ``aura_hostd`` (not built; needs its own change and Host Daemon CI).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from ._ops_common import (
    CLI_ENV_PASSTHROUGH,
    PathRefused,
    StreamTail,
    UrlRefused,
    confine,
    forbidden_env_names,
    group_popen_kwargs,
    open_owned_local,
    stop_group,
)
from ._process import scrubbed_env

# ---------------------------------------------------------------------------
# Allowlist of permitted executable names for start_server.
# Only these binaries may be launched; arbitrary shell strings are rejected.
# Add entries here as new dev-server runtimes are approved.
# ---------------------------------------------------------------------------
_ALLOWED_EXECUTABLES: frozenset = frozenset({
    # Python / ASGI / WSGI runners
    "uvicorn",
    "gunicorn",
    "python",
    "python3",
    "python3.11",
    "python3.12",
    "python3.13",
    # Node / JS runtimes
    "node",
    "npm",
    "npx",
    "pnpm",
    "yarn",
    # Vite / Next / common front-end dev servers (called through node)
    "vite",
    "next",
    # Other common runners
    "flask",
    "fastapi",
    "daphne",
    "hypercorn",
    "granian",
    # Django management
    "django-admin",
    "manage.py",
})

#: ``python -m <module>`` is only allowed for modules that are servers. ``-m pip``, ``-m venv``,
#: ``-m pytest`` and friends run arbitrary code or change the environment.
_ALLOWED_PY_MODULES: frozenset = frozenset({
    "http.server", "uvicorn", "gunicorn", "flask", "fastapi", "hypercorn", "daphne", "granian",
    "django", "streamlit",
})
#: Sub-commands of the package runners that start a project's own dev/serve script.
_ALLOWED_PM_COMMANDS: frozenset = frozenset({"run", "run-script", "start", "dev", "serve", "preview"})
_NODE_EVAL_FLAGS = ("-e", "--eval", "-p", "--print", "-r", "--require", "--import", "--loader",
                    "--experimental-loader", "--input-type")
_PY_COMMAND_FLAG = re.compile(r"^-(?![WX])[A-Za-z]*c")
_MAX_RUNNING_SERVERS = 8
_MAX_WAIT_SECONDS = 60.0

#: Loopback ports this module handed out (``find_free_port``) or was told a server it started listens
#: on. The manager only ever *connects* to these: a caller-supplied host or port -- say the gateway's
#: own port -- is refused, so the health probe cannot be turned into a probe of anything else.
_MAX_TRACKED_PORTS = 256
_OWNED_PORTS: "OrderedDict[int, None]" = OrderedDict()
_PORT_LOCK = threading.Lock()


def _own_port(port: int) -> None:
    with _PORT_LOCK:
        _OWNED_PORTS[port] = None
        _OWNED_PORTS.move_to_end(port)
        while len(_OWNED_PORTS) > _MAX_TRACKED_PORTS:
            _OWNED_PORTS.popitem(last=False)


def _release_port(port: Optional[int]) -> None:
    if port is not None:
        with _PORT_LOCK:
            _OWNED_PORTS.pop(port, None)


def _port_owned(port: Any) -> bool:
    with _PORT_LOCK:
        return isinstance(port, int) and not isinstance(port, bool) and port in _OWNED_PORTS


def _refusal_for(tokens: List[str], executable: str) -> Optional[str]:
    """Why ``tokens`` must not run even though the executable is on the allowlist, else None.

    An allowlist of *names* is not a sandbox: ``python -c`` and ``node -e`` run any code, and
    ``npx``/``npm exec`` download and run packages. These forms are refused outright.
    """
    args = tokens[1:]
    if executable.startswith("python"):
        i = 0
        while i < len(args) and args[i].startswith("-") and args[i] != "-":
            tok = args[i]
            if tok == "-m":
                module = args[i + 1] if i + 1 < len(args) else ""
                if module not in _ALLOWED_PY_MODULES:
                    return f"'python -m {module}' is not a permitted dev-server module."
                return None
            if tok.startswith("-m") and len(tok) > 2:
                return "Combined '-m' flags are not accepted; write '-m <module>' separately."
            if _PY_COMMAND_FLAG.match(tok):
                return "Inline code ('python -c') is not permitted; run a script or module."
            i += 1
        return None
    if executable == "node":
        for tok in args:
            if tok.startswith("-") and (tok in _NODE_EVAL_FLAGS or tok.split("=", 1)[0] in _NODE_EVAL_FLAGS):
                return f"'node {tok}' (inline code / preloaded modules) is not permitted."
        return None
    if executable == "npx":
        if "--no-install" not in args and "--no" not in args:
            return "npx may download and run a package; pass --no-install or use a local binary."
        return None
    if executable in ("npm", "pnpm", "yarn"):
        sub = next((a for a in args if not a.startswith("-")), None)
        if sub is None or sub not in _ALLOWED_PM_COMMANDS:
            return f"'{executable} {sub or ''}' is not a permitted dev-server command (run/start/dev/serve/preview)."
        return None
    return None


def _parse_command(command: str) -> Optional[List[str]]:
    """Split a command string safely (no shell) and validate its executable.

    Returns the token list on success, or None if the executable is not in the
    allowlist.  The function never spawns a shell, so shell metacharacters in
    the remaining arguments are inert.
    """
    if not isinstance(command, str) or "\x00" in command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    if not tokens:
        return None

    # The old check compared only basename(tokens[0]), so "/tmp/evil/python" passed. A path form is
    # accepted only when it is the very binary PATH would find for that name.
    first = tokens[0]
    executable = os.path.basename(first)
    if executable not in _ALLOWED_EXECUTABLES:
        return None
    if first != executable:
        found = shutil.which(executable)
        if found is None or os.path.realpath(found) != os.path.realpath(first):
            return None

    return tokens


class DevServerManager:
    """Manager for starting, finding free ports, health-checking, restarting, and stopping local development servers.

    Safety properties:
    - no shell: the command is split with shlex, its executable must be on ``_ALLOWED_EXECUTABLES``
      *and* be the binary PATH resolves (not a same-named file elsewhere), and inline-code forms
      (``python -c``, ``node -e``, ``npx``, ``npm exec``) are refused (ADR-036 "Deferred");
    - the child gets a scrubbed environment, its own process group, and drained, bounded output;
      stopping it stops the whole group (``npm run dev`` is several processes);
    - processes live in an instance-level registry, capped at ``_MAX_RUNNING_SERVERS``;
    - it only ever **connects** to a loopback port it allocated itself (``find_free_port``): a
      caller-supplied host or port, ``file://``, and the gateway's ``/api/v3/`` and ``/health`` paths
      are refused before any socket is opened, and redirects are never followed;
    - an ``env`` passed in may not carry credentials (``AURA_*``, keys, tokens, URLs with secrets).

    Known limit (the reason ADR-036 defers this class): ``npm run dev`` and ``python manage.py ...``
    still execute the *project's own scripts* with the gateway's privileges and no container. The
    long-term home is an ``aura_hostd`` RPC; until then do not hand this class to an agent without a
    capability and a confirmation.
    """

    def __init__(self) -> None:
        # Instance-level registry – avoids cross-caller state leakage.
        self._processes: Dict[int, subprocess.Popen] = {}
        self._tails: Dict[int, tuple] = {}
        self._ports: Dict[int, int] = {}
        self._lock = threading.RLock()

    # Keep a backward-compatible class-level fallback for callers that use the
    # class directly (e.g. DevServerManager.start_server(...)).
    _class_processes: Dict[int, subprocess.Popen] = {}

    def _registry(self) -> Dict[int, subprocess.Popen]:
        return self._processes

    @classmethod
    def _class_registry(cls) -> Dict[int, subprocess.Popen]:
        return cls._class_processes

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def start_server(
        self,
        command: str,
        cwd: Optional[str] = None,
        wait_seconds: float = 1.0,
        *,
        base_dir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        port: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Start a development server process in the background and track its PID.

        ``command`` must begin with an executable in the _ALLOWED_EXECUTABLES
        allowlist.  Shell metacharacters are not interpreted – the string is
        split with shlex and passed directly to the OS.

        ``wait_seconds`` is consumed as a non-blocking poll loop so the calling
        thread is not fully suspended for the whole duration. A server that exits within that
        window is reported as ``started: False`` with its output.

        ``port`` declares the loopback port the server will listen on; it must have been allocated
        with :meth:`find_free_port`. Only a declared port can later be probed
        (:meth:`check_http_health`, :meth:`wait_for_port_ready`).
        """
        tokens = _parse_command(command)
        if tokens is None:
            return {
                "started": False,
                "error": (
                    f"Command rejected: executable not in allowlist or command is malformed. "
                    f"Permitted executables: {sorted(_ALLOWED_EXECUTABLES)}"
                ),
                "command": command if isinstance(command, str) else None,
            }
        reason = _refusal_for(tokens, os.path.basename(tokens[0]))
        if reason:
            return {"started": False, "error": f"Command rejected: {reason}", "command": command}

        try:
            effective_cwd = str(confine(cwd or tempfile.gettempdir(), base_dir, kind="dir"))
        except PathRefused as exc:
            return {"started": False, "error": str(exc), "command": command}

        if env is not None and not (
            isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            return {"started": False, "error": "env must be a mapping of strings to strings.", "command": command}
        bad_env = forbidden_env_names(env)
        if bad_env:
            return {"started": False, "command": command,
                    "error": f"env may not carry credentials or platform settings: {sorted(bad_env)}"}
        if port is not None and not _port_owned(port):
            return {"started": False, "command": command,
                    "error": "port must be one allocated with DevServerManager.find_free_port."}
        # A scrubbed environment: the gateway's keys, JWT secret and database URL never reach the child.
        child_env = scrubbed_env({k: os.environ[k] for k in CLI_ENV_PASSTHROUGH if k in os.environ})
        child_env.update(env or {})

        try:
            wait = max(0.0, min(float(wait_seconds), _MAX_WAIT_SECONDS))
            if wait != wait:
                wait = 1.0
        except (TypeError, ValueError):
            wait = 1.0

        with self._lock:
            running = sum(1 for p in self._processes.values() if p.poll() is None)
            if running >= _MAX_RUNNING_SERVERS:
                return {"started": False, "error": f"Too many running servers (limit {_MAX_RUNNING_SERVERS}).",
                        "command": command}

            try:
                proc = subprocess.Popen(
                    tokens,
                    shell=False,          # never shell=True
                    cwd=effective_cwd,
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **group_popen_kwargs(),
                )
            except Exception as exc:
                return {
                    "started": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "command": command,
                }
            tails = (StreamTail(proc.stdout), StreamTail(proc.stderr))

        # Non-blocking wait: poll in short increments rather than sleeping all
        # at once, so that a process that exits immediately is noticed quickly.
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.05)

        poll = proc.poll()
        if poll is not None:
            stop_group(proc, 0.0)  # reap stragglers the dead leader left behind
            for tail in tails:
                tail.join(2.0)
            return {
                "started": False,
                "pid": proc.pid,
                "exit_code": poll,
                "stdout": tails[0].text().strip(),
                "stderr": tails[1].text().strip(),
            }

        with self._lock:
            self._processes[proc.pid] = proc
            self._tails[proc.pid] = tails
            if port is not None:
                self._ports[proc.pid] = port
        return {
            "started": True,
            "pid": proc.pid,
            "command": command,
            "cwd": effective_cwd,
        }

    def check_process_status(self, pid: int) -> Dict[str, Any]:
        """Check whether a tracked server process is still running."""
        with self._lock:
            proc = self._processes.get(pid)
        if proc is None:
            return {"pid": pid, "tracked": False, "running": False}

        code = proc.poll()
        return {
            "pid": pid,
            "tracked": True,
            "running": code is None,
            "exit_code": code,
        }

    def read_output(self, pid: int) -> Dict[str, Any]:
        """The most recent output of a tracked server (bounded tail)."""
        with self._lock:
            tails = self._tails.get(pid)
        if tails is None:
            return {"pid": pid, "tracked": False, "error": "PID not found in tracked processes."}
        return {"pid": pid, "tracked": True, "stdout": tails[0].text().strip(), "stderr": tails[1].text().strip()}

    def stop_server(self, pid: int, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """Terminate a tracked server and its whole process group, escalating to kill if needed."""
        with self._lock:
            proc = self._processes.get(pid)
            if proc is None:
                return {"stopped": False, "pid": pid, "error": "PID not found in tracked processes."}
            already = proc.poll() is not None
            try:
                grace = max(0.0, min(float(timeout_seconds), 60.0))
            except (TypeError, ValueError):
                grace = 5.0
            code = stop_group(proc, 0.0 if already else grace)
            tails = self._tails.pop(pid, None)
            self._processes.pop(pid, None)
            _release_port(self._ports.pop(pid, None))
        for tail in tails or ():
            tail.join(2.0)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        if already:
            return {"stopped": True, "pid": pid, "already_exited": True, "exit_code": proc.returncode}
        return {"stopped": True, "pid": pid, "exit_code": code}

    def restart_server(self, pid: int, command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        """Stop the tracked server ``pid`` and boot its replacement.

        If ``pid`` is not one this manager started, nothing is started: the old server may still
        hold the port, and a second one would collide with it or hide the first.
        """
        stop_res = self.stop_server(pid)
        if not stop_res.get("stopped", False):
            return {
                "previous_pid": pid,
                "stopped": False,
                "new_server": {"started": False,
                               "error": "The previous server could not be stopped, so no replacement was started: "
                                        + str(stop_res.get("error", "unknown error"))},
            }
        start_res = self.start_server(command, cwd=cwd)
        return {
            "previous_pid": pid,
            "stopped": stop_res.get("stopped", False),
            "new_server": start_res,
        }

    def stop_all(self) -> Dict[str, Any]:
        """Stop every tracked server. Call before dropping the manager."""
        with self._lock:
            pids = list(self._processes.keys())
        return {"stopped_count": sum(1 for p in pids if self.stop_server(p, 2.0).get("stopped")), "pids": pids}

    # ------------------------------------------------------------------
    # Network utilities (stateless – fine as staticmethods)
    # ------------------------------------------------------------------

    @staticmethod
    def check_http_health(url: str, timeout_seconds: float = 3.0) -> Dict[str, Any]:
        """Perform a lightweight HTTP GET health probe against a server this manager allocated a port for.

        Only ``http://127.0.0.1:<port>/`` (or ``localhost``) on a port from :meth:`find_free_port` is
        probed: any other host or port -- the AURA gateway's included -- is refused before a socket is
        opened, the gateway's ``/api/v3/`` and ``/health`` paths are refused on any port, and a redirect
        is reported, never followed. Every failure is a result, never an exception.
        """
        shown = url if isinstance(url, str) and len(url) <= 200 else None
        try:
            with open_owned_local(url, _port_owned, timeout=timeout_seconds) as resp:
                status_code = resp.getcode()
                return {
                    "healthy": isinstance(status_code, int) and 200 <= status_code < 300,
                    "url": shown,
                    "status_code": status_code,
                }
        except UrlRefused as exc:
            return {"healthy": False, "url": shown, "status_code": None, "error": f"URL refused: {exc}"}
        except urllib.error.HTTPError as exc:
            return {
                "healthy": False,
                "url": shown,
                "status_code": exc.code,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "healthy": False,
                "url": shown,
                "status_code": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def find_free_port(start_port: int = 8000, max_tries: int = 50) -> int:
        """Find an unallocated TCP port on localhost.

        Out-of-range arguments are clamped rather than raised. The answer is a hint: another process
        can take the port before the caller binds it. The port is recorded as owned by this module,
        which is what allows the health and readiness probes to connect to it.
        """
        try:
            start = int(start_port)
            tries = int(max_tries)
        except (TypeError, ValueError):
            start, tries = 8000, 50
        start = min(max(start, 1), 65535)
        tries = min(max(tries, 1), 1000)
        for port in range(start, min(start + tries, 65536)):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                except OSError:
                    continue
            _own_port(port)
            return port
        # Fallback OS ephemeral port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            fallback = s.getsockname()[1]
        _own_port(fallback)
        return fallback

    @staticmethod
    def wait_for_port_ready(
        host: str = "127.0.0.1",
        port: int = 8000,
        timeout_seconds: float = 10.0,
    ) -> bool:
        """Poll until a port this module allocated is listening on loopback, or the timeout expires.

        Only ``127.0.0.1`` / ``localhost`` and a port from :meth:`find_free_port` are ever connected
        to; any other host or port returns False *without opening a socket* (connecting to arbitrary
        host:port pairs from the gateway is a port scanner, and the gateway's own port is one of them).
        """
        if host not in ("127.0.0.1", "localhost") or not _port_owned(port):
            return False
        try:
            seconds = max(0.0, min(float(timeout_seconds), 120.0))
        except (TypeError, ValueError):
            return False
        if seconds != seconds:
            return False
        deadline = time.time() + seconds
        while True:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        return True
            except OSError:
                pass
            if time.time() >= deadline:
                return False
            time.sleep(0.2)
