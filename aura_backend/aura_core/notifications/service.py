"""Renders and sends the customer email AURA owns (SaaS P1-F1, spec §5.5): team invites, payment
receipts, renewal reminders and past-due notices. Sign-in verification stays Firebase's.

Every method returns ``None`` and never raises: an email that cannot go must not fail the invite
or the payment that triggered it. A failure is logged and audited ``email.failed`` with the
message's ``tag`` -- never its body, which for an invite holds a single-use token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from ..ports.email import EmailMessage
from ..ports.runtime import AuditEvent

__all__ = ["Notifier"]

logger = logging.getLogger("aura.notifications")


def _egp(piastres: int) -> str:
    return f"EGP {piastres // 100}.{piastres % 100:02d}"


def _one_line(text: str, limit: int = 120) -> str:
    return " ".join(str(text).split())[:limit]


def _date(at: datetime | None) -> str:
    return at.strftime("%d %b %Y") if at else "-"


def _valid_address(to: str) -> bool:
    if not isinstance(to, str) or any(c in to for c in "\r\n,;"):
        return False
    _name, address = parseaddr(to)
    return address == to and "@" in address and "." in address.rsplit("@", 1)[-1]


@dataclass
class Notifier:
    sender: Any
    settings: Any
    audit: Any
    clock: Any

    async def invite(self, to: str, workspace_name: str, inviter_email: str | None, role: str, token: str) -> None:
        name = _one_line(workspace_name, 60)
        link = f"{self.settings.app_base_url.rstrip('/')}/invite?token={token}"
        by = f"{_one_line(inviter_email, 80)} has invited you" if inviter_email else "You have been invited"
        await self._send(to, f"You're invited to {name} on AURA", "invite", (
            f"{by} to join the team \"{name}\" on AURA as {_one_line(role, 20)}.\n\n"
            f"Accept the invitation:\n{link}\n\n"
            "The link works once and expires. If you did not expect this, ignore this email."
        ))

    async def receipt(self, to: str, workspace_name: str, plan_id: str, amount_piastres: int,
                      paid_at: datetime | None, period_end: datetime | None, payment_id: str) -> None:
        name = _one_line(workspace_name, 60)
        await self._send(to, f"Your AURA receipt: {_egp(amount_piastres)}", "receipt", (
            f"Thank you. We received your payment for {name}.\n\n"
            f"Plan: {_one_line(plan_id, 20)}\nAmount: {_egp(amount_piastres)}\n"
            f"Paid: {_date(paid_at)}\nPaid through: {_date(period_end)}\nReference: {payment_id}\n"
        ))

    async def renewal_due(self, to: str, workspace_name: str, amount_piastres: int,
                          reference_number: str | None, expires_at: datetime | None) -> None:
        name = _one_line(workspace_name, 60)
        await self._send(to, f"Renew {name} on AURA", "renewal_due", (
            f"Your AURA plan for {name} renews soon.\n\n"
            f"Pay {_egp(amount_piastres)} with Fawry reference {reference_number or '-'} at any Fawry "
            f"outlet, from a wallet, or in myFawry before {_date(expires_at)}.\n"
        ))

    async def past_due(self, to: str, workspace_name: str, grace_until: datetime | None) -> None:
        name = _one_line(workspace_name, 60)
        await self._send(to, f"Payment needed for {name} on AURA", "past_due", (
            f"We have not received the payment for {name}. You keep full access until "
            f"{_date(grace_until)}; after that the workspace returns to the Free plan. Nothing is "
            "deleted.\n"
        ))

    async def _send(self, to: str, subject: str, tag: str, text: str) -> None:
        subject = _one_line(subject, 150)
        try:
            if not _valid_address(to):
                raise ValueError("not a single valid address")
            await self.sender.send(EmailMessage(to=to, subject=subject, text=text, tag=tag))
        except Exception as exc:
            logger.warning("email could not be sent", extra={"tag": tag, "error": type(exc).__name__})
            try:
                await self.audit.record(AuditEvent(
                    action="email.failed", actor="notifier", outcome="error", at=self.clock.now(),
                    resource="email", detail={"tag": tag, "error": type(exc).__name__},
                ))
            except Exception:
                logger.warning("could not audit an email failure", exc_info=True)
