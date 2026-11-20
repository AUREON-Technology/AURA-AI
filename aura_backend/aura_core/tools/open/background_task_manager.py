import os
import shlex
import subprocess
import time
from typing import Any, Dict, List, Optional, Union


class BackgroundTaskManager:
    """Manager for starting, polling, interacting with, and terminating background subprocess tasks safely."""

    def __init__(self) -> None:
        self._tasks: Dict[str, subprocess.Popen] = {}
        self._task_metadata: Dict[str, Dict[str, Any]] = {}

    def start_task(
        self,
        task_id: str,
        command: Union[str, List[str]],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Start a background command without shell=True and register it under task_id."""
        if not task_id:
            return {"started": False, "error": "task_id is required."}

        args = shlex.split(command) if isinstance(command, str) else list(command)
        if not args:
            return {"started": False, "task_id": task_id, "error": "Command cannot be empty."}

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        try:
            proc = subprocess.Popen(
                args,
                shell=False,
                cwd=cwd,
                env=run_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._tasks[task_id] = proc
            self._task_metadata[task_id] = {
                "task_id": task_id,
                "command": args,
                "cwd": cwd,
                "started_at": time.time(),
            }
            return {
                "started": True,
                "task_id": task_id,
                "pid": proc.pid,
                "command": args,
            }
        except Exception as exc:
            return {
                "started": False,
                "task_id": task_id,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def get_status(self, task_id: str) -> Dict[str, Any]:
        """Return current running state and exit code for a registered background task."""
        proc = self._tasks.get(task_id)
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
        results: List[Dict[str, Any]] = []
        for tid in list(self._tasks.keys()):
            results.append(self.get_status(tid))
        return results

    def send_task_input(self, task_id: str, input_text: str) -> Dict[str, Any]:
        """Send text to running background process standard input."""
        proc = self._tasks.get(task_id)
        if proc is None or proc.poll() is not None:
            return {"delivered": False, "task_id": task_id, "error": "Task is not running or not found."}

        try:
            if proc.stdin:
                proc.stdin.write(input_text + "\n")
                proc.stdin.flush()
                return {"delivered": True, "task_id": task_id}
            return {"delivered": False, "task_id": task_id, "error": "Process stdin is closed."}
        except Exception as exc:
            return {"delivered": False, "task_id": task_id, "error": str(exc)}

    def wait_for_task(self, task_id: str, timeout_seconds: float = 10.0) -> Dict[str, Any]:
        """Wait synchronously for task to complete up to timeout."""
        proc = self._tasks.get(task_id)
        if proc is None:
            return {"completed": False, "task_id": task_id, "error": "Task ID not found."}

        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            return {
                "completed": True,
                "task_id": task_id,
                "exit_code": proc.returncode,
                "stdout": stdout.strip() if stdout else "",
                "stderr": stderr.strip() if stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {
                "completed": False,
                "task_id": task_id,
                "error": f"Task timed out after {timeout_seconds} seconds.",
                "running": True,
            }

    def terminate_task(self, task_id: str, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """Terminate a background task cleanly and clean up its pipes."""
        proc = self._tasks.get(task_id)
        if proc is None:
            return {"terminated": False, "task_id": task_id, "error": "Task ID not found."}

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

        stdout_text = ""
        stderr_text = ""
        try:
            out, err = proc.communicate(timeout=1)
            stdout_text = out or ""
            stderr_text = err or ""
        except Exception:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        self._tasks.pop(task_id, None)
        self._task_metadata.pop(task_id, None)
        return {
            "terminated": True,
            "task_id": task_id,
            "exit_code": proc.returncode,
            "stdout": stdout_text.strip(),
            "stderr": stderr_text.strip(),
        }
