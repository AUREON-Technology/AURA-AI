"""Sending email to customers (SaaS P1-F1). Stdlib only (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = ["EmailMessage", "EmailSender"]


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    #: What kind of email this is (``invite``, ``receipt`` ...): logged and audited instead of
    #: the content, which can carry a single-use token.
    tag: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` or raise. Callers decide whether a failure matters."""
        ...
