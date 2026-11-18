"""EmailSender adapters (SaaS P1-F1).

``SmtpEmailSender`` speaks plain SMTP with STARTTLS (stdlib ``smtplib``, run in a thread), which
every transactional provider the owner might choose offers (Resend, SES, Mailgun, Brevo ...), so
the choice of provider (spec §8 #4) is configuration, not code.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage as MimeMessage
from typing import Any, Callable

from ..ports.email import EmailMessage

__all__ = ["InMemoryEmailSender", "LogEmailSender", "SmtpEmailSender"]

logger = logging.getLogger("aura.email")


class InMemoryEmailSender:
    """Keeps every message in ``outbox``: tests read it."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)


class LogEmailSender:
    """For deployments without email: notes that a message would have gone. Never the body (an
    invite link is a credential) and only the recipient's domain."""

    async def send(self, message: EmailMessage) -> None:
        domain = message.to.rsplit("@", 1)[-1] if "@" in message.to else "?"
        logger.info("email not sent (email is off)", extra={"tag": message.tag, "to_domain": domain})


class SmtpEmailSender:
    def __init__(self, host: str, port: int, username: str, password: str, from_address: str, *,
                 starttls: bool = True, timeout_s: float = 15.0,
                 smtp_factory: Callable[..., Any] | None = None) -> None:
        self._host, self._port = host, port
        self._username, self._password = username, password
        self._from = from_address
        self._starttls = starttls
        self._timeout = timeout_s
        self._factory = smtp_factory or smtplib.SMTP

    def __repr__(self) -> str:  # never the password
        return f"SmtpEmailSender(host={self._host!r}, port={self._port})"

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        with self._factory(self._host, self._port, timeout=self._timeout) as smtp:
            if self._starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(mime)
