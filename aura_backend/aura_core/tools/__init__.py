"""Tool engine: declarative specs, schema-validated dispatch, policy, sandboxing (SEC-07).

A tool is a declaration plus an implementation, kept separate so the model receives a schema and
never a callable. Between the model naming a tool and that tool running there is resolution,
argument validation, policy evaluation, and a timeout -- none of which exist in the legacy
if/elif dispatch.
"""

from .base import FunctionTool, param, tool
from .builtin import (
    build_file_tools,
    build_http_tool,
    build_knowledge_tools,
    build_memory_tools,
    build_weather_tool,
    calculator,
    current_datetime,
    default_tools,
)
from .policy import ToolPolicy
from .registry import ToolExecutionReport, ToolRegistry
from .schema import validate_arguments

__all__ = [
    "ToolRegistry",
    "ToolExecutionReport",
    "ToolPolicy",
    "validate_arguments",
    "FunctionTool",
    "tool",
    "param",
    "default_tools",
    "calculator",
    "current_datetime",
    "build_knowledge_tools",
    "build_memory_tools",
    "build_file_tools",
    "build_http_tool",
    "build_weather_tool",
]
