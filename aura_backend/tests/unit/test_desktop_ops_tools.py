"""ADR-036 group C: ``docker_build`` / ``docker_compose`` / ``profile_python`` on the paired desktop.

The fake runner records the argv it is handed, which is the whole interface to ``aura_hostd``: what
matters here is exactly what would be sent to the user's machine, and that nothing malformed or
option-shaped can be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.ports.coding import SandboxResult
from aura_core.ports.runtime import ToolContext
from aura_core.tools.builtin import default_tools
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.desktop_ops import DESKTOP_OPS_TOOL_NAMES, build_desktop_ops_tools
from aura_core.tools.registry import ToolRegistry


@dataclass
class FakeRunner:
    calls: list[list[str]] = field(default_factory=list)
    results: list[SandboxResult] = field(default_factory=list)

    async def run(self, handle: Any, command, *, timeout_s: float) -> SandboxResult:
        self.calls.append(list(command))
        return self.results.pop(0) if self.results else SandboxResult(stdout="ok", stderr="", exit_code=0)


class FakeSessions:
    async def handle(self, context: ToolContext, repo: str | None = None) -> str:
        return "workspace-handle"


class FakeHostAgents:
    def __init__(self, paired: bool) -> None:
        self._paired = paired

    async def resolve(self, user_id: Any) -> Any:
        return object() if self._paired else None


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-1", deadline_s=60.0)


def build(runner: FakeRunner, paired: bool = True) -> dict:
    tools = build_desktop_ops_tools(runner, FakeSessions(), FakeHostAgents(paired))
    return {t.spec.name: t for t in tools}


class TestSpecs:
    def test_the_group_is_exactly_these_three(self) -> None:
        assert {t.spec.name for t in build(FakeRunner()).values()} == DESKTOP_OPS_TOOL_NAMES

    @pytest.mark.parametrize("name", sorted(DESKTOP_OPS_TOOL_NAMES))
    def test_confirmed_capability_gated_tainting_and_never_workspace_local(self, name) -> None:
        spec = build(FakeRunner())[name].spec
        assert spec.required_capability == "engineer_agent"
        assert spec.side_effecting is True
        assert spec.requires_confirmation is True
        assert spec.workspace_local is False  # docker reaches outside the workspace
        assert "untrusted_content" in spec.tags


class TestRegistration:
    def test_not_registered_by_default_nor_without_a_host_agent_registry(self) -> None:
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        runner = InMemorySandboxRunner()
        assert not DESKTOP_OPS_TOOL_NAMES & {t.spec.name for t in build_sandbox_tools(runner)}
        # The flag alone is not enough: with no desktop machinery there is nowhere to run them.
        assert not DESKTOP_OPS_TOOL_NAMES & {
            t.spec.name for t in build_sandbox_tools(runner, desktop_ops=True)
        }
        with_both = {
            t.spec.name
            for t in build_sandbox_tools(runner, desktop_ops=True, host_agent_registry=FakeHostAgents(True))
        }
        assert DESKTOP_OPS_TOOL_NAMES <= with_both

    def test_default_tools_threads_the_flag_through(self) -> None:
        from aura_core.adapters.coding_fakes import InMemorySandboxRunner

        runner = InMemorySandboxRunner()
        off = {t.spec.name for t in default_tools(sandbox_runner=runner, host_agent_registry=FakeHostAgents(True))}
        on = {
            t.spec.name
            for t in default_tools(
                sandbox_runner=runner, host_agent_registry=FakeHostAgents(True), enable_desktop_ops_tools=True
            )
        }
        assert not DESKTOP_OPS_TOOL_NAMES & off
        assert DESKTOP_OPS_TOOL_NAMES <= on

    async def test_the_registry_asks_for_confirmation_even_with_the_capability(self, ctx) -> None:
        registry = ToolRegistry()
        registry.register_all(list(build(FakeRunner()).values()))
        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("c1"), name="docker_build", arguments={"image_tag": "app:dev"}),
            ctx, capabilities=("engineer_agent",),
        )
        assert result.error_kind == "confirmation_required"


class TestNoDesktopNoRun:
    @pytest.mark.parametrize(
        ("name", "args"),
        [
            ("docker_build", {"image_tag": "app:dev"}),
            ("docker_compose", {"action": "ps"}),
            ("profile_python", {"source_code": "print(1)"}),
        ],
    )
    async def test_a_user_without_a_paired_desktop_is_refused_and_nothing_runs(self, ctx, name, args) -> None:
        runner = FakeRunner()
        with pytest.raises(ToolExecutionError, match="AURA Desktop"):
            await build(runner, paired=False)[name].execute(args, ctx)
        assert runner.calls == []


class TestDockerBuild:
    async def test_builds_the_expected_argv(self, ctx) -> None:
        runner = FakeRunner()
        out = await build(runner)["docker_build"].execute(
            {"image_tag": "myapp:dev", "dockerfile": "deploy/Dockerfile", "context_dir": "src"}, ctx
        )
        assert runner.calls == [["docker", "build", "-t", "myapp:dev", "-f", "deploy/Dockerfile", "src"]]
        assert out.startswith("exit_code=0")

    @pytest.mark.parametrize("tag", ["-rm", "--network=host", "app dev", "app;rm", "", "app\nx", "$(id)"])
    async def test_a_tag_cannot_become_an_option_or_a_shell_fragment(self, ctx, tag) -> None:
        runner = FakeRunner()
        with pytest.raises(ToolExecutionError, match="invalid image_tag"):
            await build(runner)["docker_build"].execute({"image_tag": tag}, ctx)
        assert runner.calls == []

    @pytest.mark.parametrize(
        "path", ["/etc/passwd", "C:\\Windows", "..\\x", "a/../../b", "-f", "\\\\server\\share", ""]
    )
    async def test_paths_must_stay_inside_the_workspace(self, ctx, path) -> None:
        runner = FakeRunner()
        for field_name in ("dockerfile", "context_dir"):
            with pytest.raises(ToolExecutionError, match="inside the workspace"):
                await build(runner)["docker_build"].execute({"image_tag": "a", field_name: path}, ctx)
        assert runner.calls == []

    async def test_a_failing_build_is_reported_not_raised(self, ctx) -> None:
        runner = FakeRunner(results=[SandboxResult(stdout="", stderr="no such file", exit_code=1)])
        out = await build(runner)["docker_build"].execute({"image_tag": "a"}, ctx)
        assert "exit_code=1" in out and "no such file" in out

    async def test_output_is_capped(self, ctx) -> None:
        runner = FakeRunner(results=[SandboxResult(stdout="x" * 50_000, stderr="", exit_code=0)])
        out = await build(runner)["docker_build"].execute({"image_tag": "a"}, ctx)
        assert len(out) < 9_000 and "[truncated]" in out


class TestDockerCompose:
    async def test_up_is_detached_and_services_are_appended(self, ctx) -> None:
        runner = FakeRunner()
        await build(runner)["docker_compose"].execute({"action": "up", "services": ["web", "db"]}, ctx)
        assert runner.calls == [["docker", "compose", "-f", "docker-compose.yml", "up", "-d", "web", "db"]]

    async def test_other_actions_take_no_extra_flags(self, ctx) -> None:
        runner = FakeRunner()
        await build(runner)["docker_compose"].execute(
            {"action": "config", "compose_file": "deploy/compose.yml"}, ctx
        )
        assert runner.calls == [["docker", "compose", "-f", "deploy/compose.yml", "config"]]

    @pytest.mark.parametrize("action", ["exec", "run", "rm", "up -d", "kill", ""])
    async def test_only_the_five_actions_are_allowed(self, ctx, action) -> None:
        runner = FakeRunner()
        with pytest.raises(ToolExecutionError, match="unsupported action"):
            await build(runner)["docker_compose"].execute({"action": action}, ctx)
        assert runner.calls == []

    @pytest.mark.parametrize("service", ["--rmi=all", "-v", "web db", "web;x", "$(x)", ""])
    async def test_a_service_name_cannot_become_an_option(self, ctx, service) -> None:
        runner = FakeRunner()
        with pytest.raises(ToolExecutionError, match="invalid service name"):
            await build(runner)["docker_compose"].execute({"action": "up", "services": [service]}, ctx)
        assert runner.calls == []


class TestProfilePython:
    async def test_the_snippet_travels_as_an_argument_and_no_file_is_written(self, ctx) -> None:
        runner = FakeRunner()
        await build(runner)["profile_python"].execute({"source_code": "sum(range(10))", "limit": 5}, ctx)
        argv = runner.calls[0]
        assert argv[0] == "python3" and argv[1] == "-c"
        assert argv[3:] == ["sum(range(10))", "5"]
        assert "cProfile" in argv[2]

    async def test_falls_back_to_python_when_python3_is_missing(self, ctx) -> None:
        runner = FakeRunner(results=[
            SandboxResult(stdout="", stderr="python3: command not found", exit_code=127),
            SandboxResult(stdout="  ncalls  tottime", stderr="", exit_code=0),
        ])
        out = await build(runner)["profile_python"].execute({"source_code": "1"}, ctx)
        assert [c[0] for c in runner.calls] == ["python3", "python"]
        assert "ncalls" in out

    async def test_a_snippet_that_prints_or_raises_not_found_is_never_run_twice(self, ctx) -> None:
        """Retrying on a bare "not found" would execute the user's code a second time."""
        for result in (
            SandboxResult(stdout="key not found", stderr="", exit_code=0),
            SandboxResult(stdout="", stderr="KeyError: 'x' not found", exit_code=1),
            SandboxResult(stdout="file not found\n", stderr="command not found: foo", exit_code=2),
        ):
            runner = FakeRunner(results=[result])
            await build(runner)["profile_python"].execute({"source_code": "print('key not found')"}, ctx)
            assert [c[0] for c in runner.calls] == ["python3"], result

    async def test_the_windows_not_recognized_message_does_trigger_the_fallback(self, ctx) -> None:
        runner = FakeRunner(results=[
            SandboxResult(stdout="", stderr="'python3' is not recognized as an internal or external command", exit_code=1),
            SandboxResult(stdout="ok", stderr="", exit_code=0),
        ])
        await build(runner)["profile_python"].execute({"source_code": "1"}, ctx)
        assert [c[0] for c in runner.calls] == ["python3", "python"]

    async def test_a_snippet_error_is_not_mistaken_for_a_missing_interpreter(self, ctx) -> None:
        runner = FakeRunner(results=[SandboxResult(stdout="", stderr="NameError: x", exit_code=1)])
        out = await build(runner)["profile_python"].execute({"source_code": "x"}, ctx)
        assert [c[0] for c in runner.calls] == ["python3"]
        assert "NameError" in out

    def test_the_snippet_is_size_bounded(self) -> None:
        code = next(p for p in build(FakeRunner())["profile_python"].spec.parameters if p.name == "source_code")
        assert code.max_length == 20_000
