"""A user's own agent: a built-in persona, narrowed and given extra instructions.

The built-in agents (``aura_core/agents/registry.py``) are code: one shared ``AgentSpec`` each,
with a tool grant, a risk ceiling and a memory scope that were chosen deliberately. A *custom*
agent is not a new kind of agent. It is a built-in persona **plus** what its owner may add to it,
and nothing that widens it:

* ``instruction`` -- extra text appended under the base persona's prompt (never replacing it);
* ``allowed_tools`` -- a subset of the base persona's tools, or ``None`` for all of them;
* ``risk_ceiling`` -- a ceiling at or below the base's, or ``None`` to keep the base's.

Everything else (the prompt template, capabilities, memory scope, plan-loop budgets) is the base
persona's own, read at the moment of each turn, so narrowing a built-in later narrows every agent
built on it. ``aura_core.agents.custom.apply_custom_agent`` does that join and is the only place
a custom agent becomes an ``AgentSpec``.

Pure: stdlib only (``tests/architecture``). Validation lives here, once, for the API and the
stores to share.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .identifiers import UserId

__all__ = [
    "CustomAgent",
    "CustomAgentConflict",
    "CustomAgentError",
    "FORBIDDEN_BASE_AGENTS",
    "MAX_CUSTOM_AGENTS_PER_OWNER",
    "MAX_DESCRIPTION_CHARS",
    "MAX_INSTRUCTION_CHARS",
    "MAX_NAME_CHARS",
    "MAX_TOOLS",
    "RISK_NAMES",
    "clean_description",
    "clean_instruction",
    "clean_name",
    "clean_tools",
    "custom_spec_id",
    "is_custom_agent_id",
    "new_custom_agent_id",
]

MAX_CUSTOM_AGENTS_PER_OWNER = 20
MAX_NAME_CHARS = 60
MAX_DESCRIPTION_CHARS = 280
#: About a thousand tokens. The instruction sits in the system prompt of every turn the agent runs,
#: and counts against that turn's prompt cap, so it is bounded where a person can feel the cost.
MAX_INSTRUCTION_CHARS = 4000
MAX_TOOLS = 64

#: Built-in personas a custom agent can never be built on. ``engineer`` runs code in a sandbox or
#: on the person's desktop and is gated by consent, plans and a per-user allowance: a copy that
#: carries those grants under a name and a prompt of its owner's choosing is not something this
#: feature may mint.
FORBIDDEN_BASE_AGENTS = frozenset({"engineer"})

#: ``ToolSensitivity`` member names, lowest first. Kept here as strings so the domain does not
#: import the tool model; ``aura_core.agents.custom`` maps them back.
RISK_NAMES = ("PUBLIC", "PERSONAL", "CONFIDENTIAL", "SYSTEM")

_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:\-]{0,79}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: The id prefix a custom agent's resolved spec carries, so it can never equal a built-in id.
_SPEC_PREFIX = "custom:"


class CustomAgentError(ValueError):
    """A field of a custom agent is not acceptable; ``field`` names it for the API's 422."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


class CustomAgentConflict(Exception):
    """A store refused a write: the owner is at the limit (``reason="limit"``) or already has an
    agent of that name (``reason="name"``). Not a validation error: nothing about the input is
    malformed, so the API answers 409 rather than 422."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def new_custom_agent_id() -> str:
    return str(uuid.uuid4())


def custom_spec_id(agent_id: str) -> str:
    """The ``AgentSpec.id`` a custom agent resolves to (telemetry, run records, tool context)."""
    return f"{_SPEC_PREFIX}{agent_id}"


def is_custom_agent_id(value: str) -> bool:
    """Whether ``value`` is shaped like a custom agent's id (a UUID), as opposed to a built-in's."""
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False


def clean_name(value: object) -> str:
    if not isinstance(value, str):
        raise CustomAgentError("name", "a name is required")
    name = " ".join(value.split())
    if not name:
        raise CustomAgentError("name", "a name is required")
    if len(name) > MAX_NAME_CHARS:
        raise CustomAgentError("name", f"at most {MAX_NAME_CHARS} characters")
    if _CONTROL.search(name):
        raise CustomAgentError("name", "control characters are not allowed")
    return name


def clean_description(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CustomAgentError("description", "must be text")
    text = " ".join(value.split())
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise CustomAgentError("description", f"at most {MAX_DESCRIPTION_CHARS} characters")
    if _CONTROL.search(text):
        raise CustomAgentError("description", "control characters are not allowed")
    return text


def clean_instruction(value: object) -> str:
    """The extra instruction, trimmed. Newlines are kept (it is prose); control characters are not."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CustomAgentError("instruction", "must be text")
    text = value.strip()
    if len(text) > MAX_INSTRUCTION_CHARS:
        raise CustomAgentError("instruction", f"at most {MAX_INSTRUCTION_CHARS} characters")
    if _CONTROL.search(text):
        raise CustomAgentError("instruction", "control characters are not allowed")
    return text


def clean_tools(value: object) -> tuple[str, ...] | None:
    """A tool list as a sorted, de-duplicated tuple; ``None`` means "no narrowing"."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise CustomAgentError("allowed_tools", "must be a list of tool names, or null")
    names: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _TOOL_NAME.match(item):
            raise CustomAgentError("allowed_tools", "tool names are letters, digits and _ . : - only")
        names.add(item)
    if len(names) > MAX_TOOLS:
        raise CustomAgentError("allowed_tools", f"at most {MAX_TOOLS} tools")
    return tuple(sorted(names))


@dataclass(frozen=True, slots=True)
class CustomAgent:
    id: str
    owner_id: UserId
    name: str
    base_agent_id: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    instruction: str = ""
    #: ``None`` keeps all of the base persona's tools; a tuple narrows to that subset.
    allowed_tools: tuple[str, ...] | None = None
    #: ``None`` keeps the base persona's ceiling; a name lowers it (never raises it).
    risk_ceiling: str | None = None

    def __post_init__(self) -> None:
        if self.risk_ceiling is not None and self.risk_ceiling not in RISK_NAMES:
            raise ValueError(f"risk_ceiling must be one of {RISK_NAMES}")
        if self.base_agent_id in FORBIDDEN_BASE_AGENTS:
            raise ValueError(f"no custom agent can be built on {self.base_agent_id!r}")

    def as_dict(self, *, include_instruction: bool = True) -> dict[str, object]:
        body: dict[str, object] = {
            "id": self.id,
            "kind": "custom",
            "name": self.name,
            "description": self.description,
            "base_agent": self.base_agent_id,
            "allowed_tools": list(self.allowed_tools) if self.allowed_tools is not None else None,
            "risk_ceiling": self.risk_ceiling,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_instruction:
            body["instruction"] = self.instruction
        return body


def unknown_tools(requested: Iterable[str], known: Iterable[str]) -> list[str]:
    """The requested tool names that are not in ``known``, sorted."""
    known_set = set(known)
    return sorted(name for name in set(requested) if name not in known_set)
