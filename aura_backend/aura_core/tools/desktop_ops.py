"""Desktop-only operations (ADR-036, group C): ``docker_build``, ``docker_compose``, ``profile_python``.

These need a real machine with ``docker`` and a Python on it, and they act beyond a scratch
directory, so they run on the user's **own paired desktop** through ``aura_hostd`` -- never inside
the gateway. The gateway has no ``docker`` binary and its socket proxy refuses ``docker exec``
(CLAUDE.md, ``tests/unit/test_sandbox_exec_surface.py``); the old ``DeploymentReleaseManager`` ran
``subprocess`` on the host and ``PerformanceProfiler`` ``eval``'d strings, neither acceptable here.

What this module does instead is *build a validated argv* and hand it to the same ``SandboxRunner``
that backs ``run_command``. The daemon's own policy (``aura_hostd/lib/src/command_policy.dart`` and
its approval queue) then applies as it does to any command. Three layers, then, in order: this
module refuses anything malformed (a tag or service starting with ``-`` is how an argument becomes
an option), ``ToolSpec`` forces a confirmation (SYSTEM + side-effecting, and deliberately **not**
``workspace_local`` -- ``docker`` reaches outside the workspace), and the daemon decides whether the
command runs.

A user without a paired desktop gets a plain refusal. Registered only when a host-agent registry is
wired (``tools/coding.py::build_sandbox_tools``).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..domain.errors import ToolExecutionError
from ..domain.tools import NETWORK_TAG, ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool
from .open.profile_ext import PROFILE_WRAPPER, compare as compare_profile, parse_profile

__all__ = ["build_desktop_ops_tools", "DESKTOP_OPS_TOOL_NAMES"]

_ENGINEER_CAPABILITY = "engineer_agent"
_IMAGE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-/:@]{0,199}")
_SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,99}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_COMPOSE_ACTIONS = ("up", "down", "ps", "config", "pull")
_OUTPUT_CAP = 8_000

#: Runs ``sys.argv[1]`` under cProfile + tracemalloc (``tools/open/profile_ext.py``). The snippet
#: travels as an argument, so nothing is written to the user's folder.
_PROFILE_WRAPPER = PROFILE_WRAPPER

_COMMON = dict(
    category=ToolCategory.DEVELOPMENT,
    sensitivity=ToolSensitivity.SYSTEM,
    side_effecting=True,
    required_capability=_ENGINEER_CAPABILITY,
    max_result_chars=20_000,
    # `docker build` output and a profiled program's output both echo whatever the project prints.
    tags=("untrusted_content",),
    # NOT workspace_local: these leave the workspace, so the full confirmation path applies.
)


def _relative_path(tool_name: str, label: str, value: str) -> str:
    """A path inside the workspace: not absolute, not a drive, not an option, no ``..``."""
    bad = (
        not value
        or not value.strip()
        or any(ord(ch) < 32 for ch in value)
        or value.startswith(("-", "/", "\\"))
        or _DRIVE.match(value)
        or ".." in re.split(r"[\\/]+", value)
    )
    if bad:
        raise ToolExecutionError(
            f"{label} must be a path inside the workspace (relative, no '..'), got {value!r}",
            tool=tool_name,
        )
    return value


def _format(result: Any) -> str:
    def cap(text: str) -> str:
        """Head and tail, not just the head: a build's error is the last thing it prints, so a
        head-only cut dropped exactly the lines the caller ran the command to see."""
        if len(text) <= _OUTPUT_CAP:
            return text
        head = _OUTPUT_CAP // 4
        tail = _OUTPUT_CAP - head
        return (
            f"{text[:head]}\n…[truncated] ({len(text) - head - tail} characters omitted from the "
            f"middle)…\n{text[-tail:]}"
        )

    return (
        f"exit_code={result.exit_code}\n"
        f"--- stdout ---\n{cap(result.stdout or '')}\n"
        f"--- stderr ---\n{cap(result.stderr or '')}"
    )


def _missing_executable(result: Any, interpreter: str) -> bool:
    """Did the *interpreter* fail to start, as opposed to the snippet failing or printing?

    Matching a bare "not found" anywhere in the output would re-run a snippet that merely printed
    or raised that phrase, doubling its side effects. So: the shell's own "command not found"
    codes (126/127), or a message that names this interpreter and says it is missing.
    """
    # 126/127: a POSIX shell's "cannot execute"/"not found". 9009: what Windows' cmd.exe returns for
    # a command it cannot find, which is also what the Microsoft Store's `python3` stub exits with.
    if result.exit_code in (126, 127, 9009):
        return True
    if result.exit_code == 0:
        return False
    text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    name = interpreter.lower()
    return any(
        phrase in text
        for phrase in (
            f"{name}: command not found",
            f"{name}: not found",
            f"'{name}' is not recognized",
            f"{name} is not recognized",
            f"no such file or directory: '{name}'",
            f"{name} was not found",  # the Windows Store stub: "Python was not found; run without ..."
        )
    )


def build_desktop_ops_tools(sandbox_runner: Any, sessions: Any, host_agent_registry: Any) -> list[Tool]:
    """The tools that only make sense on the user's own machine.

    ``sessions`` is the ``_SessionAdapter`` ``build_sandbox_tools`` already holds, so these run in
    the same workspace the agent's ``write_file``/``run_command`` calls used -- a Dockerfile it just
    wrote is the one that gets built.
    """

    async def require_desktop(tool_name: str, context: ToolContext) -> None:
        if await host_agent_registry.resolve(context.user_id) is None:
            raise ToolExecutionError(
                f"{tool_name} runs on your own machine and needs the AURA Desktop app connected. "
                "It is never run on the server.",
                tool=tool_name,
            )

    @tool(
        "docker_build",
        "Build a Docker image on the user's own machine (needs AURA Desktop connected), from a "
        "Dockerfile and context directory inside the workspace. Returns the exit code and the "
        "build output. The daemon may ask the user to approve the command.",
        param("image_tag", "string", "Image name and optional tag, e.g. 'myapp:dev'.", max_length=200),
        param("dockerfile", "string", "Dockerfile path inside the workspace (default Dockerfile).",
              required=False, default="Dockerfile", max_length=300),
        param("context_dir", "string", "Build context directory inside the workspace (default '.').",
              required=False, default=".", max_length=300),
        param("timeout_s", "number", "Max seconds for the build (default 600).", required=False,
              default=600, minimum=10, maximum=900),
        timeout_s=910.0,
        **{**_COMMON, "tags": ("untrusted_content", NETWORK_TAG)},  # may pull images
    )
    async def docker_build(
        image_tag: str,
        context: ToolContext,
        dockerfile: str = "Dockerfile",
        context_dir: str = ".",
        timeout_s: float = 600.0,
    ) -> str:
        name = "docker_build"
        if not _IMAGE_TAG.fullmatch(image_tag):
            raise ToolExecutionError(f"invalid image_tag {image_tag!r}", tool=name)
        argv = [
            "docker", "build", "-t", image_tag,
            "-f", _relative_path(name, "dockerfile", dockerfile),
            _relative_path(name, "context_dir", context_dir),
        ]
        await require_desktop(name, context)
        handle = await sessions.handle(context)
        return _format(await sandbox_runner.run(handle, argv, timeout_s=timeout_s))

    @tool(
        "docker_compose",
        "Run `docker compose` on the user's own machine (needs AURA Desktop connected) against a "
        "compose file inside the workspace. action: up (detached), down, ps, config (validate and "
        "print) or pull, optionally for named services. The daemon may ask the user to approve it.",
        param("action", "string", "The compose subcommand.", enum=_COMPOSE_ACTIONS),
        param("compose_file", "string", "Compose file inside the workspace "
              "(default docker-compose.yml).", required=False, default="docker-compose.yml",
              max_length=300),
        param("services", "array", "Service names to limit the action to.", required=False,
              item_type="string"),
        param("timeout_s", "number", "Max seconds (default 120).", required=False, default=120,
              minimum=5, maximum=600),
        timeout_s=610.0,
        **{**_COMMON, "tags": ("untrusted_content", NETWORK_TAG)},  # may pull images
    )
    async def docker_compose(
        action: str,
        context: ToolContext,
        compose_file: str = "docker-compose.yml",
        services: list | None = None,
        timeout_s: float = 120.0,
    ) -> str:
        name = "docker_compose"
        if action not in _COMPOSE_ACTIONS:
            raise ToolExecutionError(
                f"unsupported action {action!r}; allowed: {list(_COMPOSE_ACTIONS)}", tool=name
            )
        names = [str(s) for s in (services or [])]
        for service in names:
            if not _SERVICE.fullmatch(service):
                raise ToolExecutionError(f"invalid service name {service!r}", tool=name)
        argv = ["docker", "compose", "-f", _relative_path(name, "compose_file", compose_file), action]
        if action == "up":
            argv.append("-d")
        argv += names
        await require_desktop(name, context)
        handle = await sessions.handle(context)
        return _format(await sandbox_runner.run(handle, argv, timeout_s=timeout_s))

    @tool(
        "profile_python",
        "Run a Python snippet under cProfile and tracemalloc on the user's own machine (needs AURA "
        "Desktop connected). Returns the hottest functions plus wall time, peak memory and the top "
        "allocations; pass `baseline` (figures from an earlier run) to get a regression verdict. "
        "RUNS THE CODE with the user's permissions; nothing is written to their folder.",
        param("source_code", "string", "The Python code to profile.", max_length=20_000),
        param("limit", "integer", "How many of the hottest functions to list (default 15).", required=False,
              default=15, minimum=1, maximum=50),
        param("memory", "boolean", "Track memory with tracemalloc (default true; slows the run).",
              required=False, default=True),
        param("baseline", "object", "Earlier figures to compare with: any of wall_s, peak_kb, "
              "function_calls (as returned in `metrics`).", required=False),
        param("max_regression_pct", "number", "Largest acceptable rise over the baseline (default 25).",
              required=False, default=25, minimum=0, maximum=1000),
        param("max_peak_kb", "number", "Absolute peak-memory budget in KiB.", required=False, minimum=1),
        param("timeout_s", "number", "Max seconds (default 60).", required=False, default=60,
              minimum=1, maximum=300),
        timeout_s=310.0,
        **_COMMON,
    )
    async def profile_python(
        source_code: str,
        context: ToolContext,
        limit: int = 15,
        timeout_s: float = 60.0,
        memory: bool = True,
        baseline: dict | None = None,
        max_regression_pct: float = 25.0,
        max_peak_kb: float | None = None,
    ) -> str:
        name = "profile_python"
        if baseline is not None and (not isinstance(baseline, dict) or len(str(baseline)) > 2_000):
            raise ToolExecutionError("baseline must be a small object of earlier figures", tool=name)
        await require_desktop(name, context)
        handle = await sessions.handle(context)
        result = None
        extra = [] if memory else ["0"]
        for interpreter in ("python3", "python"):
            result = await sandbox_runner.run(
                handle, [interpreter, "-c", _PROFILE_WRAPPER, source_code, str(limit), *extra], timeout_s=timeout_s
            )
            if not _missing_executable(result, interpreter):
                break
        text = _format(result)
        metrics = parse_profile(result.stdout or "")
        if metrics is not None:
            # The figures line is machine output; the table above already carries the human view.
            text += "\n--- metrics ---\n" + json.dumps(metrics, ensure_ascii=False)
            if baseline is not None or max_peak_kb is not None:
                verdict = compare_profile(metrics, baseline or {}, max_regression_pct, max_peak_kb)
                text += "\n--- regression ---\n" + json.dumps(verdict, ensure_ascii=False)
        elif baseline is not None:
            text += "\n--- regression ---\n" + json.dumps(
                {"regressed": None, "note": "no figures were produced (the snippet may have exited early)"})
        return text

    return [docker_build, docker_compose, profile_python]


#: Names, for persona grants (``agents/registry.py``) and the tests that pin them.
DESKTOP_OPS_TOOL_NAMES: frozenset[str] = frozenset({"docker_build", "docker_compose", "profile_python"})
