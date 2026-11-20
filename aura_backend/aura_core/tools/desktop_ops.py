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

import re
from typing import Any

from ..domain.errors import ToolExecutionError
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool

__all__ = ["build_desktop_ops_tools", "DESKTOP_OPS_TOOL_NAMES"]

_ENGINEER_CAPABILITY = "engineer_agent"
_IMAGE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-/:@]{0,199}")
_SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,99}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_COMPOSE_ACTIONS = ("up", "down", "ps", "config", "pull")
_OUTPUT_CAP = 8_000

#: Runs ``sys.argv[1]`` under cProfile and prints the ``sys.argv[2]`` hottest functions. The snippet
#: travels as an argument, so nothing is written to the user's folder.
_PROFILE_WRAPPER = (
    "import cProfile, io, pstats, sys\n"
    "src, top = sys.argv[1], int(sys.argv[2])\n"
    "prof = cProfile.Profile()\n"
    "ns = {'__name__': '__main__'}\n"
    "prof.enable()\n"
    "try:\n"
    "    exec(compile(src, '<snippet>', 'exec'), ns)\n"
    "finally:\n"
    "    prof.disable()\n"
    "buf = io.StringIO()\n"
    "pstats.Stats(prof, stream=buf).sort_stats('tottime').print_stats(top)\n"
    "print(buf.getvalue())\n"
)

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
        or "\x00" in value
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
        return text if len(text) <= _OUTPUT_CAP else text[:_OUTPUT_CAP] + "\n…[truncated]"

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
    if result.exit_code in (126, 127):
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
        **_COMMON,
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
        **_COMMON,
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
        "Run a Python snippet under cProfile on the user's own machine (needs AURA Desktop "
        "connected) and return its hottest functions by own time. RUNS THE CODE with the user's "
        "permissions; nothing is written to their folder.",
        param("code", "string", "The Python code to run.", max_length=20_000),
        param("top_n", "integer", "How many functions to list (default 15).", required=False,
              default=15, minimum=1, maximum=50),
        param("timeout_s", "number", "Max seconds (default 60).", required=False, default=60,
              minimum=1, maximum=300),
        timeout_s=310.0,
        **_COMMON,
    )
    async def profile_python(
        code: str, context: ToolContext, top_n: int = 15, timeout_s: float = 60.0
    ) -> str:
        name = "profile_python"
        await require_desktop(name, context)
        handle = await sessions.handle(context)
        result = None
        for interpreter in ("python3", "python"):
            result = await sandbox_runner.run(
                handle, [interpreter, "-c", _PROFILE_WRAPPER, code, str(top_n)], timeout_s=timeout_s
            )
            if not _missing_executable(result, interpreter):
                break
        return _format(result)

    return [docker_build, docker_compose, profile_python]


#: Names, for persona grants (``agents/registry.py``) and the tests that pin them.
DESKTOP_OPS_TOOL_NAMES: frozenset[str] = frozenset({"docker_build", "docker_compose", "profile_python"})
