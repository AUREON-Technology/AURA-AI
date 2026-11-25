"""A person's standing preferences for how their chats are answered.

These are *defaults*: a chat request that names a value wins over them, and a field left ``None``
means "no preference" (the server's own default applies). They choose among what the router may
already use -- a routing policy, local-only, a temperature, an output size -- and never a specific
model or provider: which model a piece of content may reach is decided by its data classification
(ADR-030), not by a preference.

Stdlib only (ADR-001).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import ValidationError

__all__ = [
    "MAX_OUTPUT_TOKENS", "MIN_OUTPUT_TOKENS", "ROUTING_POLICIES", "SERVER_DEFAULTS", "ModelSettings",
]

#: ``RoutingPolicy``'s values, spelled out so this module stays stdlib-only.
ROUTING_POLICIES = ("local_first", "cheapest", "fastest", "quality_first", "balanced")
MIN_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS = 16, 8192
MIN_TEMPERATURE, MAX_TEMPERATURE = 0.0, 2.0

#: What applies when neither the request nor the person says anything (``POST /chat``'s own defaults).
SERVER_DEFAULTS: dict[str, Any] = {
    "routing_policy": None, "require_local": False, "temperature": 0.3, "max_output_tokens": 1024,
}


@dataclass(frozen=True, slots=True)
class ModelSettings:
    routing_policy: str | None = None
    require_local: bool | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.routing_policy is not None and self.routing_policy not in ROUTING_POLICIES:
            raise ValidationError(
                f"routing_policy must be one of {', '.join(ROUTING_POLICIES)}", details={"field": "routing_policy"},
            )
        if self.temperature is not None and not MIN_TEMPERATURE <= self.temperature <= MAX_TEMPERATURE:
            raise ValidationError(
                f"temperature must be between {MIN_TEMPERATURE} and {MAX_TEMPERATURE}", details={"field": "temperature"},
            )
        if self.max_output_tokens is not None and not MIN_OUTPUT_TOKENS <= self.max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ValidationError(
                f"max_output_tokens must be between {MIN_OUTPUT_TOKENS} and {MAX_OUTPUT_TOKENS}",
                details={"field": "max_output_tokens"},
            )

    @property
    def is_empty(self) -> bool:
        return (self.routing_policy, self.require_local, self.temperature, self.max_output_tokens) == (None,) * 4

    def as_dict(self) -> dict[str, Any]:
        return {
            "routing_policy": self.routing_policy,
            "require_local": self.require_local,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
