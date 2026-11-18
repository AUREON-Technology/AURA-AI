"""Sending email to customers (SaaS P1-F1). Stdlib only (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

__all__ = ["EmailMessage", "EmailSender"]


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    #: Not in ``repr``: an invite's body carries a single-use token (review F1#9).
    text: str = field(repr=False)
    #: What kind of email this is (``invite``, ``receipt`` ...): logged and audited instead of
    #: the content, which can carry a single-use token.
    tag: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` or raise. Callers decide whether a failure matters."""
        ...
