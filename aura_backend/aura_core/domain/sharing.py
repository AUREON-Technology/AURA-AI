"""A chat a member shared with their team (SaaS P1-F1, spec §5.2). Stdlib only (ADR-001).

Team chats are private by default; sharing puts a *copy* of the member's own chat into the team
workspace, readable by every member. The copy comes from the member's app: the server's episodic
transcript is a short rolling window, not the conversation. Unsharing deletes the copy. The
member's own chat rows never become team-readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import ValidationError

__all__ = [
    "MAX_SHARED_CHARS",
    "MAX_SHARED_MESSAGES",
    "MAX_TITLE_CHARS",
    "SharedConversation",
    "SharedMessage",
    "parse_shared_messages",
]

MAX_SHARED_MESSAGES = 200
MAX_SHARED_CHARS = 200_000
MAX_TITLE_CHARS = 120
_ROLES = ("user", "assistant")


@dataclass(frozen=True, slots=True)
class SharedMessage:
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class SharedConversation:
    id: str
    workspace_id: str
    shared_by: str
    #: The sharer's own conversation id: sharing it again replaces this copy.
    source_id: str
    title: str
    messages: tuple[SharedMessage, ...]
    created_at: datetime


def parse_shared_messages(raw: Any) -> tuple[SharedMessage, ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValidationError("a shared chat needs at least one message")
    if len(raw) > MAX_SHARED_MESSAGES:
        raise ValidationError(f"a shared chat can hold at most {MAX_SHARED_MESSAGES} messages")
    messages, total = [], 0
    for item in raw:
        role = item.get("role") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        if role not in _ROLES or not isinstance(text, str):
            raise ValidationError("each message needs a role (user or assistant) and text")
        total += len(text)
        if total > MAX_SHARED_CHARS:
            raise ValidationError(f"a shared chat can hold at most {MAX_SHARED_CHARS} characters")
        messages.append(SharedMessage(role, text))
    return tuple(messages)
