"""Tool declarations, policy, and invocation records.

A tool is described declaratively (:class:`ToolSpec`) and separated from its implementation, so
the model receives a schema and the runtime receives a callable -- never the same object. The
legacy code creates Python closures per request and dispatches model-chosen names through an
``if/elif`` chain with no validation, timeout, or authorisation (SEC-07).

Three properties carry security weight:

``side_effecting``      -- the tool changes state outside AURA. Gated in tainted contexts.
``required_capability`` -- an explicit grant the caller must hold. No grant, no dispatch.
``sensitivity``         -- what class of data the tool can reach; drives audit detail and
                           whether local-only inference is required for turns that use it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Mapping

from .identifiers import ToolCallId

__all__ = [
    "ToolSensitivity",
    "ToolCategory",
    "ParameterSpec",
    "ToolSpec",
    "ToolPolicyDecision",
    "ToolInvocation",
]


class ToolSensitivity(int, enum.Enum):
    PUBLIC = 0        # no user data (calculator, current time)
    PERSONAL = 1      # user's own data (tasks, calendar, finance)
    CONFIDENTIAL = 2  # credentials, third-party accounts (Drive, email, GitHub)
    SYSTEM = 3        # infrastructure (terminal, database, model management)


class ToolCategory(str, enum.Enum):
    UTILITY = "utility"
    KNOWLEDGE = "knowledge"
    MEMORY = "memory"
    PRODUCTIVITY = "productivity"
    COMMUNICATION = "communication"
    DEVELOPMENT = "development"
    DATA = "data"
    MEDIA = "media"
    SYSTEM = "system"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One tool parameter.

    Kept as an explicit value object rather than raw JSON Schema so specs can be validated,
    diffed across versions, and rendered into any provider's tool format
    (``tools/schema.py`` does the translation).
    """

    name: str
    type: str  # JSON Schema primitive: string | number | integer | boolean | array | object
    description: str
    required: bool = True
    enum: tuple[str, ...] | None = None
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    item_type: str | None = None

    _JSON_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object"})

    def __post_init__(self) -> None:
        if self.type not in self._JSON_TYPES:
            raise ValueError(f"unsupported parameter type {self.type!r}")
        if self.type == "array" and not self.item_type:
            raise ValueError(f"array parameter {self.name!r} must declare item_type")
        if not self.description:
            raise ValueError(f"parameter {self.name!r} must be described; the model reads this")

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.max_length is not None:
            schema["maxLength"] = self.max_length
        if self.item_type:
            schema["items"] = {"type": self.item_type}
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Everything about a tool except how it runs."""

    name: str
    description: str
    parameters: tuple[ParameterSpec, ...] = ()
    category: ToolCategory = ToolCategory.UTILITY
    sensitivity: ToolSensitivity = ToolSensitivity.PUBLIC
    side_effecting: bool = False
    requires_confirmation: bool = False
    required_capability: str | None = None
    timeout_s: float = 15.0
    max_concurrency: int = 4
    max_result_chars: int = 16_000
    version: str = "1.0.0"
    tags: tuple[str, ...] = ()

    _NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")

    def __post_init__(self) -> None:
        if not self.name or set(self.name) - self._NAME_OK:
            raise ValueError(
                f"tool name {self.name!r} must be lowercase snake_case "
                "(providers reject other characters)"
            )
        if not self.description:
            raise ValueError(f"tool {self.name!r} must have a description; the model selects on it")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError(f"tool {self.name!r} has duplicate parameter names")
        # A tool that changes the world and cannot be confirmed is a footgun; make the
        # combination explicit at declaration time rather than discovering it in production.
        if self.side_effecting and self.sensitivity >= ToolSensitivity.CONFIDENTIAL:
            object.__setattr__(self, "requires_confirmation", True)

    @property
    def required_parameters(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.parameters if p.required)

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema for the arguments object, used for validation and for provider payloads."""
        return {
            "type": "object",
            "properties": {p.name: p.json_schema() for p in self.parameters},
            "required": list(self.required_parameters),
            "additionalProperties": False,
        }

    def declaration(self) -> dict[str, Any]:
        """Vendor-neutral declaration; adapters reshape this per provider."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.json_schema(),
        }


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    """Why a tool call was allowed, blocked, or deferred to the user.

    ``requires_confirmation`` is not a refusal -- the gateway surfaces a confirmation prompt and
    the same call can be replayed with an approval token.
    """

    allowed: bool
    reason: str
    requires_confirmation: bool = False
    audit_fields: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str = "policy_ok", **audit: Any) -> "ToolPolicyDecision":
        return cls(True, reason, audit_fields=audit)

    @classmethod
    def confirm(cls, reason: str, **audit: Any) -> "ToolPolicyDecision":
        return cls(True, reason, requires_confirmation=True, audit_fields=audit)

    @classmethod
    def deny(cls, reason: str, **audit: Any) -> "ToolPolicyDecision":
        return cls(False, reason, audit_fields=audit)


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """The audit record of one tool execution.

    Written for every dispatch attempt, including denials, because "the model tried to read
    your Drive and policy stopped it" is exactly the event a reviewer needs to see.
    """

    call_id: ToolCallId
    tool: str
    version: str
    arguments_digest: str
    allowed: bool
    ok: bool
    duration_ms: float
    error_kind: str | None = None
    policy_reason: str = ""
    result_chars: int = 0
    tainted_context: bool = False
