"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. ``start_task`` runs
``subprocess.Popen`` in the gateway process with ``os.environ.copy()``, so the child inherits the
gateway's provider keys and JWT secret, and the process table has no owner scoping. Long-running
processes on the user's machine are ``aura_hostd``'s terminal sessions (KAN-34).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import os
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Union

from ._ops_common import (
    PathRefused,
    StreamTail,
    confine,
    forbidden_env_names,
    group_popen_kwargs,
    stop_group,
)
from ._process import scrubbed_env

#: Hard limits: a registry that grows without bound is a process leak waiting to happen.
MAX_RUNNING_TASKS = 16
MAX_TRACKED_TASKS = 64
_MAX_INPUT_CHARS = 64 * 1024
_MAX_WAIT_SECONDS = 600.0


class BackgroundTaskManager:
    """Manager for starting, polling, interacting with, and terminating background subprocess tasks.

    Safety properties (the previous version had none of them):

    * the child gets a **scrubbed environment** (``_process.scrubbed_env``), not the gateway's -- its
      provider keys, JWT secret and database URL are not passed on, and there is no opt-out. ``env``
      adds to that, but a name that looks like a credential or platform setting (``AURA_*``, ``*KEY``,
      ``*TOKEN``, ``*SECRET``, ``DATABASE_URL`` ...) is refused;
    * every task runs in **its own process group**, and stopping it stops the whole group, so a task
      that spawned children leaves nothing behind;
    * stdout/stderr are **drained continuously** into a bounded tail -- an undrained pipe blocks a
      chatty child after ~64 KiB, which looked like a hung task;
    * at most ``MAX_RUNNING_TASKS`` run at once, and ``task_id`` cannot be reused while it runs
      (the old registry silently orphaned the running process);
    * ``base_dir`` confines ``cwd``; ``allowed_executables`` confines what may be started.

    Known limit: this runs *whatever command it is given*, with the caller's privileges and no
    container. It does not (and cannot) stop that command from opening sockets of its own -- a task
    that runs ``curl`` can reach whatever the host network reaches -- so the network policy of
    ``_ops_common.check_url`` does NOT cover it. It is not registered as an agent tool (ADR-036); a caller that
    exposes it must gate it (capability + confirmation) and pass ``allowed_executables``.
    Tasks still running when the manager is dropped are not killed automatically: call
    :meth:`terminate_all`.
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, subprocess.Popen] = {}
        self._task_metadata: Dict[str, Dict[str, Any]] = {}
        self._tails: Dict[str, tuple] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def start_task(
        self,
        task_id: str,
        command: Union[str, List[str]],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        *,
        base_dir: Optional[str] = None,
        allowed_executables: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Start a background command without a shell and register it under ``task_id``."""
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 128 or not task_id.isprintable():
            return {"started": False, "error": "task_id is required (a printable string up to 128 characters)."}

        try:
            if isinstance(command, str):
                args = shlex.split(command)
            elif isinstance(command, (list, tuple)):
                args = list(command)
            else:
                return {"started": False, "task_id": task_id,
                        "error": f"command must be a string or a list of strings (got {type(command).__name__})."}
        except ValueError as exc:
            return {"started": False, "task_id": task_id, "error": f"Command could not be parsed: {exc}"}
        if not args:
            return {"started": False, "task_id": task_id, "error": "Command cannot be empty."}
        if not all(isinstance(a, str) for a in args):
            return {"started": False, "task_id": task_id, "error": "Every command argument must be a string."}
        if any("\x00" in a for a in args):
            return {"started": False, "task_id": task_id, "error": "Command arguments cannot contain NUL bytes."}

        if allowed_executables is not None:
            exe = args[0]
            if os.sep in exe or (os.altsep and os.altsep in exe) or exe not in set(allowed_executables):
                return {"started": False, "task_id": task_id,
                        "error": f"Executable {os.path.basename(exe)!r} is not in the allowed list."}

        try:
            if cwd is not None:
                run_cwd: Optional[str] = str(confine(cwd, base_dir, kind="dir"))
            elif base_dir is not None:
                run_cwd = str(confine(base_dir, None, kind="dir"))
            else:
                run_cwd = None
        except PathRefused as exc:
            return {"started": False, "task_id": task_id, "error": str(exc)}

        if env is not None and not (
            isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            return {"started": False, "task_id": task_id, "error": "env must be a mapping of strings to strings."}
        bad_env = forbidden_env_names(env)
        if bad_env:
            return {"started": False, "task_id": task_id,
                    "error": f"env may not carry credentials or platform settings: {sorted(bad_env)}"}
        # Never the gateway's own environment, and there is no opt-out.
        run_env = scrubbed_env(env)

        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is not None:
                if existing.poll() is None:
                    return {"started": False, "task_id": task_id,
                            "error": "A task with this id is still running; terminate it first."}
                self._forget(task_id)
            running = sum(1 for p in self._tasks.values() if p.poll() is None)
            if running >= MAX_RUNNING_TASKS:
                return {"started": False, "task_id": task_id,
                        "error": f"Too many running tasks (limit {MAX_RUNNING_TASKS})."}
            if len(self._tasks) >= MAX_TRACKED_TASKS:
                return {"started": False, "task_id": task_id,
                        "error": f"Too many tracked tasks (limit {MAX_TRACKED_TASKS}); terminate finished ones."}

            try:
                proc = subprocess.Popen(
                    args,
                    shell=False,
                    cwd=run_cwd,
                    env=run_env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **group_popen_kwargs(),
                )
            except Exception as exc:
                return {"started": False, "task_id": task_id, "error": f"{type(exc).__name__}: {exc}"}

            self._tasks[task_id] = proc
            self._tails[task_id] = (StreamTail(proc.stdout), StreamTail(proc.stderr))
            self._task_metadata[task_id] = {
                "task_id": task_id,
                "command": args,
                "cwd": run_cwd,
                "started_at": time.time(),
            }
        return {"started": True, "task_id": task_id, "pid": proc.pid, "command": args}

    def _forget(self, task_id: str) -> None:
        proc = self._tasks.pop(task_id, None)
        self._task_metadata.pop(task_id, None)
        self._tails.pop(task_id, None)
        if proc is not None:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass

    def _output(self, task_id: str) -> tuple:
        tails = self._tails.get(task_id)
        if not tails:
            return "", ""
        for tail in tails:
            tail.join(2.0)
        return tails[0].text().strip(), tails[1].text().strip()

    # ------------------------------------------------------------------
    def get_status(self, task_id: str) -> Dict[str, Any]:
        """Return current running state and exit code for a registered background task."""
        with self._lock:
            proc = self._tasks.get(task_id) if isinstance(task_id, str) else None
            if proc is None:
                return {"task_id": task_id, "found": False, "running": False, "status": "unknown"}

            code = proc.poll()
            meta = self._task_metadata.get(task_id, {})
            uptime = round(time.time() - meta.get("started_at", time.time()), 2)

            return {
                "task_id": task_id,
                "found": True,
                "pid": proc.pid,
                "running": code is None,
                "exit_code": code,
                "status": "running" if code is None else ("succeeded" if code == 0 else "failed"),
                "uptime_seconds": uptime,
            }

    def list_tasks(self) -> List[Dict[str, Any]]:
        """List all tracked tasks with their status and pid."""
        with self._lock:
            return [self.get_status(tid) for tid in list(self._tasks.keys())]

    def read_output(self, task_id: str) -> Dict[str, Any]:
        """The most recent output of a task (bounded tail), without stopping it."""
        with self._lock:
            if not isinstance(task_id, str) or task_id not in self._tasks:
                return {"found": False, "task_id": task_id, "error": "Task ID not found."}
            tails = self._tails.get(task_id)
            if not tails:
                return {"found": True, "task_id": task_id, "stdout": "", "stderr": ""}
            return {"found": True, "task_id": task_id,
                    "stdout": tails[0].text().strip(), "stderr": tails[1].text().strip()}

    def send_task_input(self, task_id: str, input_text: str) -> Dict[str, Any]:
        """Send text (plus a newline) to a running background process's standard input."""
        with self._lock:
            proc = self._tasks.get(task_id) if isinstance(task_id, str) else None
        if proc is None or proc.poll() is not None:
            return {"delivered": False, "task_id": task_id, "error": "Task is not running or not found."}
        if not isinstance(input_text, str):
            return {"delivered": False, "task_id": task_id, "error": "input_text must be a string."}
        if len(input_text) > _MAX_INPUT_CHARS:
            return {"delivered": False, "task_id": task_id,
                    "error": f"input_text is too long (limit {_MAX_INPUT_CHARS} characters)."}

        try:
            if proc.stdin:
                proc.stdin.write((input_text + "\n").encode("utf-8"))
                proc.stdin.flush()
                return {"delivered": True, "task_id": task_id}
            return {"delivered": False, "task_id": task_id, "error": "Process stdin is closed."}
        except Exception as exc:
            return {"delivered": False, "task_id": task_id, "error": f"{type(exc).__name__}: {exc}"}

    def wait_for_task(self, task_id: str, timeout_seconds: float = 10.0) -> Dict[str, Any]:
        """Wait synchronously for the task to finish, up to ``timeout_seconds`` (at most 10 minutes)."""
        with self._lock:
            proc = self._tasks.get(task_id) if isinstance(task_id, str) else None
        if proc is None:
            return {"completed": False, "task_id": task_id, "error": "Task ID not found."}

        try:
            seconds = float(timeout_seconds)
            if seconds != seconds:
                raise ValueError
        except (TypeError, ValueError):
            seconds = 10.0
        seconds = max(0.0, min(seconds, _MAX_WAIT_SECONDS))

        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return {
                "completed": False,
                "task_id": task_id,
                "error": f"Task timed out after {seconds} seconds.",
                "running": True,
            }
        stdout, stderr = self._output(task_id)
        return {
            "completed": True,
            "task_id": task_id,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    def terminate_task(self, task_id: str, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """Stop a task and everything it started, collect its output, and forget it."""
        with self._lock:
            proc = self._tasks.get(task_id) if isinstance(task_id, str) else None
            if proc is None:
                return {"terminated": False, "task_id": task_id, "error": "Task ID not found."}
            try:
                grace = max(0.0, min(float(timeout_seconds), 60.0))
            except (TypeError, ValueError):
                grace = 5.0
            # stop_group also kills stragglers left in the group after the leader exited.
            stop_group(proc, grace)
            stdout_text, stderr_text = self._output(task_id)
            code = proc.returncode
            self._forget(task_id)
        return {
            "terminated": True,
            "task_id": task_id,
            "exit_code": code,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }

    def terminate_all(self) -> Dict[str, Any]:
        """Stop every tracked task. Call before dropping the manager."""
        with self._lock:
            ids = list(self._tasks.keys())
        results = [self.terminate_task(tid, timeout_seconds=2.0) for tid in ids]
        return {"terminated_count": sum(1 for r in results if r.get("terminated")), "task_ids": ids}
