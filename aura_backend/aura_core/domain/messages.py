"""Conversation primitives.

The central idea here is the **trust channel** (ADR-006). A message is not just role + text;
it carries where its content came from and therefore how much authority it has over the model's
behaviour. Retrieved documents and tool results are ``TrustChannel.DATA`` and are rendered into
fenced, provenance-tagged blocks -- never concatenated into the system instruction, which is
exactly the defect behind SEC-02.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .identifiers import ToolCallId
from .tokens import MESSAGE_OVERHEAD, REPLY_PRIMING, TokenCounter, estimate_tokens

__all__ = [
    "Role",
    "TrustChannel",
    "ToolCall",
    "ToolResult",
    "Message",
    "Conversation",
]


class Role(str, enum.Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

    def __str__(self) -> str:
        return self.value


class TrustChannel(str, enum.Enum):
    """How much authority this content has over model behaviour.

    ``SYSTEM``  -- operator-authored, versioned prompt templates. Instructions here are honoured.
    ``USER``    -- the end user's own words. Semi-trusted: may direct the assistant, may not
                   reconfigure it.
    ``DATA``    -- retrieved chunks, tool results, memories. **Never instructions.** Rendered
                   inside a nonce-delimited fence with provenance.
    """

    SYSTEM = "system"
    USER = "user"
    DATA = "data"

    def __str__(self) -> str:
        return self.value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to invoke a tool.

    ``arguments`` is the *raw* mapping produced by the model. It is untrusted and unvalidated at
    this point; ``tools/registry.py`` validates it against the tool's JSON schema before dispatch.
    The legacy code splats it straight into a Python callable (``services/agent.py:282``).
    """

    id: ToolCallId
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolCall.name must be non-empty")


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of a tool invocation.

    ``ok=False`` results are still sent back to the model -- an error the model can react to is
    more useful than a silent omission -- but they are counted in
    ``aura_tool_calls_total{outcome="error"}`` and never retried implicitly.
    """

    call_id: ToolCallId
    name: str
    ok: bool
    content: str
    duration_ms: float = 0.0
    error_kind: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def trust_channel(self) -> TrustChannel:
        # Tool output is third-party content. It is data, not instruction.
        return TrustChannel.DATA


@dataclass(frozen=True, slots=True)
class Message:
    """One turn element.

    Immutable: transformations (redaction, truncation, fencing) return new instances so the
    original is always available for audit and for the telemetry record.
    """

    role: Role
    content: str
    channel: TrustChannel = TrustChannel.USER
    name: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: ToolCallId | None = None
    created_at: datetime = field(default_factory=_utcnow)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role is Role.TOOL and self.tool_call_id is None:
            raise ValueError("tool messages must carry the tool_call_id they answer")
        if self.role is Role.SYSTEM and self.channel is not TrustChannel.SYSTEM:
            raise ValueError("system messages must use the SYSTEM trust channel")

    # -- constructors ------------------------------------------------------------------

    @classmethod
    def system(cls, content: str, **kw: Any) -> "Message":
        return cls(role=Role.SYSTEM, content=content, channel=TrustChannel.SYSTEM, **kw)

    @classmethod
    def user(cls, content: str, **kw: Any) -> "Message":
        return cls(role=Role.USER, content=content, channel=TrustChannel.USER, **kw)

    @classmethod
    def assistant(cls, content: str, **kw: Any) -> "Message":
        return cls(role=Role.ASSISTANT, content=content, channel=TrustChannel.USER, **kw)

    @classmethod
    def from_tool_result(cls, result: ToolResult) -> "Message":
        return cls(
            role=Role.TOOL,
            content=result.content,
            channel=TrustChannel.DATA,
            name=result.name,
            tool_call_id=result.call_id,
            metadata={"ok": result.ok, "error_kind": result.error_kind},
        )

    # -- transformations ---------------------------------------------------------------

    def with_content(self, content: str) -> "Message":
        return replace(self, content=content)

    def with_metadata(self, **extra: Any) -> "Message":
        merged = dict(self.metadata)
        merged.update(extra)
        return replace(self, metadata=merged)

    @property
    def is_trusted_instruction(self) -> bool:
        return self.channel is TrustChannel.SYSTEM

    def approx_tokens(self, counter: TokenCounter | None = None) -> int:
        """Estimated tokens for this message, framing included.

        ``counter`` is the routed provider's tokenizer (``Tokenizer.count``), passed in so the
        domain stays dependency-free; without one this is the generic heuristic of
        :mod:`aura_core.domain.tokens` -- the same estimator the context engine, the router and
        the quota use (KAN-104), not the flat ``len/4`` that undercounted Arabic and code.
        """
        count = counter(self.content) if counter is not None else estimate_tokens(self.content)
        return count + MESSAGE_OVERHEAD


@dataclass(frozen=True, slots=True)
class Conversation:
    """An ordered message sequence with convenience accessors.

    Not a mutable buffer: every operation returns a new ``Conversation``. The kernel builds one
    per turn from episodic memory + the incoming request, so there is no shared mutable history
    to corrupt under concurrency.
    """

    messages: tuple[Message, ...] = ()

    @classmethod
    def of(cls, messages: Iterable[Message]) -> "Conversation":
        return cls(tuple(messages))

    def append(self, *messages: Message) -> "Conversation":
        return Conversation(self.messages + tuple(messages))

    def extend(self, messages: Sequence[Message]) -> "Conversation":
        return Conversation(self.messages + tuple(messages))

    def __iter__(self):
        return iter(self.messages)

    def __len__(self) -> int:
        return len(self.messages)

    def __bool__(self) -> bool:
        return bool(self.messages)

    @property
    def last_user_message(self) -> Message | None:
        for message in reversed(self.messages):
            if message.role is Role.USER:
                return message
        return None

    @property
    def system_messages(self) -> tuple[Message, ...]:
        return tuple(m for m in self.messages if m.role is Role.SYSTEM)

    def without_system(self) -> "Conversation":
        return Conversation(tuple(m for m in self.messages if m.role is not Role.SYSTEM))

    def approx_tokens(self, counter: TokenCounter | None = None) -> int:
        """Estimated prompt tokens: every message plus the reply priming, counted exactly as
        ``Tokenizer.count_messages`` counts them, so the two figures agree for one counter."""
        if not self.messages:
            return 0
        return sum(m.approx_tokens(counter) for m in self.messages) + REPLY_PRIMING

    def tail(self, n: int) -> "Conversation":
        return Conversation(self.messages[-n:] if n > 0 else ())
