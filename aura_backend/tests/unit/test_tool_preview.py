"""D6: ToolSpec.preview -> ToolRegistry.execute()'s confirmation branch -> ToolExecutionReport.diff_preview.

Exercises the generic mechanism with a synthetic tool rather than `github_commit_files` directly
(that tool's own diff-generation logic is covered in `test_engineer_tools.py`) -- this file is
about the plumbing: does the registry call the hook when it should, ignore it when it shouldn't,
and degrade gracefully when the hook itself fails.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from aura_core.ports.runtime import ToolContext
from aura_core.tools.base import FunctionTool
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0)


def _confirm_tool_with_preview(preview) -> FunctionTool:
    async def handler(text: str, context: ToolContext) -> str:
        return f"did: {text}"

    spec = ToolSpec(
        name="confirm_tool_with_preview",
        description="A side-effecting, confidential tool with a preview hook.",
        parameters=(ParameterSpec(name="text", type="string", description="text"),),
        category=ToolCategory.UTILITY,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        preview=preview,
    )
    return FunctionTool(spec, handler)


class TestToolPreview:
    async def test_diff_preview_is_populated_when_the_tool_needs_confirmation(self, ctx) -> None:
        async def preview(arguments, context):
            return f"--- proposed change for {arguments['text']} ---"

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(_confirm_tool_with_preview(preview))

        _, report = await registry.execute(
            ToolCall(id=ToolCallId("c1"), name="confirm_tool_with_preview", arguments={"text": "x"}),
            ctx,
        )
        assert report.needs_confirmation is True
        assert report.diff_preview == "--- proposed change for x ---"

    async def test_diff_preview_is_none_when_the_tool_has_no_preview_hook(self, ctx) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(_confirm_tool_with_preview(None))

        _, report = await registry.execute(
            ToolCall(id=ToolCallId("c1"), name="confirm_tool_with_preview", arguments={"text": "x"}),
            ctx,
        )
        assert report.needs_confirmation is True
        assert report.diff_preview is None

    async def test_a_failing_preview_hook_does_not_block_confirmation(self, ctx) -> None:
        async def broken_preview(arguments, context):
            raise RuntimeError("boom")

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(_confirm_tool_with_preview(broken_preview))

        result, report = await registry.execute(
            ToolCall(id=ToolCallId("c1"), name="confirm_tool_with_preview", arguments={"text": "x"}),
            ctx,
        )
        assert report.needs_confirmation is True
        assert report.diff_preview is None
        assert result.ok is False  # still the normal "needs confirmation" outcome, not an error

    async def test_diff_preview_is_never_populated_for_a_call_that_does_not_need_confirmation(self, ctx) -> None:
        called = False

        async def preview(arguments, context):
            nonlocal called
            called = True
            return "should never be seen"

        async def handler(text: str, context: ToolContext) -> str:
            return "ok"

        spec = ToolSpec(
            name="no_confirm_tool",
            description="Not confidential, not side-effecting -- never needs confirmation.",
            parameters=(ParameterSpec(name="text", type="string", description="text"),),
            category=ToolCategory.UTILITY,
            sensitivity=ToolSensitivity.PUBLIC,
            preview=preview,
        )
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(FunctionTool(spec, handler))

        _, report = await registry.execute(
            ToolCall(id=ToolCallId("c1"), name="no_confirm_tool", arguments={"text": "x"}), ctx,
        )
        assert report.needs_confirmation is False
        assert report.diff_preview is None
        assert called is False

    async def test_confirmed_replay_does_not_carry_a_diff_preview(self, ctx) -> None:
        """The second call (confirmed=True) actually executes -- diff_preview is a
        confirmation-limbo-only artifact (see ToolExecutionReport's own doc comment), not part of
        a normal successful-execution report."""

        async def preview(arguments, context):
            return "a diff"

        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(_confirm_tool_with_preview(preview))

        call = ToolCall(id=ToolCallId("c1"), name="confirm_tool_with_preview", arguments={"text": "x"})
        _, first = await registry.execute(call, ctx)
        assert first.diff_preview == "a diff"

        _, second = await registry.execute(call, ctx, confirmed=True)
        assert second.ok is True
        assert second.diff_preview is None
