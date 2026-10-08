"""``FunctionTool.execute``'s catch-all must not discard the handler's own error message.

A prior version kept only ``type(exc).__name__``, which erased every actionable ``SandboxError``
string (e.g. "no desktop app is connected -- pair it") before it ever reached the model or the
user -- the same class of bug as the Qdrant ``DependencyUnavailableError`` masking a real 400,
except here it was generic to every non-``ToolExecutionError`` exception any tool could raise.
"""

from __future__ import annotations

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import FunctionTool

pytestmark = pytest.mark.asyncio


def _tool(handler) -> FunctionTool:
    spec = ToolSpec(
        name="probe_tool",
        description="Raises whatever the test handler raises.",
        parameters=(),
        category=ToolCategory.UTILITY,
        sensitivity=ToolSensitivity.PUBLIC,
    )
    return FunctionTool(spec, handler)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0)


async def test_it_preserves_the_original_exception_message(ctx: ToolContext) -> None:
    async def handler() -> str:
        raise RuntimeError("no desktop app is connected -- pair AURA Desktop and try again")

    with pytest.raises(ToolExecutionError) as excinfo:
        await _tool(handler).execute({}, ctx)

    assert "no desktop app is connected" in excinfo.value.message
    assert excinfo.value.cause is not None
    assert isinstance(excinfo.value.cause, RuntimeError)


async def test_it_falls_back_to_the_exception_type_name_when_the_message_is_empty(
    ctx: ToolContext,
) -> None:
    async def handler() -> str:
        raise RuntimeError()

    with pytest.raises(ToolExecutionError) as excinfo:
        await _tool(handler).execute({}, ctx)

    assert "RuntimeError" in excinfo.value.message


async def test_a_tool_execution_error_raised_by_the_handler_passes_through_unchanged(
    ctx: ToolContext,
) -> None:
    async def handler() -> str:
        raise ToolExecutionError("already precise", tool="probe_tool")

    with pytest.raises(ToolExecutionError) as excinfo:
        await _tool(handler).execute({}, ctx)

    assert excinfo.value.message == "already precise"
