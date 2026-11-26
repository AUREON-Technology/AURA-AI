"""``run_command`` holds its own limits, not only the registry's.

The registry validates arguments against the declared schema before a tool runs, but a tool is
also called directly (the engineer tests do, and so do wrappers), and a non-streaming runner
returns its whole output as one string. The tool therefore repeats the schema's bounds and caps
non-streaming output the way ``_drain_streaming_run`` caps streamed output
(``_RUN_COMMAND_STREAM_CAP_CHARS``), so the two paths behave the same (ADR-011, "Streaming").
"""

from __future__ import annotations

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId
from aura_core.domain.messages import ToolCall
from aura_core.ports.coding import SandboxResult
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import (
    _COMMAND_RESULT_BUDGET,
    _RUN_COMMAND_STREAM_CAP_CHARS,
    build_sandbox_tools,
)
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry


@pytest.fixture
def ctx(user) -> ToolContext:
    return ToolContext(user_id=user, correlation_id="corr-run-command-bounds", deadline_s=30.0)


def _run_command(runner):
    return {t.spec.name: t for t in build_sandbox_tools(runner)}["run_command"]


class _HugeOutputRunner(InMemorySandboxRunner):
    """A non-streaming runner whose command prints far more than the cap."""

    async def run(self, handle, command, *, timeout_s=60.0):  # type: ignore[override]
        return SandboxResult(
            stdout="x" * (_RUN_COMMAND_STREAM_CAP_CHARS + 5_000), stderr="", exit_code=0
        )


class TestBoundsHeldByTheToolItself:
    @pytest.mark.parametrize("command", ["", "   ", "\n\t"])
    async def test_a_blank_command_is_refused_not_run(self, ctx, command) -> None:
        with pytest.raises(ToolExecutionError, match="command"):
            await _run_command(InMemorySandboxRunner()).execute({"command": command}, ctx)

    async def test_an_oversized_command_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="2000"):
            await _run_command(InMemorySandboxRunner()).execute({"command": "x" * 2001}, ctx)

    @pytest.mark.parametrize("timeout_s", [0, -1, 601, float("nan")])
    async def test_an_out_of_range_timeout_is_refused(self, ctx, timeout_s) -> None:
        with pytest.raises(ToolExecutionError, match="timeout_s"):
            await _run_command(InMemorySandboxRunner()).execute(
                {"command": "echo a", "timeout_s": timeout_s}, ctx
            )

    async def test_the_boundary_values_still_run(self, ctx) -> None:
        tool = _run_command(InMemorySandboxRunner())
        assert "exit_code=0" in await tool.execute({"command": "echo a", "timeout_s": 1}, ctx)
        assert "exit_code=0" in await tool.execute({"command": "echo a", "timeout_s": 600}, ctx)
        at_the_limit = "echo a" + " " * (2000 - len("echo a"))
        assert len(at_the_limit) == 2000
        assert "exit_code=0" in await tool.execute({"command": at_the_limit}, ctx)

    async def test_a_valid_call_is_unchanged(self, ctx) -> None:
        out = await _run_command(InMemorySandboxRunner()).execute({"command": "echo hi"}, ctx)
        assert out == "exit_code=0\n--- stdout ---\nhi\n\n--- stderr ---\n"


class TestNonStreamingOutputIsCapped:
    async def test_output_beyond_the_cap_is_truncated_and_says_so(self, ctx) -> None:
        out = await _run_command(_HugeOutputRunner()).execute({"command": "yes"}, ctx)
        stdout = out.split("--- stdout ---\n", 1)[1].split("\n--- stderr ---", 1)[0]
        # The stream cap applies first; the result is then fitted to the result budget, keeping
        # both ends, so the truncation note at the end survives.
        assert len(stdout) < _COMMAND_RESULT_BUDGET + 200
        assert "characters of stdout omitted from the middle" in out
        assert "output truncated after" in out

    async def test_output_under_the_cap_is_left_alone(self, ctx) -> None:
        out = await _run_command(InMemorySandboxRunner()).execute({"command": "echo hi"}, ctx)
        assert "truncated" not in out


class TestRegistryBehaviourIsUnchanged:
    async def test_the_registry_still_rejects_bad_arguments_before_the_tool_runs(self, ctx) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(InMemorySandboxRunner()))
        result, report = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="run_command", arguments={"command": "x" * 2001}),
            ctx,
            capabilities=("engineer_agent",),
        )
        assert not result.ok
        assert report.policy_reason == "invalid_arguments"
