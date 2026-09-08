"""Helpers for writing tools.

:class:`FunctionTool` adapts a plain async callable into the :class:`~aura_core.ports.runtime.Tool`
protocol, so a tool is a function plus a declaration rather than a class hierarchy. The
declaration stays explicit -- deriving a schema from type hints reads well in a demo but produces
parameter descriptions the model cannot use, and description quality is what actually drives
correct tool selection.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from ..ports.runtime import ToolContext

__all__ = ["FunctionTool", "tool", "param"]

Handler = Callable[..., Awaitable[Any]]


def param(
    name: str,
    type: str,
    description: str,
    *,
    required: bool = True,
    enum: Sequence[str] | None = None,
    default: Any = None,
    minimum: float | None = None,
    maximum: float | None = None,
    max_length: int | None = None,
    item_type: str | None = None,
) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        type=type,
        description=description,
        required=required,
        enum=tuple(enum) if enum else None,
        default=default,
        minimum=minimum,
        maximum=maximum,
        max_length=max_length,
        item_type=item_type,
    )


class FunctionTool:
    """Wraps an async callable as a tool.

    The handler receives validated keyword arguments and, if it declares a ``context``
    parameter, the :class:`ToolContext`. Inspecting the signature once at construction keeps the
    per-call path free of reflection.
    """

    def __init__(self, spec: ToolSpec, handler: Handler) -> None:
        self._spec = spec
        self._handler = handler
        signature = inspect.signature(handler)
        self._wants_context = "context" in signature.parameters
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(
                f"tool {spec.name!r} handler must be async -- a synchronous handler would block "
                "the event loop for every concurrent request"
            )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> str:
        kwargs = dict(arguments)
        if self._wants_context:
            kwargs["context"] = context
        try:
            result = await self._handler(**kwargs)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                f"{self._spec.name} failed: {type(exc).__name__}",
                tool=self._spec.name,
                cause=exc,
            ) from exc
        return result if isinstance(result, str) else str(result)


def tool(
    name: str,
    description: str,
    *parameters: ParameterSpec,
    category: ToolCategory = ToolCategory.UTILITY,
    sensitivity: ToolSensitivity = ToolSensitivity.PUBLIC,
    side_effecting: bool = False,
    required_capability: str | None = None,
    timeout_s: float = 15.0,
    max_result_chars: int = 16_000,
    version: str = "1.0.0",
    tags: tuple[str, ...] = (),
    workspace_local: bool = False,
) -> Callable[[Handler], FunctionTool]:
    """Decorator form.

        @tool("add", "Add two numbers.", param("a", "number", "First"), param("b", "number", "Second"))
        async def add(a: float, b: float) -> str:
            return str(a + b)

    ``tags`` is free-form metadata read by the registry rather than the policy engine directly --
    e.g. ``"untrusted_content"`` marks a tool whose *output* is attacker-influenceable external
    text (registry.execute taints the calling ToolContext when it sees this tag; see
    tools/registry.py and tools/builtin.py's fetch_url).

    ``workspace_local`` is read by the policy engine and exempts a tool from confirmation: set it
    only where the effect really is confined to an ephemeral agent workspace. See
    ``ToolSpec.workspace_local`` and ADR-014.
    """

    def decorate(handler: Handler) -> FunctionTool:
        spec = ToolSpec(
            name=name,
            description=description,
            parameters=tuple(parameters),
            category=category,
            sensitivity=sensitivity,
            side_effecting=side_effecting,
            required_capability=required_capability,
            timeout_s=timeout_s,
            max_result_chars=max_result_chars,
            version=version,
            tags=tags,
            workspace_local=workspace_local,
        )
        return FunctionTool(spec, handler)

    return decorate
